"""A foreign notice arrives. One entry point for every way in: a source fetch and a staged event in simulation.

In one transaction it stores the notice (a correction is a new revision; the same content again does nothing), matches it to
licences and open cases, creates the alert, and acts on the cases it touches. No agent is called here, so all of it
happens with every agent down. An explanation can come later and may only raise the severity.
"""
from dataclasses import dataclass
from typing import Optional

from pharmalign_contracts.server import models as m
from sqlalchemy import func, select

from ..context import Ctx
from ..domain import rules
from ..problems import ApiError
from ..stores import STORES
from ..tables import ROW
from . import alerts
from .logic import BURST, Hit, change_type_of, content_hash, find_hits, floor_for, is_retraction, notice_key, raised, severities_of, site_of

NOTICES = STORES["foreign_notice"]
REVISIONS = STORES["foreign_notice_revision"]
MATCHES = STORES["watch_match"]
INCIDENTS = STORES["watch_incident"]
CASES = STORES["case"]
DECISIONS = STORES["decision"]
CHECKS = STORES["check_result"]
FOREIGN_STATUS_CHECK = "WATCH-01"
SYSTEM = {"type": "system", "id": "watch"}
EXPLAIN_JOB = "watch.explain"
EXPLAIN_DELAY_MS = 2200


@dataclass
class Ingested:
    notice: m.NoticeDetail
    state: str  # new, revised or duplicate
    new_matches: list[m.WatchMatch]


async def _pack(ctx: Ctx) -> Optional[m.PackDocument]:
    workspace = await STORES["workspace"].get(ctx, ctx.tenant.id)
    pack = await STORES["pack_version"].get(ctx, workspace.active_pack_version_id) if workspace and workspace.active_pack_version_id else None
    return pack.document if pack else None


async def _hits(ctx: Ctx, regulator: str, title: str, text: str) -> list[Hit]:
    open_cases = [c for c in await CASES.list(ctx) if c.status != "closed"]
    return find_hits(
        regulator, title, text, await STORES["medicine"].list(ctx), await STORES["licence"].list(ctx), open_cases, await STORES["reliance_basis"].list(ctx),
    )


async def ingest(ctx: Ctx, staged: m.StagedNotice, source_id: Optional[str] = None) -> Ingested:
    key = notice_key(staged.regulator, staged.title, staged.reference)
    await ctx.db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(f"notice:{ctx.tenant.id}:{key}", 0))))
    found = await NOTICES.list(ctx, ROW["foreign_notice"].notice_key == key)
    digest = content_hash(staged.text)
    old = found[0] if found else None
    if old is not None:
        old = await NOTICES.require(ctx, old.id, for_update=True)
        if old.revisions[-1].content_hash == digest:
            return Ingested(old, "duplicate", [])

    pack = await _pack(ctx)
    options = severities_of(pack)
    change_type = change_type_of(f"{staged.title} {staged.text}")
    floor = floor_for(pack, change_type)
    source = await STORES["watch_source"].get(ctx, source_id) if source_id else None
    if source and source.severity_floor:
        floor = raised(options, floor, source.severity_floor)  # a source can raise the pack's floor, never lower it
    number = old.latest_revision + 1 if old else 1
    notice_id = old.id if old else ctx.id("n")
    revision = m.NoticeRevision(
        revision=number, fetched_at=ctx.now(), text_anchor=f"{notice_id}:r{number}:c1", content_hash=digest, is_retraction=is_retraction(staged.text),
        text=staged.text,
    )
    # A correction can't lower the severity the notice already had.
    severity = raised(options, old.severity, floor) if old else floor
    if old is None:
        notice = NOTICES.add(ctx, m.NoticeDetail(
            id=notice_id, regulator=staged.regulator, reference=staged.reference, title=staged.title, published_on=staged.published_on or ctx.now().date(), fetched_at=ctx.now(),
            latest_revision=1, retracted=revision.is_retraction, language=staged.language, severity=severity, match_count=0, unmatched=True,
            revisions=[revision], matches=[], assessment=None, incident_id=None,
        ), source_id=source_id)
    else:
        notice = await NOTICES.save(ctx, old.model_copy(update={
            "latest_revision": number, "fetched_at": ctx.now(), "retracted": old.retracted or revision.is_retraction, "severity": severity,
            "revisions": [*old.revisions, revision], "assessment": None,
        }))
    await ctx.db.flush()
    REVISIONS.add(ctx, revision, id=f"{notice_id}:r{number}", notice_id=notice_id)

    # Matching, in the same transaction as the notice: a crash can't leave a notice with no matches.
    already = {(x.target_type, x.target_id) for x in await MATCHES.list(ctx, ROW["watch_match"].notice_id == notice_id)}
    fresh = [h for h in await _hits(ctx, staged.regulator, staged.title, staged.text) if (h.target_type, h.target_id) not in already]
    new_matches = [
        MATCHES.add(ctx, m.WatchMatch(
            id=ctx.id("m"), notice_id=notice_id, target_type=h.target_type, target_id=h.target_id, match_basis=list(h.basis), match_confidence=h.confidence,
            status="open", decided_by=None, decided_at=None, reason=None,
        )) for h in fresh
    ]
    if not new_matches and old is None:
        # Stored and visible, with no target.
        new_matches = [MATCHES.add(ctx, m.WatchMatch(
            id=ctx.id("m"), notice_id=notice_id, target_type="none", target_id=None, match_basis=[], match_confidence="low", status="open",
            decided_by=None, decided_at=None, reason=None,
        ))]
    await ctx.db.flush()
    targeted = [x for x in new_matches if x.target_type != "none"]
    total = sum(1 for kind, _ in already if kind != "none") + len(targeted)
    notice = await NOTICES.save(ctx, notice.model_copy(update={"match_count": total, "unmatched": total == 0}))

    # Many licences from one notice are one incident.
    licences = sum(1 for x in targeted if x.target_type == "licence")
    if notice.incident_id:
        incident = await INCIDENTS.require(ctx, notice.incident_id, for_update=True)
        await INCIDENTS.save(ctx, incident.model_copy(update={"match_count": incident.match_count + licences, "severity": raised(options, incident.severity, severity)}))
    elif licences >= BURST:
        incident = INCIDENTS.add(ctx, m.WatchIncident(id=ctx.id("inc"), notice_id=notice_id, site=site_of(staged.title), match_count=licences, severity=severity, status="open"))
        notice = await NOTICES.save(ctx, notice.model_copy(update={"incident_id": incident.id}))

    people = await alerts.watch_officers(ctx)
    for match in [x for x in targeted if x.target_type == "case"]:
        people += await _act_on_case(ctx, notice, match, change_type)
    title = f"{staged.regulator}: {staged.title}" if total else f"Unmatched notice stored: {staged.title}"
    body = f"{total} match(es) wait for a person." if total else "No licence or open case matched. Stored so it can be found later."
    link = "/watch/matches" if total else f"/watch/notices/{notice_id}"
    if old is None:
        alerts.send(ctx, notice_id, people, severity, title, body, link)
    else:
        await alerts.update(ctx, notice_id, people, severity, f"Corrected (revision {number}): {title}", body, link, options)
    ctx.audit(
        "notice_stored" if old is None else "notice_revised", "foreign_notice", notice_id,
        after={"revision": number, "matches": len(targeted), "severity": severity, "change_type": change_type}, actor_type="system", actor_id="watch",
    )
    ctx.schedule(EXPLAIN_JOB, {"notice_id": notice_id, "change_type": change_type}, ctx.after(EXPLAIN_DELAY_MS))
    return Ingested(notice, "new" if old is None else "revised", new_matches)


async def _act_on_case(ctx: Ctx, notice: m.NoticeDetail, match: m.WatchMatch, change_type: str) -> list[str]:
    """What code does for a case the notice touches. Returns the people to tell as well."""
    from ..handlers.records import hold_for_notice  # imported late: the handlers import this module
    from ..handlers.support import people_in_view
    from ..pipeline import runner

    case = await CASES.require(ctx, match.target_id, "Case", for_update=True)
    decision = await DECISIONS.get(ctx, case.decision_id) if case.decision_id else None
    checks = await CHECKS.list(ctx, ROW["check_result"].case_id == case.id, ROW["check_result"].is_current.is_(True))
    gate = rules.notice_gate(case, decision, [c.check_id for c in checks if c.check_id == FOREIGN_STATUS_CHECK])
    tell = [*gate.tell_people, *(await people_in_view(ctx, gate.tell_role) if gate.tell_role else [])]
    if gate.hold_decision and decision is not None:
        await hold_for_notice(ctx, decision.id)
    for check_id in gate.rerun_results:
        try:
            await runner.rerun(ctx, case.id, [check_id], f"Foreign notice {notice.id}")
        except ApiError:
            pass  # already running or frozen: it reads the open match when it next runs
    ctx.timeline(case.id, "foreign_notice", f"Foreign notice matched: {notice.title}", SYSTEM, notice.id)
    await runner.rebrief(ctx, case.id)
    return tell


async def apply_assessment(ctx: Ctx, notice_id: str, change_type: str, summary: str, suggested_severity: str, evidence: list, run_id: str) -> m.NoticeDetail:
    """An explanation arrives, from an agent or from code. It adds a summary and can raise the severity, never lower it."""
    notice = await NOTICES.require(ctx, notice_id, "Notice", for_update=True)
    pack = await _pack(ctx)
    options = severities_of(pack)
    level = raised(options, notice.severity, suggested_severity)
    view = m.NoticeAssessmentView(change_type=change_type, summary=summary, suggested_severity=suggested_severity, evidence=evidence, run_id=run_id)
    notice = await NOTICES.save(ctx, notice.model_copy(update={"assessment": view, "severity": level}))
    await alerts.raise_severity(ctx, notice_id, level, options)
    return notice

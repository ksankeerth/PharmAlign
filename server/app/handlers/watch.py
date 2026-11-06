"""Foreign watch: sources and their health, notices with revisions, matches decided by a person, incidents and coverage.
Code alerts first (`app/watch/notices.py`); an explanation can come later."""
from datetime import timedelta

from pharmalign_contracts.common import Evidence
from pharmalign_contracts.server import models as m
from sqlalchemy import func, select

from ..context import Ctx
from ..domain.rules import UNREADABLE_SOURCE
from ..jobs import task
from ..ops import op
from ..paging import paginate
from ..pipeline import runner
from ..problems import ApiError, enforce_guards, guard, not_found, require_reason
from ..stores import STORES
from ..tables import ROW
from ..watch.notices import EXPLAIN_JOB, FOREIGN_STATUS_CHECK, INCIDENTS, MATCHES, NOTICES, REVISIONS, apply_assessment
from ..watch.sources import SOURCES  # noqa: F401  (importing binds the scheduler's "watch" tick)
from .support import CASES, RELIANCE, get_case

LICENCES = STORES["licence"]
CHECKS = STORES["check_result"]


async def summaries(ctx: Ctx, notices: list[m.NoticeDetail]) -> dict[str, int]:
    """Matches with a target, per notice. A notice with none is unmatched, and stays listed."""
    row = ROW["watch_match"]
    counted = (await ctx.db.execute(
        select(row.notice_id, func.count()).where(row.tenant_id == ctx.tenant.id, row.target_type != "none").group_by(row.notice_id)
    )).all()
    return dict(counted)


def summary_of(n: m.NoticeDetail, count: int) -> m.NoticeSummary:
    return m.NoticeSummary(
        id=n.id, regulator=n.regulator, reference=n.reference, title=n.title, published_on=n.published_on, fetched_at=n.fetched_at, latest_revision=n.latest_revision,
        retracted=n.retracted, language=n.language, severity=n.severity, match_count=count, unmatched=count == 0,
    )


@op("GET", "/api/v1/watch/sources")
async def list_sources(req, ctx):
    return await SOURCES.page(ctx, req.query)


@op("POST", "/api/v1/watch/sources")
async def add_source(req, ctx):
    s = m.WatchSource(**req.body.model_dump(), id=ctx.id("src"), health="unchecked", last_success_at=None, last_error=None, items_last_30_days=0)
    SOURCES.add(ctx, s)
    ctx.audit("watch_source_added", "watch_source", s.id, after=s)
    return s


@op("POST", "/api/v1/watch/sources/{source_id}/fetch")
async def fetch_source(req, ctx):
    source_id = req.path.source_id
    if not await ctx.db.scalar(select(func.count()).select_from(ROW["watch_source"]).where(ROW["watch_source"].id == source_id, ROW["watch_source"].tenant_id == ctx.tenant.id)):
        not_found("Watch source", source_id)
    # The read runs in transactions of its own, so a slow source holds no lock here.
    counts = (await ctx.rt.fetcher.fetch(ctx.rt, ctx.tenant.id, [source_id])) or {}
    ctx.db.expire_all()
    s = await SOURCES.require(ctx, source_id, "Watch source")
    new, revised = counts.get(source_id, (0, 0))
    ctx.audit("watch_source_fetched", "watch_source", s.id, after={"health": s.health})
    return m.FetchResult(source_id=s.id, fetched_at=ctx.now(), health=s.health, new_notices=new, new_revisions=revised)


@op("GET", "/api/v1/watch/notices")
async def list_notices(req, ctx):
    q = req.query
    every = await NOTICES.list(ctx)
    counts = await summaries(ctx, every)
    found = [summary_of(n, counts.get(n.id, 0)) for n in every]
    found = [n for n in found if (q.unmatched is None or n.unmatched == q.unmatched) and (not q.severity or n.severity == q.severity)]
    found.sort(key=lambda n: n.fetched_at, reverse=True)
    return paginate(found, q)


@op("GET", "/api/v1/watch/notices/{notice_id}")
async def get_notice(req, ctx):
    n = await NOTICES.require(ctx, req.path.notice_id, "Notice")
    count = (await summaries(ctx, [n])).get(n.id, 0)
    matches = await MATCHES.list(ctx, ROW["watch_match"].notice_id == n.id)
    return m.NoticeDetail(**{**n.model_dump(), **summary_of(n, count).model_dump(), "matches": matches})


@op("GET", "/api/v1/watch/matches")
async def list_matches(req, ctx):
    row = ROW["watch_match"]
    return await MATCHES.page(ctx, req.query, *([row.status == req.query.status] if req.query.status else []))


async def _recheck_foreign_status(ctx: Ctx, case_id: str, why: str) -> None:
    """The foreign-status check reads the open matches, so it is run again whenever one is decided."""
    case = await get_case(ctx, case_id)
    row = ROW["check_result"]
    current = [c for c in await CHECKS.list(ctx, row.case_id == case_id, row.is_current.is_(True), row.check_id == FOREIGN_STATUS_CHECK) if c.status not in ("pass", "running")]
    if current and case.status not in runner.FROZEN:
        try:
            await runner.rerun(ctx, case_id, [FOREIGN_STATUS_CHECK], why)
        except ApiError:
            pass


@op("POST", "/api/v1/watch/matches/{match_id}/dismiss")
async def dismiss(req, ctx):
    match = await MATCHES.require(ctx, req.path.match_id, "Match", for_update=True)
    reason = require_reason(req.body.reason)
    enforce_guards([guard("match_open", match.status == "open", f"Already {match.status.replace('_', ' ')}.")], "Dismiss refused")
    done = await MATCHES.save(ctx, match.model_copy(update={"status": "dismissed", "decided_by": ctx.person_id, "decided_at": ctx.now(), "reason": reason}))
    case_id = done.target_id if done.target_type == "case" else None
    if case_id and await CASES.get(ctx, case_id):
        await _recheck_foreign_status(ctx, case_id, f"Notice {done.notice_id} dismissed by a person: {reason}")
        ctx.timeline(case_id, "match_dismissed", f"Foreign notice match dismissed: {reason}", {"type": "person", "id": ctx.person_id}, done.id)
        await runner.refresh_case(ctx, case_id)
        await runner.rebrief(ctx, case_id)
    ctx.audit("match_dismissed", "watch_match", done.id, case_id, before={"status": "open"}, after={"status": "dismissed"}, reason=reason)
    return done


@op("POST", "/api/v1/watch/matches/{match_id}/open-rereview")
async def open_rereview(req, ctx):
    match = await MATCHES.require(ctx, req.path.match_id, "Match", for_update=True)
    reason = require_reason(req.body.reason)
    enforce_guards([
        guard("match_open", match.status == "open", f"Already {match.status.replace('_', ' ')}."),
        guard("has_target", match.target_type in ("licence", "case"), "A match with no target can't open a re-review."),
    ], "Re-review refused")
    done = await MATCHES.save(ctx, match.model_copy(update={"status": "rereview_opened", "decided_by": ctx.person_id, "decided_at": ctx.now(), "reason": reason}))
    actor = {"type": "person", "id": ctx.person_id}
    case = await CASES.get(ctx, done.target_id) if done.target_type == "case" else None
    if case is None:
        licence = await LICENCES.require(ctx, done.target_id, "Licence")
        medicine_id, applicant = licence.medicine_id, licence.holder_party_id
    else:
        medicine_id, applicant = case.medicine_id, case.applicant_party_id
    # The re-review is a case of its own. The match names it, and the timeline and the audit entry do too.
    detail = await _open_rereview_case(ctx, medicine_id, applicant)
    done = await MATCHES.save(ctx, done.model_copy(update={"rereview_case_id": detail.id}))
    ctx.timeline(detail.id, "rereview_opened", f"Reliance re-review opened from notice {done.notice_id}: {reason}", actor, done.id)
    if case is not None:
        ctx.timeline(case.id, "rereview_opened", f"Re-review opened as {detail.id}: {reason}", actor, done.id)
        await _recheck_foreign_status(ctx, case.id, reason)
    ctx.audit("rereview_opened", "watch_match", done.id, detail.id, before={"status": "open"}, after={"status": "rereview_opened", "case_id": detail.id}, reason=reason)
    await runner.refresh_case(ctx, detail.id)
    return await get_case(ctx, detail.id)


async def _open_rereview_case(ctx: Ctx, medicine_id: str, applicant_party_id: str) -> m.CaseDetail:
    workspace = await STORES["workspace"].require(ctx, ctx.tenant.id, "Workspace")
    await ctx.db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended("case-number", 0))))
    # A case number is unique across countries, so the next one counts every country's cases.
    ids = (await ctx.db.scalars(select(ROW["case"].id))).all()
    numbers = [int(i[5:]) for i in ids if i[5:].isdigit() and int(i[5:]) < 2000]
    case_id = f"CASE-{max([1051, *numbers]) + 1}"
    detail = m.CaseDetail(
        id=case_id, case_type="reliance_rereview", route="rereview", status="intake", environment="simulation", medicine_id=medicine_id,
        applicant_party_id=applicant_party_id, sequence=1, assigned_to=ctx.person_id, blocking_checks_open=0, next_deadline=ctx.now() + timedelta(days=2),
        created_at=ctx.now(), pack_version_id=workspace.active_pack_version_id, kb_snapshot_id=workspace.active_kb_snapshot_id, current_pipeline_run_id=None,
        current_recommendation_version=None, decision_id=None, clock_running=True, days_used=0,
    )
    CASES.add(ctx, detail)
    await ctx.db.flush()
    await runner.start_pipeline(ctx, case_id, "Reliance re-review")
    return await get_case(ctx, case_id)


@op("GET", "/api/v1/watch/incidents")
async def list_incidents(req, ctx):
    return await INCIDENTS.page(ctx, req.query)


@op("GET", "/api/v1/watch/coverage")
async def coverage(req, ctx):
    licences = await LICENCES.list(ctx)
    row = ROW["reliance_basis"]
    linked = {b.target_id for b in await RELIANCE.list(ctx, row.target_type == "licence")}
    sources = await SOURCES.list(ctx)
    return m.WatchCoverage(
        licences_total=len(licences), licences_with_reliance_link=sum(1 for l in licences if l.id in linked),
        licences_without_link=[l.id for l in licences if l.id not in linked], sources_total=len(sources),
        unhealthy_sources=[s.id for s in sources if s.health in UNREADABLE_SOURCE],
    )


@task(EXPLAIN_JOB)
async def explain(payload: dict, ctx: Ctx) -> None:
    """The explanation, made by code here. An agent's can replace it later; both go through `apply_assessment`, which never lowers the severity."""
    n = await NOTICES.get(ctx, payload["notice_id"])
    if n is None or n.assessment:
        return
    kind = payload["change_type"]
    said = kind.replace("_", " ")
    summary = (
        f"{n.regulator} reports a {said}. {n.match_count} target(s) here may be affected; a person decides each match."
        if n.match_count else f"{n.regulator} reports a {said}. Nothing here matched."
    )
    await apply_assessment(ctx, n.id, kind, summary, n.severity, [Evidence(anchor=f"{n.id}:r{n.latest_revision}:c1", quote=n.title)], f"{n.id}-r{n.latest_revision}-a1")
    ctx.audit("notice_explained", "foreign_notice", n.id, after={"severity": n.severity}, actor_type="system", actor_id="watch")

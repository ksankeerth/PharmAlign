"""Audit: the filtered log, the chain check, and a signed case read back as it was when signed."""
from datetime import datetime
from typing import Optional

from pharmalign_contracts.server import models as m
from sqlalchemy import select

from .. import audit
from ..domain.fingerprints import CaseFacts, case_package_fingerprint, locale_sort_key
from ..ops import op
from ..paging import paginate_select
from ..problems import invalid, problem
from ..stores import STORES
from ..tables import ROW, AuditEventRow
from .support import CASES, CHECKS, DECISIONS, MATCHES, PROPOSALS, QUESTIONS, RECOMMENDATIONS, THREADS, TRACES, with_limit

RUNS = STORES["pipeline_run"]
NOTICES = STORES["foreign_notice"]
EVENT_FIELDS = tuple(m.AuditEvent.model_fields)


def event_of(row: AuditEventRow) -> m.AuditEvent:
    return m.AuditEvent(**{name: getattr(row, name) for name in EVENT_FIELDS})


def moment(q, name: str) -> Optional[datetime]:
    """A query time, which must carry a zone."""
    raw = q.get(name)
    if not raw:
        return None
    try:
        when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        when = None
    if when is None or when.tzinfo is None:
        invalid(f"{name} must be a date and time with a zone, such as 2026-11-01T09:00:00Z.")
    return when


@op("GET", "/api/v1/audit-events")
async def list_audit_events(req, ctx):
    q = req.query
    row = AuditEventRow
    filters = [row.tenant_id == ctx.tenant.id]
    for name in ("case_id", "actor_id", "action", "object_id"):
        if q.get(name):
            filters.append(getattr(row, name) == q[name])
    start, end = moment(q, "from"), moment(q, "to")
    if start:
        filters.append(row.at >= start)
    if end:
        filters.append(row.at < end)
    return await paginate_select(ctx.db, select(row).where(*filters).order_by(row.seq.desc()), with_limit(q, 100), map=event_of)


@op("GET", "/api/v1/audit/verify")
async def verify_audit(req, ctx):
    return await audit.verify(ctx.db, ctx.tenant.id)


# ------------------------------------------------------------------ reconstruction


def signing_time(decision: m.Decision):
    return min(s.signed_at for s in decision.signers)


async def as_signed(ctx, case: m.CaseDetail, decision: m.Decision) -> CaseFacts:
    """The records a signed decision rested on, picked from what is stored: the check results that were newest when it was signed,
    the recommendation version it names, and the foreign matches open then. Nothing is read from the workspace's current pack or library."""
    signed_at = signing_time(decision)
    by_check: dict[str, m.CheckDetail] = {}
    for c in await CHECKS.list(ctx, ROW["check_result"].case_id == case.id):
        if c.updated_at <= signed_at:
            best = by_check.get(c.check_id)
            if best is None or (c.generation, c.attempt, c.updated_at) > (best.generation, best.attempt, best.updated_at):
                by_check[c.check_id] = c
    checks = [c.model_copy(update={"is_current": True}) for c in sorted(by_check.values(), key=lambda c: locale_sort_key(c.check_id))]
    versions = await RECOMMENDATIONS.list(ctx, ROW["recommendation_version"].case_id == case.id)
    recommendations = [r.model_copy(update={"is_current": r.version == decision.recommendation_version}) for r in versions if r.created_at <= signed_at]
    fetched = {n.id: n.fetched_at for n in await NOTICES.list(ctx)}
    matches = []
    for x in await MATCHES.list(ctx, ROW["watch_match"].target_type == "case", ROW["watch_match"].target_id == case.id):
        arrived = fetched.get(x.notice_id)
        if arrived is not None and arrived <= signed_at and (x.status == "open" or (x.decided_at and x.decided_at > signed_at)):
            matches.append(x.model_copy(update={"status": "open"}))
    return CaseFacts(case_id=case.id, case=case, checks=checks, recommendations=recommendations, matches=matches)


async def reconstruct(ctx, case_id: str) -> tuple[m.CaseReconstruction, Optional[str]]:
    """The case and what it rested on, with the fingerprint those records give. An unsigned case is read as it stands, with no fingerprint."""
    case = await CASES.require(ctx, case_id, "Case")
    decision = await DECISIONS.require(ctx, case.decision_id, "Decision") if case.decision_id else None
    runs = await RUNS.list(ctx, ROW["pipeline_run"].case_id == case.id, order_by=ROW["pipeline_run"].started_at)
    threads = await THREADS.list(ctx, ROW["review_thread"].case_id == case.id, order_by=ROW["review_thread"].created_at)
    if decision:
        facts = await as_signed(ctx, case, decision)
        checks, recommendations = list(facts.checks), list(facts.recommendations)
        fingerprint = case_package_fingerprint(facts)
        signed_at = signing_time(decision)
        threads = [t for t in threads if t.created_at <= signed_at]
    else:
        checks = await CHECKS.list(ctx, ROW["check_result"].case_id == case.id, ROW["check_result"].is_current.is_(True), order_by=ROW["check_result"].id)
        recommendations = await RECOMMENDATIONS.list(ctx, ROW["recommendation_version"].case_id == case.id)
        fingerprint = None
    traces = [t for t in [await TRACES.get(ctx, c.id) for c in checks] if t]
    row = AuditEventRow
    events = (await ctx.db.scalars(select(row).where(row.tenant_id == ctx.tenant.id, row.case_id == case.id).order_by(row.seq))).all()
    return m.CaseReconstruction(
        case=case, pipeline_runs=runs, checks=checks, traces=traces, recommendations=sorted(recommendations, key=lambda r: r.version),
        threads=threads, questions=await QUESTIONS.list(ctx, ROW["question"].case_id == case.id),
        decision=decision, proposal=await PROPOSALS.get(ctx, decision.proposal_id) if decision and decision.proposal_id else None,
        audit_events=[event_of(e) for e in events], reconstructed_fingerprint=fingerprint,
    ), fingerprint


@op("GET", "/api/v1/cases/{case_id}/reconstruct")
async def reconstruct_case(req, ctx):
    reconstruction, fingerprint = await reconstruct(ctx, req.path.case_id)
    decision = reconstruction.decision
    if decision and fingerprint != decision.signed_payload_hash:
        problem(409, "reconstruction_differs", "The records no longer give the signed fingerprint",
                f"Decision {decision.id} was signed on {decision.signed_payload_hash}; the stored records give {fingerprint}.")
    return reconstruction

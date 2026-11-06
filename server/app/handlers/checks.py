"""Pipeline checks: results, traces, confirm, waive, re-check, facts, recommendations and spot checks.
A person's act re-opens the pipeline, which asks the Coordinator for a new brief."""
from pharmalign_contracts.server import models as m

from ..domain.fingerprints import locale_sort_key
from ..domain.guards import confirm_guards, waive_guards
from ..ops import op
from ..paging import paginate
from ..pipeline import runner
from ..problems import enforce_guards, guard, invalid, require_reason
from ..stores import STORES
from ..tables import ROW
from .support import CASES, CHECKS, RECOMMENDATIONS, THREADS, TRACES, add_assessor, as_result, facts_of, get_case, name_of, people_in_view, with_limit

STAGE_ORDER = ["safe_to_read", "complete", "route", "same_product", "knowledge", "review"]
FACTS = STORES["fact"]
SPOT_CHECKS = STORES["spot_check"]
WAIVERS = STORES["waiver"]
CONFIRMATIONS = STORES["confirmation"]


def _stage_rank(stage: str) -> int:
    return STAGE_ORDER.index(stage) if stage in STAGE_ORDER else len(STAGE_ORDER)


async def _check(ctx, check_result_id: str, for_update: bool = False) -> m.CheckDetail:
    return await CHECKS.require(ctx, check_result_id, "Check result", for_update=for_update)


@op("GET", "/api/v1/cases/{case_id}/checks")
async def list_checks(req, ctx):
    await get_case(ctx, req.path.case_id)
    q = req.query
    statuses = q.status.split(",") if q.status else None
    row = ROW["check_result"]
    where = [row.case_id == req.path.case_id, row.is_current.is_(q.history != "superseded")]
    if statuses:
        where.append(row.status.in_(statuses))
    if q.blocking is not None:
        where.append(row.blocking.is_(q.blocking))
    found = [(r.stage, CHECKS._load(r)) for r in (await ctx.db.scalars(CHECKS._select(ctx).where(*where))).all()]
    if q.history == "superseded":
        found.sort(key=lambda x: (locale_sort_key(x[1].check_id), -x[1].generation, -x[1].attempt))
    else:
        found.sort(key=lambda x: (_stage_rank(x[0]), locale_sort_key(x[1].check_id)))
    return paginate([as_result(d) for _, d in found], with_limit(q, 200))


@op("GET", "/api/v1/check-results/{check_result_id}")
async def get_check(req, ctx):
    return await _check(ctx, req.path.check_result_id)


@op("GET", "/api/v1/check-results/{check_result_id}/trace")
async def get_trace(req, ctx):
    d = await _check(ctx, req.path.check_result_id)
    saved = await TRACES.get(ctx, d.id)
    if saved:
        return saved
    case = await CASES.get(ctx, d.case_id)
    return m.CheckTrace(
        check_result_id=d.id, basis="rule_trace" if d.rule_outcome else "not_saved", rule_outcome=d.rule_outcome,
        pack_version_id=case.pack_version_id if case else "", kb_snapshot_id=case.kb_snapshot_id if case else "",
    )


@op("POST", "/api/v1/check-results/{check_result_id}/evidence-opened")
async def evidence_opened(req, ctx):
    d = await _check(ctx, req.path.check_result_id, for_update=True)
    if not d.evidence_opened_at:
        d = await CHECKS.save(ctx, d.model_copy(update={"evidence_opened_at": ctx.now()}))
        ctx.audit("evidence_opened", "check_result", d.id, d.case_id)
    return d


@op("POST", "/api/v1/check-results/{check_result_id}/confirm")
async def confirm(req, ctx):
    d = await _check(ctx, req.path.check_result_id, for_update=True)
    case = await get_case(ctx, d.case_id, for_update=True)
    enforce_guards(confirm_guards(await facts_of(ctx, case), d, req.body.fingerprint), "Confirmation refused", d.fingerprint)
    view = m.ConfirmationView(id=ctx.id("cf"), by=ctx.person_id, note=req.body.note, fingerprint=d.fingerprint, at=ctx.now(), valid=True)
    CONFIRMATIONS.add(ctx, view, check_result_id=d.id, case_id=d.case_id)
    moved = await CHECKS.save(ctx, d.model_copy(update={"status": "confirmed", "confirmation": view, "updated_at": ctx.now()}))
    await add_assessor(ctx, d.case_id, ctx.person_id)
    ctx.timeline(d.case_id, "check_confirmed", f"{await name_of(ctx, ctx.person_id)} confirmed {d.check_id}.", {"type": "person", "id": ctx.person_id}, d.id)
    ctx.audit("check_confirmed", "check_result", d.id, d.case_id, before={"status": d.status}, after={"status": moved.status, "fingerprint": d.fingerprint}, reason=req.body.note)
    await runner.refresh_case(ctx, d.case_id)
    await runner.rebrief(ctx, d.case_id)
    return moved


@op("POST", "/api/v1/check-results/{check_result_id}/waive")
async def waive(req, ctx):
    d = await _check(ctx, req.path.check_result_id, for_update=True)
    reason = require_reason(req.body.reason)
    case = await get_case(ctx, d.case_id, for_update=True)
    enforce_guards(waive_guards(await facts_of(ctx, case), d, ctx.view, req.body.fingerprint), "Waiver refused", d.fingerprint)
    view = m.WaiverView(id=ctx.id("wv"), by=ctx.person_id, reason=reason, fingerprint=d.fingerprint, at=ctx.now(), valid=True)
    WAIVERS.add(ctx, view, check_result_id=d.id, case_id=d.case_id)
    moved = await CHECKS.save(ctx, d.model_copy(update={"status": "waived", "waiver": view, "updated_at": ctx.now()}))
    ctx.timeline(d.case_id, "check_waived", f"{await name_of(ctx, ctx.person_id)} waived {d.check_id}: {reason}", {"type": "person", "id": ctx.person_id}, d.id)
    ctx.audit("check_waived", "check_result", d.id, d.case_id, before={"status": d.status}, after={"status": moved.status, "fingerprint": d.fingerprint}, reason=reason)
    await runner.refresh_case(ctx, d.case_id)
    await runner.rebrief(ctx, d.case_id)
    return moved


@op("POST", "/api/v1/check-results/{check_result_id}/recheck")
async def recheck(req, ctx):
    d = await _check(ctx, req.path.check_result_id, for_update=True)
    if not req.body.thread_id and not req.body.comment:
        invalid("A re-check needs a thread or a comment.")
    thread = await THREADS.require(ctx, req.body.thread_id, "Thread") if req.body.thread_id else None
    enforce_guards([
        guard("agent_check", d.method in ("agent", "typed") or d.status == "error", "Only agent and typed checks take a reviewer's comment."),
    ], "Re-check refused")
    comment = req.body.comment or (thread.comments[-1].text if thread and thread.comments else None)
    done = await runner.rerun(ctx, d.case_id, [d.check_id], comment)
    enforce_guards([guard("run_exists", bool(done), "This case has no pipeline run to re-run a check in.")], "Re-check refused")
    ctx.timeline(d.case_id, "recheck_requested", f"Re-check of {d.check_id} requested, with a reviewer's comment as context.", {"type": "person", "id": ctx.person_id}, d.id)
    ctx.audit("recheck_requested", "check_result", d.id, d.case_id, after={"attempt": done[0].attempt}, reason=comment)
    return as_result(done[0])


@op("GET", "/api/v1/cases/{case_id}/facts")
async def list_facts(req, ctx):
    await get_case(ctx, req.path.case_id)
    row = ROW["fact"]
    return await FACTS.page(ctx, with_limit(req.query, 200), row.case_id == req.path.case_id, order_by=row.key)


@op("GET", "/api/v1/cases/{case_id}/recommendations")
async def list_recommendations(req, ctx):
    await get_case(ctx, req.path.case_id)
    row = ROW["recommendation_version"]
    return await RECOMMENDATIONS.page(ctx, req.query, row.case_id == req.path.case_id, order_by=row.version.desc())


@op("GET", "/api/v1/spot-checks")
async def list_spot_checks(req, ctx):
    row = ROW["spot_check"]
    where = []
    if ctx.view != "auditor":
        where.append(row.assigned_to == ctx.person_id)
    if req.query.status:
        where.append(row.status == req.query.status)
    return await SPOT_CHECKS.page(ctx, req.query, *where)


@op("POST", "/api/v1/spot-checks/{spot_check_id}/complete")
async def complete_spot_check(req, ctx):
    sc = await SPOT_CHECKS.require(ctx, req.path.spot_check_id, "Spot check", for_update=True)
    enforce_guards([
        guard("assigned_to_you", sc.assigned_to == ctx.person_id, "This spot check is assigned to someone else."),
        guard("still_open", sc.status == "open", f"Already completed: {sc.status}."),
    ], "Spot check refused")
    done = await SPOT_CHECKS.save(ctx, sc.model_copy(update={"status": req.body.status, "note": req.body.note}))
    ctx.audit("spot_check_completed", "spot_check", sc.id, sc.case_id, before=sc, after=done, reason=done.note)
    if done.status == "disagree":
        for person_id in await people_in_view(ctx, "process_owner"):
            ctx.notify(person_id, "spot_check_disagreed", "review", f"Spot check disagreed on {sc.case_id}", body=done.note, link="/owner/gaps")
    return done

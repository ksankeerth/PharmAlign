"""Gaps: signals counted by code, the Gap Analyst's candidates, and a person's review.

A candidate always cites a signal. Accepting one opens a change in a pack draft for a person to approve. It never edits
a pack that is active. The grouping here is done by code, as the replay does; no model is called."""
from pharmalign_contracts.agents.run_agents import GapSignal
from pharmalign_contracts.server import models as m

from ..jobs import task
from ..ops import op
from ..problems import invalid, not_found, require_reason
from ..stores import STORES
from ..tables import ROW
from .packs import open_draft, rows_of, summary_of
from .support import people_in_view, reach

SIGNALS = STORES["gap_signal"]
GAPS = STORES["gap"]
ANALYSES = STORES["gap_analysis"]
FINISH_ANALYSIS = "gaps.finish_analysis"


def title_of(s: GapSignal) -> str:
    if s.kind == "repeated_waiver":
        return f"Repeated waivers: {s.detail}"
    return f"Missing source: {s.detail}" if s.kind == "empty_slot" else s.detail


def candidate_from(ctx, s: GapSignal) -> m.Gap:
    waiver = s.kind == "repeated_waiver"
    return m.Gap(
        id=ctx.id("gap"), workspace_id=ctx.tenant.id, title=title_of(s)[:200], kind="standards" if s.kind in ("empty_slot", "no_source") else "policy",
        status="candidate", signal_ids=[s.id], description=f"Grouped from {s.count} signal(s) counted by code: {s.detail}. A candidate only, for a person to review.",
        pipeline_node="policy" if waiver else "source", readiness_dimension=None,
        suggested_next_step="Consider changing the check, or record why waivers are expected." if waiver else "Find or adopt a source.",
    )


async def add_candidate(ctx, gap: m.Gap) -> m.Gap:
    """The one way a candidate is made. It must cite signals of this workspace."""
    if not gap.signal_ids:
        invalid("A gap candidate must cite at least one signal.")
    known = {s.id for s in await SIGNALS.list(ctx)}
    unknown = [i for i in gap.signal_ids if i not in known]
    if unknown:
        invalid(f"A gap candidate can only cite this workspace's signals. Unknown: {', '.join(unknown)}.")
    return GAPS.add(ctx, gap)


@op("GET", "/api/v1/workspaces/{workspace_id}/gap-signals")
async def list_gap_signals(req, ctx):
    wctx = await reach(ctx, req.path.workspace_id)
    return await SIGNALS.page(wctx, req.query)


@op("POST", "/api/v1/workspaces/{workspace_id}/gap-analyses")
async def start_analysis(req, ctx):
    wctx = await reach(ctx, req.path.workspace_id)
    known = {s.id for s in await SIGNALS.list(wctx)}
    ids = list(req.body.signal_ids) if req.body and req.body.signal_ids else sorted(known)
    unknown = [i for i in ids if i not in known]
    if unknown:
        invalid(f"Unknown signals: {', '.join(unknown)}.")
    a = m.GapAnalysis(id=ctx.id("ga"), status="running", started_at=ctx.now(), finished_at=None, candidates_created=0)
    ANALYSES.add(wctx, a)
    wctx.schedule(FINISH_ANALYSIS, {"analysis_id": a.id, "signal_ids": ids}, ctx.after(2600))
    wctx.audit(action="gap_analysis_started", object_type="gap_analysis", object_id=a.id, after={"signals": len(ids)})
    return a


@op("GET", "/api/v1/gap-analyses/{analysis_id}")
async def get_analysis(req, ctx):
    return await ANALYSES.require(ctx, req.path.analysis_id, "Gap analysis")


@op("GET", "/api/v1/workspaces/{workspace_id}/gaps")
async def list_gaps(req, ctx):
    wctx = await reach(ctx, req.path.workspace_id)
    row = ROW["gap"]
    where = [c for c in (row.status == req.query.status if req.query.status else None, row.kind == req.query.kind if req.query.kind else None) if c is not None]
    return await GAPS.page(wctx, req.query, *where)


async def propose_change(ctx, gap: m.Gap) -> tuple[str, str]:
    """Where an accepted gap is worked on: the workspace's open draft, or a new draft copied from the active pack.
    The draft is for a person to edit and a second person to approve; the active pack is not touched."""
    workspace = await STORES["workspace"].require(ctx, ctx.tenant.id, "Workspace")
    drafts = await rows_of(ctx, ctx.tenant.id, ROW["pack_version"].state.in_(("draft", "simulated")))
    row = drafts[0] if drafts else await open_draft(ctx, workspace)
    p = summary_of(row)
    ctx.audit(action="gap_change_proposed", object_type="gap", object_id=gap.id, after={"pack_version_id": p.id, "signal_ids": gap.signal_ids})
    for person in await people_in_view(ctx, "process_owner"):
        if person != ctx.person_id:
            ctx.notify(person, "gap_accepted", "info", f"Gap accepted: {gap.title}"[:200], body=f"Change it in pack draft v{p.version}, then approve it.", link=f"/owner/packs/{p.id}")
    return f"Change proposed in pack draft v{p.version}. A second Process Owner approves it before it is active.", p.id


@op("PATCH", "/api/v1/gaps/{gap_id}")
async def review_gap(req, ctx):
    gap = await GAPS.require(ctx, req.path.gap_id, "Gap", for_update=True)
    note = require_reason(req.body.review_note, "review note")
    before = {"status": gap.status}
    update = {"status": req.body.status, "review_note": note, "reviewed_by": ctx.person_id}
    if req.body.status == "accepted":
        update["suggested_next_step"], update["pack_version_id"] = await propose_change(ctx, gap)
    reviewed = await GAPS.save(ctx, gap.model_copy(update=update))
    ctx.audit(action="gap_reviewed", object_type="gap", object_id=gap.id, before=before, after={"status": reviewed.status}, reason=note)
    return reviewed


@task(FINISH_ANALYSIS)
async def finish_analysis(payload, ctx):
    a = await ANALYSES.get(ctx, payload["analysis_id"], for_update=True)
    if a is None or a.status != "running":
        return
    covered = {i for g in await GAPS.list(ctx) for i in g.signal_ids}
    wanted = set(payload["signal_ids"])
    fresh = [s for s in await SIGNALS.list(ctx) if s.id in wanted and s.id not in covered]
    for s in fresh:
        await add_candidate(ctx, candidate_from(ctx, s))
    await ANALYSES.save(ctx, a.model_copy(update={"status": "completed", "finished_at": ctx.now(), "candidates_created": len(fresh)}))
    ctx.audit(action="gap_analysis_completed", object_type="gap_analysis", object_id=a.id, after={"candidates_created": len(fresh)}, actor_type="agent", actor_id="gap-analyst")

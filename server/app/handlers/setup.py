"""The nine setup stages, readiness runs and sign-off. A stage's blockers are read from the state of the domain that owns them,
each time, so they cannot drift from what is real."""
from typing import Optional

from pharmalign_contracts.server import models as m
from sqlalchemy import select

from ..domain.guards import guard
from ..domain.pack_validation import validate_pack_document
from ..domain.setup import OPTIONAL_STAGES, SETUP_STAGES
from ..jobs import task
from ..ops import op
from ..pipeline.health import agent_health, agents_are_live
from ..problems import enforce_guards, not_found
from ..stores import STORES
from ..tables import ROW, PersonRow, RoleGrantRow
from .packs import known_documents, rows_of, summary_of
from .support import reach

STAGES = STORES["setup_stage"]
RUNS = STORES["readiness_run"]
FINISH_READINESS = "setup.finish_readiness"
CHECK_LABELS = {
    "library_slots": "Must-have slots filled",
    "pack_valid": "Pack validates",
    "retrieval": "Retrieval test",
    "watch_sources": "Watch sources healthy",
    "connection": "Government connection",
    "agents": "Agents healthy",
    "scenarios": "Scenarios meet expectations",
}


# ------------------------------------------------------------------ what blocks a stage

async def current_pack(ctx):
    """The active version, else the newest. Read as stored, since a draft may not pass the full model."""
    rows = await rows_of(ctx, ctx.tenant.id)
    return next((r for r in rows if r.state == "active"), rows[0] if rows else None)


async def pack_errors(ctx, row) -> list[m.PackIssue]:
    return [i for i in validate_pack_document(row.doc["document"], await known_documents(ctx)).issues if i.severity == "error"]


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


async def holders(ctx, role: str) -> int:
    today = ctx.now().date()
    stmt = (
        select(RoleGrantRow.id).join(PersonRow, PersonRow.id == RoleGrantRow.person_id)
        .where(PersonRow.tenant_id == ctx.tenant.id, RoleGrantRow.role == role, RoleGrantRow.valid_from <= today)
    )
    return sum(1 for _ in (await ctx.db.scalars(stmt)).all())


async def stage_blockers(ctx, stage: str) -> list[str]:
    """Everything that stops this stage being marked done. Empty means nothing does."""
    if stage == "library":
        slots = await STORES["document_slot"].list(ctx)
        open_must = [s for s in slots if s.importance == "must" and s.status != "filled"]
        if not slots:
            return ["No document slots are set up yet."]
        return [f"{plural(len(open_must), 'must-have slot')} not filled: {', '.join(s.label for s in open_must[:5])}."] if open_must else []
    if stage == "rules_and_workflows":
        row = await current_pack(ctx)
        if row is None:
            return ["No pack version exists yet."]
        errors = await pack_errors(ctx, row)
        p = summary_of(row)
        return [f"Pack {'draft ' if p.state in ('draft', 'simulated') else ''}v{p.version} has {plural(len(errors), 'validation error')}: {' '.join(e.message for e in errors)}"] if errors else []
    if stage == "watch_sources":
        sources = await STORES["watch_source"].list(ctx)
        if not sources:
            return ["No watch source is registered yet."]
        return [f"{s.regulator} source is unhealthy: {s.health.replace('_', ' ')}." for s in sources if s.health not in ("healthy", "manual")]
    if stage == "connections":
        conns = await STORES["connection"].list(ctx)
        if not conns:
            return ["No government connection is registered yet."]
        if any(c.status == "approved" for c in conns):
            return []
        passing = [c for c in conns if c.status == "passing"]
        if passing:
            return [f"{passing[0].name} passed conformance but is not approved yet."]
        return [f"No connection passes conformance yet: {conns[0].name} is {conns[0].status}."]
    if stage == "people_and_notifications":
        out = []
        if await holders(ctx, "decision_maker") == 0:
            out.append("No Decision Maker granted yet.")
        row = await current_pack(ctx)
        if row is not None and not row.doc["document"].get("final_escalation_holder"):
            out.append("Urgent alerts have no final escalation holder.")
        return out
    if stage == "agents_and_limits":
        return [f"The {h.agent} agent is down." for h in await agent_health(ctx) if h.status == "down"]
    if stage == "test_and_simulate":
        sims = await STORES["simulation"].list(ctx)
        done = [s for s in sims if s.status == "completed"]
        missed = [s for s in done if s.expected_met is False]
        if not done:
            return ["No simulation has run for this country yet."]
        return [f"{plural(len(missed), 'simulation')} did not meet the expected outcome."] if missed else []
    return []


async def stages_of(ctx) -> list[m.SetupStageStatus]:
    """The stages in order. A stage not yet done shows what blocks it now, not what blocked it when it was saved."""
    saved = {s.stage: s for s in await STAGES.list(ctx)}
    out = []
    for stage, done_when in SETUP_STAGES:
        s = saved.get(stage) or m.SetupStageStatus(stage=stage, status="not_started", done_when=done_when)
        out.append(s if s.status == "done" else s.model_copy(update={"blockers": [b[:500] for b in await stage_blockers(ctx, stage)]}))
    return out


@op("GET", "/api/v1/workspaces/{workspace_id}/setup")
async def get_setup(req, ctx):
    wctx = await reach(ctx, req.path.workspace_id)
    return m.SetupView(workspace_id=wctx.tenant.id, stages=await stages_of(wctx))


@op("POST", "/api/v1/workspaces/{workspace_id}/setup/stages/{stage}/complete")
async def complete_stage(req, ctx):
    wctx = await reach(ctx, req.path.workspace_id)
    saved = await STAGES.get(wctx, f"{wctx.tenant.id}:{req.path.stage}", for_update=True)
    if saved is None:
        not_found("Setup stage", req.path.stage)
    stages = await stages_of(wctx)
    stage = next(s for s in stages if s.stage == saved.stage)
    order = [s for s, _ in SETUP_STAGES].index(stage.stage)
    earlier_open = [s.stage for i, s in enumerate(stages) if i < order and s.status != "done" and s.stage not in OPTIONAL_STAGES]
    enforce_guards([
        *(guard("no_blockers", False, b) for b in stage.blockers),
        guard("not_already_done", saved.status != "done", "This stage is already done." if saved.status == "done" else None),
        guard("earlier_stages_done", stage.stage != "sign_off" or not earlier_open, f"Finish first: {', '.join(earlier_open)}." if earlier_open else None),
    ], "This stage can't be marked done")
    done = saved.model_copy(update={"status": "done", "blockers": [], "completed_by": ctx.person_id, "completed_at": ctx.now()})
    await STAGES.save(wctx, done)
    wctx.audit(action="setup_stage_completed", object_type="setup_stage", object_id=f"{wctx.tenant.id}:{done.stage}", before=saved, after=done, reason=req.body.note if req.body else None)
    return done


# ------------------------------------------------------------------ readiness

async def evaluate(ctx, previous: dict[str, m.ReadinessCheck]) -> list[m.ReadinessCheck]:
    """Each check, from the state as it is now. The retrieval check runs the core questions against the workspace's active snapshot."""
    def check(key, status, detail):
        return m.ReadinessCheck(key=key, label=CHECK_LABELS[key], status=status, detail=detail[:500] if detail else None)

    out = []
    slots = [s for s in await STORES["document_slot"].list(ctx) if s.importance == "must"]
    filled = sum(1 for s in slots if s.status == "filled")
    out.append(check("library_slots", "pass" if slots and filled == len(slots) else "fail", f"{filled} of {len(slots)} must-have slots filled."))
    row = await current_pack(ctx)
    errors = await pack_errors(ctx, row) if row is not None else []
    p = summary_of(row) if row is not None else None
    out.append(check("pack_valid", "pass" if p and not errors else "fail",
                     "No pack version exists." if p is None else f"Pack v{p.version} has {plural(len(errors), 'validation error')}."))
    out.append(check("retrieval", *await retrieval_result(ctx)))
    sources = await STORES["watch_source"].list(ctx)
    sick = [s for s in sources if s.health not in ("healthy", "manual")]
    out.append(check("watch_sources", "pass" if sources and not sick else "fail",
                     "No source is registered." if not sources else f"{len(sources) - len(sick)} of {len(sources)} sources are healthy."))
    approved = [c for c in await STORES["connection"].list(ctx) if c.status == "approved"]
    out.append(check("connection", ("mock" if approved[0].kind == "mock" else "pass") if approved else "fail",
                     f"{approved[0].name} is approved." if approved else "No approved connection."))
    agents = await agent_health(ctx)
    ok = sum(1 for a in agents if a.status == "ok")
    if agents:
        out.append(check("agents", "pass" if ok == len(agents) else "fail", f"{ok} of {len(agents)} agents report ok."))
    elif ctx.rt.settings.env == "simulation" and not agents_are_live(ctx.rt):
        out.append(check("agents", "mock", "No agent services run here, so the server answers from recordings."))
    else:
        out.append(check("agents", "fail", "No agent has reported."))
    scenarios = await STORES["scenario"].list(ctx)
    sims = await STORES["simulation"].list(ctx)
    met = {s.scenario_id for s in sims if s.status == "completed" and s.expected_met}
    out.append(check("scenarios", "pass" if scenarios and all(s.id in met for s in scenarios) else ("fail" if sims else "not_run"),
                     f"{len([s for s in scenarios if s.id in met])} of {len(scenarios)} scenarios met their expected outcome."))
    return out


async def retrieval_result(ctx) -> tuple[str, Optional[str]]:
    """(status, detail) of the core question set, run now through the same search the agents use."""
    from ..knowledge.embedding import EmbedderUnavailable, get_embedder
    from ..knowledge.retrieval import QUESTION_SETS, for_country, run_questions, verdict
    from ..knowledge.search import Scope

    w = await STORES["workspace"].require(ctx, ctx.tenant.id, "Workspace")
    if not w.active_kb_snapshot_id:
        return "not_run", "No knowledge snapshot is active, so there is nothing to search."
    try:
        embedder = get_embedder(ctx.rt.settings)
    except EmbedderUnavailable as e:
        return "fail", str(e)
    run = await run_questions(ctx.db, Scope(ctx.tenant.id, w.country_code, w.active_kb_snapshot_id, "requirement"), embedder,
                              for_country(QUESTION_SETS["core-questions"], await STORES["document_slot"].list(ctx)))
    return verdict(run, embedder.name)


async def latest_run(ctx):
    runs = await RUNS.list(ctx, order_by=ROW["readiness_run"].started_at.desc())
    return runs[0] if runs else None


@op("POST", "/api/v1/workspaces/{workspace_id}/readiness-runs")
async def start_readiness(req, ctx):
    wctx = await reach(ctx, req.path.workspace_id)
    run = m.ReadinessRun(
        id=ctx.id("rr"), workspace_id=wctx.tenant.id, status="running", all_passed=False,
        checks=[m.ReadinessCheck(key=k, label=label, status="not_run") for k, label in CHECK_LABELS.items()], started_at=ctx.now(),
    )
    RUNS.add(wctx, run)
    wctx.schedule(FINISH_READINESS, {"run_id": run.id}, ctx.after(2500))
    wctx.audit(action="readiness_started", object_type="readiness_run", object_id=run.id)
    return run


@op("GET", "/api/v1/workspaces/{workspace_id}/readiness")
async def get_readiness(req, ctx):
    wctx = await reach(ctx, req.path.workspace_id)
    run = await latest_run(wctx)
    if run is None:
        not_found("Readiness run for", wctx.tenant.id)
    return run


@task(FINISH_READINESS)
async def finish_readiness(payload, ctx):
    run = await RUNS.get(ctx, payload["run_id"], for_update=True)
    if run is None or run.status != "running":
        return
    runs = [r for r in await RUNS.list(ctx, order_by=ROW["readiness_run"].started_at.desc()) if r.id != run.id and r.status == "completed"]
    previous = {c.key: c for c in (runs[0].checks if runs else [])}
    checks = [c.model_copy(update={"proof_ref": f"proof-{c.key}-{run.id}" if c.status in ("pass", "mock") else None}) for c in await evaluate(ctx, previous)]
    done = run.model_copy(update={
        "checks": checks, "all_passed": all(c.status in ("pass", "mock") for c in checks), "status": "completed", "finished_at": ctx.now(),
    })
    await RUNS.save(ctx, done)
    ctx.audit(action="readiness_completed", object_type="readiness_run", object_id=run.id, after={"all_passed": done.all_passed}, actor_type="system", actor_id="readiness")


# ------------------------------------------------------------------ go-live

@op("POST", "/api/v1/workspaces/{workspace_id}/sign-off")
async def sign_off(req, ctx):
    wctx = await reach(ctx, req.path.workspace_id)
    b = req.body
    w = await STORES["workspace"].require(wctx, wctx.tenant.id, "Workspace", for_update=True)
    run = await latest_run(wctx)
    pack = await wctx.db.get(ROW["pack_version"], b.pack_version_id)
    pack = pack if pack is not None and pack.tenant_id == w.id else None
    snapshot = await STORES["kb_snapshot"].get(wctx, b.kb_snapshot_id)
    current = run is not None and run.id == b.readiness_run_id
    enforce_guards([
        guard("readiness_current", current, None if current else "Sign off against the latest readiness run."),
        guard("readiness_passed", bool(run and run.all_passed), None if run and run.all_passed else "Readiness checks have not all passed."),
        guard("pack_approved", bool(pack and pack.state in ("approved", "active")),
              f"Pack v{pack.version} is {pack.state}." if pack else "Unknown pack version."),
        guard("snapshot_exists", snapshot is not None, None if snapshot else "Unknown snapshot."),
    ], "Sign-off refused")
    so = m.SignOff(**b.model_dump(), id=ctx.id("so"), workspace_id=w.id, signed_by=ctx.person_id, signed_at=ctx.now())
    STORES["sign_off"].add(wctx, so)
    await STORES["workspace"].save(wctx, w.model_copy(update={"mode": b.target_mode, "active_kb_snapshot_id": b.kb_snapshot_id}))
    wctx.audit(action="workspace_signed_off", object_type="workspace", object_id=w.id, before={"mode": w.mode}, after={"mode": b.target_mode}, reason=b.reason)
    return so

"""Simulation: scenarios, runs, and staged events. A foreign notice event goes in by the same entry point as a real one."""
from pharmalign_contracts.server import models as m
from sqlalchemy import select

from ..domain.scenario_template import FOREIGN_SUSPENSION, scenario_key
from ..jobs import task
from ..ops import op
from ..paging import paginate
from ..problems import forbidden, invalid, not_found, problem
from ..stores import STORES
from ..tables import ROW
from ..watch.notices import ingest
from .support import with_limit

SCENARIOS = STORES["scenario"]
SIMULATIONS = STORES["simulation"]
COMPARISONS = STORES["simulation_comparison"]
FINISH = "simulations.finish"
# Said on every ungoverned run, so nobody reads its output as a product result.
UNGOVERNED = "Ungoverned run, simulation only. "
EFFECTS = {
    "company_reply": "Company reply arrived: the affected checks are marked stale and will re-run on the new evidence.",
    "model_outage": "Model outage: in-flight checks end as Error, not as a finding. A re-run succeeds when the model returns.",
    "agent_restart": "Agent restarted: the interrupted run is recorded as lost and a new attempt starts.",
    "server_restart": "Server restarted: background work resumed from saved state. Nothing was lost or repeated.",
    "gov_failure": "Government system failed: the record stays queued, then needs a read-back before it is trusted.",
}


@op("GET", "/api/v1/scenarios")
async def list_scenarios(req, ctx):
    return await SCENARIOS.page(ctx, req.query)


@op("GET", "/api/v1/simulations")
async def list_simulations(req, ctx):
    q = req.query
    row = ROW["simulation"]
    where = [c for c in (row.status == q.status if q.status else None, row.scenario_id == q.scenario_id if q.scenario_id else None) if c is not None]
    found = await SIMULATIONS.list(ctx, *where)
    found.sort(key=lambda s: s.started_at, reverse=True)
    return paginate(found, with_limit(q, 200))


@op("POST", "/api/v1/simulations/{simulation_id}/events")
async def deliver_event(req, ctx):
    sim = await SIMULATIONS.require(ctx, req.path.simulation_id, "Simulation", for_update=True)
    b = req.body
    if b.kind == "foreign_notice" and not b.notice:
        invalid("A foreign notice event needs a notice.")
    waiting = sim.status in ("waiting_for_event", "running")
    if b.kind == "foreign_notice" and not waiting:
        problem(409, "simulation_not_waiting", "This simulation is not waiting for an event", f"It is {sim.status.replace('_', ' ')}.")
    if sim.status in ("failed", "queued"):
        problem(409, "simulation_not_waiting", "This simulation can't take events now", f"It is {sim.status}.")
    if b.kind == "foreign_notice":
        source = next((s for s in await STORES["watch_source"].list(ctx) if s.kind == "staged" and s.regulator == b.notice.regulator), None)
        result = await ingest(ctx, b.notice, source.id if source else None)
        n = result.notice
        sim = sim.model_copy(update={
            "outcome": f"Delivered notice {n.id}: {n.match_count} match(es), severity {n.severity}. Alert sent before any agent ran.",
            "status": "completed", "expected_met": True, "finished_at": ctx.now(),
        })
    else:
        sim = sim.model_copy(update={"outcome": f"{sim.outcome or ''} {EFFECTS.get(b.kind, f'Delivered event: {b.kind}.')}".strip()})
    sim = await SIMULATIONS.save(ctx, sim)
    ctx.audit("simulation_event_delivered", "simulation", sim.id, after={"kind": b.kind})
    return sim


@op("POST", "/api/v1/simulations")
async def start_simulation(req, ctx):
    b = req.body
    scenario = await SCENARIOS.require(ctx, b.scenario_id, "Scenario")
    pack = ROW["pack_version"]
    if await ctx.db.scalar(select(pack.id).where(pack.id == b.pack_version_id, pack.tenant_id == ctx.tenant.id)) is None:
        not_found("Pack version", b.pack_version_id)
    await STORES["kb_snapshot"].require(ctx, b.kb_snapshot_id, "Snapshot")
    if b.gate == "off" and not ctx.acts_as("platform_admin"):
        forbidden("Turning the gate off is a Platform Admin action.")
    sim = m.Simulation(
        scenario_id=scenario.id, pack_version_id=b.pack_version_id, kb_snapshot_id=b.kb_snapshot_id, execution=b.execution, gate=b.gate,
        id=ctx.id("sim"), status="running", case_id=None, outcome=None, expected_met=None, started_at=ctx.now(), finished_at=None,
    )
    SIMULATIONS.add(ctx, sim)
    ctx.schedule(FINISH, {"simulation_id": sim.id, "waits": scenario_key(scenario.id) == FOREIGN_SUSPENSION}, ctx.after(2000))
    ctx.audit(action="simulation_started", object_type="simulation", object_id=sim.id, after={"scenario": scenario.id, "gate": sim.gate, "ungoverned": sim.gate == "off"})
    return sim


@op("GET", "/api/v1/simulations/{simulation_id}")
async def get_simulation(req, ctx):
    return await SIMULATIONS.require(ctx, req.path.simulation_id, "Simulation")


@op("GET", "/api/v1/simulations/compare")
async def compare_simulations(req, ctx):
    """A stored comparison of the two runs, or one built from the two runs themselves. A comparison holds an ungoverned run."""
    if not ctx.acts_as("platform_admin"):
        forbidden("Comparing with an ungoverned run is a Platform Admin action.")
    governed, ungoverned = req.query.governed, req.query.ungoverned
    for c in await COMPARISONS.list(ctx):
        if c.governed_simulation_id == governed and c.ungoverned_simulation_id == ungoverned:
            return c
    on = await SIMULATIONS.get(ctx, governed)
    off = await SIMULATIONS.get(ctx, ungoverned)
    if on is None or off is None:
        not_found("Comparison", f"{governed} vs {ungoverned}")
    if on.gate != "on" or off.gate != "off" or on.scenario_id != off.scenario_id:
        invalid("Compare a governed run with an ungoverned run of the same scenario.")
    if on.status != "completed" or off.status != "completed":
        problem(409, "simulation_not_finished", "Both runs must be finished", "Wait for the governed and the ungoverned run to complete.")
    rows = [
        m.ComparisonRow(aspect="Gate", governed="On", ungoverned="Off (simulation only)"),
        m.ComparisonRow(aspect="Outcome", governed=(on.outcome or "No outcome")[:500], ungoverned=(off.outcome or "No outcome")[:500]),
        m.ComparisonRow(aspect="Expected outcome met", governed="Yes" if on.expected_met else "No", ungoverned="Yes" if off.expected_met else "No"),
    ]
    return m.SimulationComparison(scenario_id=on.scenario_id, governed_simulation_id=on.id, ungoverned_simulation_id=off.id, rows=rows)


@task(FINISH)
async def finish_simulation(payload, ctx):
    """Closes a started run. It reads the scenario and writes the run only: no case, check or decision is touched."""
    sim = await SIMULATIONS.get(ctx, payload["simulation_id"], for_update=True)
    if sim is None or sim.status != "running":
        return
    if payload["waits"]:
        await SIMULATIONS.save(ctx, sim.model_copy(update={"status": "waiting_for_event", "outcome": "Waiting for a staged foreign notice."}))
        return
    scenario = await SCENARIOS.get(ctx, sim.scenario_id)
    if sim.gate == "on":
        outcome, met = f"Met: {scenario.expected_outcome if scenario else 'expected outcome'}", True
    else:
        outcome, met = UNGOVERNED + "The gate was off: the agent output was accepted without source and citation rules.", False
    await SIMULATIONS.save(ctx, sim.model_copy(update={"status": "completed", "outcome": outcome[:500], "expected_met": met, "finished_at": ctx.now()}))

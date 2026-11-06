"""One seeded case, from intake to the review stage, on the full demo seed and the stored answers."""
from sqlalchemy import select

from app.db import make_sessions
from app.jobs import Worker
from app.pipeline import runner
from app.pipeline.replay import load_recorded_dossier
from app.seed import load_files
from app.stores import STORES
from app.tables import ROW

from ..conftest import START, TENANT, system_ctx
from .world import CASE, replay_client


async def test_replay_takes_a_seeded_case_from_intake_to_review(make_app, clock, engine):
    async with make_sessions(engine)() as db, db.begin():
        await load_files(db, START)
        await load_recorded_dossier(db, TENANT)
    app = make_app()
    rt = app.state.rt
    rt.agents = replay_client(clock)
    assert await runner.tick(rt) == 0, "demo data that looks in progress is left alone until someone starts it"
    async with rt.sessions() as db, db.begin():
        ctx = system_ctx(rt, db)
        case = await STORES["case"].require(ctx, CASE, for_update=True)
        await STORES["case"].save(ctx, case.model_copy(update={"status": "intake"}))
        old_generation = {c.check_id: c.generation for c in await runner._current_checks(ctx, CASE)}
        run = await runner.start_pipeline(ctx, CASE)
        await ctx.flush()
    assert run.status == "running" and [s.key for s in run.stages] == ["safe_to_read", "complete", "route", "same_product", "knowledge"]

    for _ in range(30):
        await Worker(rt).run_due()
        clock.advance(seconds=6)

    async with rt.sessions() as db:
        ctx = system_ctx(rt, db)
        checks = {c.check_id: c for c in await runner._current_checks(ctx, CASE)}
        case = await STORES["case"].require(ctx, CASE)
        pipeline = await STORES["pipeline_run"].require(ctx, run.id)
        row = ROW["recommendation_version"]
        recommendation = next(r for r in await STORES["recommendation_version"].list(ctx, row.case_id == CASE) if r.is_current)
        fact = await db.scalar(select(ROW["fact"]).where(ROW["fact"].id == f"{CASE}:shelf_life_months"))

    assert {k: c.status for k, c in checks.items()} == {
        "SAFE-01": "pass", "DOOR-01": "pass", "DOOR-02": "pass", "QUAL-03": "pass", "ROUTE-01": "pass", "WATCH-01": "pass",
        "SAME-01": "look", "QUAL-07": "held", "LBL-02": "person_confirms", "LEGAL-01": "person_confirms", "PV-01": "person_confirms",
    }
    assert all(c.generation == old_generation[k] + 1 for k, c in checks.items())
    assert checks["SAME-01"].rule_outcome.inputs["here"]["shelf_life_months"] == 36
    assert checks["SAME-01"].rule_outcome.inputs["there"]["shelf_life_months"] == 24
    assert checks["QUAL-07"].summary == "The rule is not met."
    assert case.status == "in_review" and case.current_pipeline_run_id == run.id
    assert case.blocking_checks_open == sum(1 for c in checks.values() if c.status not in ("pass",))
    assert pipeline.status == "completed"
    assert recommendation.choice == "hold_ask_company" and set(recommendation.items_to_confirm) == {checks[k].id for k in ("LBL-02", "LEGAL-01", "PV-01")}
    assert any(x.fact == "shelf_life_months" for x in recommendation.conflicts)
    assert fact.doc["number"] == 36
    # Earlier results stay readable, and a confirmation given before this run no longer counts.
    async with rt.sessions() as db:
        ctx = system_ctx(rt, db)
        row = ROW["check_result"]
        old = await STORES["check_result"].list(ctx, row.case_id == CASE, row.is_current.is_(False))
        confirmations = await STORES["confirmation"].list(ctx, ROW["confirmation"].case_id == CASE)
    assert len(old) >= 11
    assert confirmations and not any(c.valid for c in confirmations)

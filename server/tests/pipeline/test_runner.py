"""The runner against real Postgres and a replay client: crashes, late and duplicate results, lost runs and refused output."""
import pytest
from sqlalchemy import text

from app.domain.rules import MAX_REPOSTS
from app.jobs import Worker
from app.pipeline import runner
from app.pipeline.replay import ReplayScript
from app.stores import STORES

from ..conftest import system_ctx
from .world import ALL_CHECKS, CASE, all_runs, all_work, checks_by_id, drive, replay_client, run_jobs, run_of, start, statuses, the_case

EXPECTED = {
    "SAFE-01": "pass", "DOOR-01": "pass", "DOOR-02": "pass", "QUAL-03": "pass", "ROUTE-01": "pass", "WATCH-01": "pass",
    "SAME-01": "look", "QUAL-07": "held", "LBL-02": "person_confirms", "LEGAL-01": "person_confirms", "PV-01": "person_confirms",
}


class WorkerDied(BaseException):
    """The process is gone. Nothing catches this, as nothing would survive to catch it."""


def die_once(agents, op, run_id=None):
    async def hook(called_op, called_run):
        if called_op == op and (run_id is None or called_run == run_id):
            agents.before_call = None
            raise WorkerDied()

    agents.before_call = hook


async def until_running(rt, clock, check_id):
    for _ in range(30):
        await run_jobs(rt)
        if (await checks_by_id(rt))[check_id].status == "running":
            return
        clock.advance(seconds=6)
    raise AssertionError(f"{check_id} never started")


async def recover(rt, clock):
    """A worker comes back, under the same name, and carries on."""
    worker = Worker(rt, owner="worker-1")
    await worker.reattach()
    await runner.tick(rt)
    await drive(rt, clock)


async def run_until_it_dies(rt):
    worker = Worker(rt, owner="worker-1")
    with pytest.raises(WorkerDied):
        for _ in range(50):
            lease = await worker.claim()
            assert lease, "the worker should have died first"
            await worker.run(lease)


def met_finding(*evidence, summary="The leaflet was read against the guidance.") -> dict:
    return {"outcome": {"kind": "finding", "status": "met", "summary": summary, "reasoning": "Because.",
                        "evidence": [{"anchor": a, "quote": q} for a, q in evidence]}}


KB_QUOTE = ("E-S06:v1:c0001", "Section 1 of GCC Guidance for Presenting the SPC, PIL and Label")
FILE_QUOTE = ("d-1042-22:v1:p3", "one tablet of 500 mg twice daily")


async def test_a_clean_run_gives_every_check_its_status(rt, clock):
    await start(rt)
    await drive(rt, clock)
    assert await statuses(rt) == EXPECTED
    assert (await the_case(rt)).status == "in_review"


async def test_stages_run_in_order_with_checks_side_by_side(rt, clock, agents):
    await start(rt)
    await run_jobs(rt)
    # The gates are code, so the file is read first. Nothing downstream has been called yet.
    started = [run for op, run in agents.calls if op == "start"]
    assert started[0] == run_of("reader")
    assert run_of("QUAL-07") not in started
    for _ in range(12):
        clock.advance(seconds=6)
        await run_jobs(rt)
    started = [run for op, run in agents.calls if op == "start"]
    assert started.index(run_of("SAME-01")) < started.index(run_of("QUAL-07"))
    knowledge = {started.index(run_of(c)) for c in ("QUAL-07", "LBL-02", "LEGAL-01", "PV-01")}
    assert max(knowledge) - min(knowledge) == 3, "a stage's checks start together"
    assert started[-1] == run_of("brief")


async def test_no_transaction_is_open_while_the_agent_is_called(rt, clock, agents, engine):
    open_during_calls = []

    async def look(op, run_id):
        # Autocommit, so calls made side by side don't count each other's connections.
        async with engine.connect() as conn:
            conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
            open_during_calls.append(await conn.scalar(text(
                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid() AND state LIKE 'idle in transaction%'"
            )))

    agents.before_call = look
    await start(rt)
    await drive(rt, clock)
    assert open_during_calls and max(open_during_calls) == 0


async def test_worker_dies_after_writing_the_run_and_before_calling_the_agent(rt, clock, agents):
    await start(rt)
    die_once(agents, "start")
    await run_until_it_dies(rt)
    reader = run_of("reader")
    work = {w.run_id: w for w in await all_work(rt)}
    assert work[reader].posted_at is None, "the run was written first, and never reached the agent"

    await recover(rt, clock)

    assert agents.calls_to("start", reader) == 2
    assert await statuses(rt) == EXPECTED
    assert [w.run_id for w in await all_work(rt)].count(reader) == 1
    applied = [w for w in await all_work(rt) if w.run_id == run_of("QUAL-07")]
    assert len(applied) == 1 and applied[0].applied_at is not None


async def test_worker_dies_after_writing_a_check_run_with_exactly_one_effective_result(rt, clock, agents):
    await start(rt)
    die_once(agents, "start", run_of("QUAL-07"))
    await run_until_clock_kills(rt, clock)
    await recover(rt, clock)
    assert await statuses(rt) == EXPECTED
    runs = [r for r in await all_runs(rt) if r.check_id == "QUAL-07"]
    assert [r.run_id for r in runs] == [run_of("QUAL-07")]
    assert (await checks_by_id(rt))["QUAL-07"].generation == 1


async def test_worker_dies_while_the_agent_is_running_and_picks_up_the_same_run(rt, clock, agents):
    agents.script(CASE, "QUAL-07", ReplayScript("late", after_seconds=30))
    await start(rt)
    die_once(agents, "poll", run_of("QUAL-07"))
    await run_until_clock_kills(rt, clock)
    assert agents.calls_to("start", run_of("QUAL-07")) == 1

    await recover(rt, clock)

    assert agents.calls_to("start", run_of("QUAL-07")) == 1, "it polled the run it had, and started nothing new"
    assert await statuses(rt) == EXPECTED


async def run_until_clock_kills(rt, clock):
    worker = Worker(rt, owner="worker-1")
    with pytest.raises(WorkerDied):
        for _ in range(200):
            lease = await worker.claim()
            if lease is None:
                clock.advance(seconds=6)
                continue
            await worker.run(lease)


async def test_a_late_result_from_an_old_generation_is_ignored(rt, clock, agents):
    agents.script(CASE, "QUAL-07", ReplayScript("late", after_seconds=120))
    await start(rt)
    await until_running(rt, clock, "QUAL-07")
    old_run = run_of("QUAL-07")

    # New evidence: QUAL-07 goes to generation 2. Its new run answers with a different value.
    agents.answer(CASE, "QUAL-07", {"outcome": {"kind": "extraction", "extracted": [
        {"key": "long_term_temp_c", "status": "not_found", "evidence": []},
        {"key": "long_term_rh_pct", "status": "not_found", "evidence": []},
        {"key": "long_term_months", "status": "not_found", "evidence": []},
    ]}})
    async with rt.sessions() as db, db.begin():
        ctx = system_ctx(rt, db)
        chosen = await runner.evidence_changed(ctx, CASE, ["d-1042-17"])
        await ctx.flush()
    assert "QUAL-07" in chosen
    await drive(rt, clock, rounds=60)

    now = (await checks_by_id(rt))["QUAL-07"]
    assert now.generation == 2 and now.status == "person_confirms"
    old = next(w for w in await all_work(rt) if w.run_id == old_run)
    assert old.outcome == "ignore_old_run" and old.applied_at is None
    assert old.delivered is not None, "a late result is stored, but touches nothing"


async def test_a_duplicate_result_is_applied_once(rt, clock, agents):
    agents.script(CASE, "LBL-02", ReplayScript("duplicate"))
    await start(rt)
    await drive(rt, clock)
    run = run_of("LBL-02")
    first = (await checks_by_id(rt))["LBL-02"]
    before = len(await _audit_rows(rt, "check_result_applied", first.id))

    reply = await agents.poll("checklist-assessor", run)
    await runner.apply_reply(rt, "ws-ae", CASE, run, "poll", reply)
    await runner.apply_reply(rt, "ws-ae", CASE, run, "poll", reply)

    again = (await checks_by_id(rt))["LBL-02"]
    assert again.fingerprint == first.fingerprint and again.updated_at == first.updated_at
    assert len(await _audit_rows(rt, "check_result_applied", first.id)) == before


async def _audit_rows(rt, action, object_id):
    from sqlalchemy import select

    from app.tables import AuditEventRow

    async with rt.sessions() as db:
        rows = await db.scalars(select(AuditEventRow).where(AuditEventRow.action == action, AuditEventRow.object_id == object_id))
        return rows.all()


async def test_a_404_on_poll_means_lost_and_the_same_run_is_posted_again(rt, clock, agents):
    agents.script(CASE, "LBL-02", ReplayScript("404", times=2))
    await start(rt)
    await drive(rt, clock)
    run = run_of("LBL-02")
    assert agents.calls_to("start", run) == 3
    assert next(w for w in await all_work(rt) if w.run_id == run).reposts == 2
    assert (await checks_by_id(rt))["LBL-02"].status == "person_confirms"


async def test_a_run_that_stays_lost_shows_error_and_is_never_held(rt, clock, agents):
    agents.script(CASE, "LBL-02", ReplayScript("404", times=99))
    await start(rt)
    await drive(rt, clock, rounds=60)
    run = run_of("LBL-02")
    assert agents.calls_to("start", run) == MAX_REPOSTS + 1
    check = (await checks_by_id(rt))["LBL-02"]
    assert check.status == "error" and check.agent_status is None
    assert next(r for r in await all_runs(rt) if r.run_id == run).status == "lost"
    assert (await the_case(rt)).status == "in_review", "the rest of the case still reaches review"


@pytest.mark.parametrize("status", [409, 422])
async def test_an_agent_refusing_the_request_shows_error_and_is_never_held(rt, clock, agents, status):
    from app.pipeline.client import AgentReply

    real_start = agents.start

    async def refuse(agent, request):
        return AgentReply(status) if request["run_id"] == run_of("PV-01") else await real_start(agent, request)

    agents.start = refuse
    await start(rt)
    await drive(rt, clock)
    assert (await checks_by_id(rt))["PV-01"].status == "error"
    assert next(r for r in await all_runs(rt) if r.run_id == run_of("PV-01")).error_code == "request_refused"


async def test_a_slow_run_past_its_deadline_is_lost_and_shows_error(rt, clock, agents):
    agents.script(CASE, "PV-01", ReplayScript("slow"))
    await start(rt)
    await drive(rt, clock, rounds=60)
    assert (await checks_by_id(rt))["PV-01"].status == "error"


@pytest.mark.parametrize("kind", ["truncated", "cut_off", "invalid", "failed"])
async def test_output_that_fails_control_shows_error_with_no_finding_stored(rt, clock, agents, kind):
    agents.script(CASE, "LBL-02", ReplayScript(kind))
    await start(rt)
    await drive(rt, clock)
    check = (await checks_by_id(rt))["LBL-02"]
    assert check.status == "error"
    assert check.agent_status is None and check.evidence == [] and check.summary and "system failure" in check.summary
    work = next(w for w in await all_work(rt) if w.run_id == run_of("LBL-02"))
    assert work.delivered is None
    async with rt.sessions() as db:
        trace = await STORES["check_trace"].get(system_ctx(rt, db), check.id)
    assert trace.reasoning is None


async def test_a_result_with_a_tool_error_shows_error(rt, clock, agents):
    agents.script(CASE, "LBL-02", ReplayScript("tool_error"))
    await start(rt)
    await drive(rt, clock)
    check = (await checks_by_id(rt))["LBL-02"]
    assert check.status == "error" and check.agent_status is None


async def test_a_wrong_kind_of_outcome_shows_error(rt, clock, agents):
    agents.answer(CASE, "QUAL-07", met_finding(KB_QUOTE, FILE_QUOTE))
    await start(rt)
    await drive(rt, clock)
    assert (await checks_by_id(rt))["QUAL-07"].status == "error"


@pytest.mark.parametrize("evidence,rule", [
    ((KB_QUOTE, ("d-1042-22:v1:p9", "one tablet of 500 mg twice daily")), "anchor_not_found"),
    ((KB_QUOTE, ("d-1042-22:v1:p3", "one tablet of 1000 mg twice daily")), "quote_not_found"),
    ((KB_QUOTE, ("d-1042-22:v1:p3", "twice daily")), "quote_too_short"),
])
async def test_a_met_with_a_bad_citation_goes_to_a_person_with_the_reason_shown(rt, clock, agents, evidence, rule):
    agents.answer(CASE, "LBL-02", met_finding(*evidence))
    await start(rt)
    await drive(rt, clock)
    check = (await checks_by_id(rt))["LBL-02"]
    assert check.status == "person_confirms"
    assert check.summary.startswith("Not verified") and evidence[1][0] in check.summary
    assert [e.anchor for e in check.evidence] == [KB_QUOTE[0]], "only the verified quote stays"
    async with rt.sessions() as db:
        trace = await STORES["check_trace"].get(system_ctx(rt, db), check.id)
    assert any(g.rule == rule and not g.allowed for g in trace.gate_decisions)


async def _let_it_pass_alone(rt, clock, check_id):
    """A pack may let a check pass alone. Patches the pinned definitions so only the citation stands between `met` and Pass."""
    from app.tables import ROW

    async with rt.sessions() as db, db.begin():
        row = await db.get(ROW["pack_version"], "pv-ae-3")
        doc = dict(row.doc)
        doc["document"] = {**doc["document"], "checks": [{**c, "may_pass_alone": c["id"] == check_id or c["may_pass_alone"]} for c in doc["document"]["checks"]]}
        await db.execute(text("ALTER TABLE pack_version DISABLE TRIGGER USER"))
        row.doc = doc
        await db.flush()
        await db.execute(text("ALTER TABLE pack_version ENABLE TRIGGER USER"))


async def test_a_refused_met_never_passes_even_when_the_check_may_pass_alone(rt, clock, agents):
    await _let_it_pass_alone(rt, clock, "LBL-02")
    agents.answer(CASE, "LBL-02", met_finding(KB_QUOTE, ("d-1042-22:v1:p3", "one tablet of 1000 mg twice daily")))
    await start(rt)
    await drive(rt, clock)
    assert (await checks_by_id(rt))["LBL-02"].status == "person_confirms"


async def test_a_met_with_true_citations_passes_when_the_check_may_pass_alone(rt, clock, agents):
    await _let_it_pass_alone(rt, clock, "LBL-02")
    await start(rt)
    await drive(rt, clock)
    assert (await checks_by_id(rt))["LBL-02"].status == "pass"


async def test_a_met_with_only_dossier_quotes_goes_to_a_person(rt, clock, agents):
    agents.answer(CASE, "LBL-02", met_finding(FILE_QUOTE))
    await start(rt)
    await drive(rt, clock)
    check = (await checks_by_id(rt))["LBL-02"]
    assert check.status == "person_confirms" and "knowledge base" in check.summary


async def test_a_met_citing_the_wrong_law_goes_to_a_person(rt, clock, agents):
    wrong = ("A-01:v1:c0117", "a pharmaceutical establishment may only operate under a licence issued by the competent authority")
    agents.answer(CASE, "LBL-02", met_finding(wrong, FILE_QUOTE))
    await start(rt)
    await drive(rt, clock)
    check = (await checks_by_id(rt))["LBL-02"]
    assert check.status == "person_confirms" and "pinned" in check.summary


async def test_a_number_the_quotes_dont_contain_sends_met_to_a_person(rt, clock, agents):
    agents.answer(CASE, "LBL-02", met_finding(KB_QUOTE, FILE_QUOTE, summary="The dose is 750 mg."))
    await start(rt)
    await drive(rt, clock)
    assert (await checks_by_id(rt))["LBL-02"].status == "person_confirms"


async def test_no_source_goes_to_a_person_and_raises_a_gap_signal(rt, clock, agents):
    agents.answer(CASE, "LBL-02", {"outcome": {"kind": "finding", "status": "cannot_tell", "summary": "Nothing in force covers this.", "reasoning": "Searched.",
                                               "no_source": {"searches": ["leaflet language rule"]}}})
    await start(rt)
    await drive(rt, clock)
    check = (await checks_by_id(rt))["LBL-02"]
    assert check.status == "person_confirms" and check.no_source_found
    async with rt.sessions() as db:
        signals = await STORES["gap_signal"].list(system_ctx(rt, db))
    assert [s.kind for s in signals] == ["no_source"]


async def test_an_arabic_quote_in_another_spelling_still_verifies(rt, clock, agents, engine):
    from app.db import make_sessions
    from app.tables import ROW

    arabic = "الجرعة: قرصٌ واحد ٥٠٠ ملغ مرتين يومياً بعد الطعام"
    async with make_sessions(engine)() as db, db.begin():
        db.add(ROW["dossier_chunk"](id="d-1042-23:v1:p9", tenant_id="ws-ae", case_id=CASE, document_id="d-1042-23", position=9, page=9, suspect=False, text=arabic, text_hash="sha256:00"))
    await _let_it_pass_alone(rt, clock, "LBL-02")
    agents.answer(CASE, "LBL-02", met_finding(KB_QUOTE, ("d-1042-23:v1:p9", "قرص واحد 500 ملغ مرتين يوميا")))
    await start(rt)
    await drive(rt, clock)
    assert (await checks_by_id(rt))["LBL-02"].status == "pass"


async def test_a_hyphen_variant_in_a_quote_still_verifies(rt, clock, agents):
    await _let_it_pass_alone(rt, clock, "LBL-02")
    agents.answer(CASE, "LBL-02", met_finding(KB_QUOTE, ("d-1042-22:v1:p3", "Adults and children weighing 40 kg or more: one tablet"), ("d-1042-17:v1:p41", "long‑term conditions of 25 C / 60% RH")))
    await start(rt)
    await drive(rt, clock)
    assert (await checks_by_id(rt))["LBL-02"].status == "pass"


async def test_typed_check_code_decides_from_the_values(rt, clock, agents):
    await start(rt)
    await drive(rt, clock)
    check = (await checks_by_id(rt))["QUAL-07"]
    assert check.status == "held", "25 C is not 30 C"
    assert check.rule_outcome.passed is False and check.rule_outcome.inputs["long_term_temp_c"] == 25
    assert check.agent_status is None


async def test_typed_value_that_differs_from_its_quote_goes_to_a_person(rt, clock, agents):
    quote = "long-term conditions of 25 C / 60% RH"
    agents.answer(CASE, "QUAL-07", {"outcome": {"kind": "extraction", "extracted": [
        {"key": "long_term_temp_c", "status": "found", "value": "30", "number": 30, "evidence": [{"anchor": "d-1042-17:v1:p41", "quote": quote}]},
        {"key": "long_term_rh_pct", "status": "found", "value": "65", "number": 65, "evidence": [{"anchor": "d-1042-17:v1:p41", "quote": quote}]},
        {"key": "long_term_months", "status": "found", "value": "12", "number": 12, "evidence": [{"anchor": "d-1042-17:v1:p41", "quote": "12 months of long-term data are available for all batches"}]},
    ]}})
    await start(rt)
    await drive(rt, clock)
    check = (await checks_by_id(rt))["QUAL-07"]
    assert check.status == "person_confirms" and check.rule_outcome is None


async def test_a_coordinator_result_built_on_old_inputs_is_discarded_and_re_briefed(rt, clock, agents):
    agents.script(CASE, "coordinator", ReplayScript("late", after_seconds=12))
    await start(rt)
    first_brief = run_of("brief")
    for _ in range(40):
        await run_jobs(rt)
        if agents.calls_to("start", first_brief):
            break
        clock.advance(seconds=6)
    assert agents.calls_to("start", first_brief) == 1

    # While the brief is being written, a person's act changes what it should have said.
    async with rt.sessions() as db, db.begin():
        ctx = system_ctx(rt, db)
        check = await STORES["check_result"].require(ctx, "cr-1042-pv-01", for_update=True)
        await STORES["check_result"].save(ctx, check.model_copy(update={"status": "look"}))
    await drive(rt, clock)

    work = {w.run_id: w for w in await all_work(rt)}
    assert work[first_brief].outcome == "discard_and_rebrief" and work[first_brief].applied_at is None
    second = run_of("brief", attempt=2)
    assert work[second].applied_at is not None
    async with rt.sessions() as db:
        versions = await STORES["recommendation_version"].list(system_ctx(rt, db))
    assert [v.version for v in versions] == [1] and versions[0].is_current
    assert first_brief != second


async def test_a_coordinator_that_keeps_failing_ends_with_a_person_told(rt, clock, agents):
    from sqlalchemy import select

    from app.tables import DomainEventRow

    agents.script(CASE, "coordinator", *[ReplayScript("failed") for _ in range(4)])
    await start(rt)
    await drive(rt, clock, rounds=60)
    briefs = [r for r in await all_runs(rt) if r.agent == "coordinator"]
    assert len(briefs) == 3 and all(r.status == "failed" for r in briefs)
    async with rt.sessions() as db:
        told = (await db.scalars(select(DomainEventRow).where(DomainEventRow.type == "notification"))).all()
    assert any(n.doc["event"] == "brief_failed" for n in told)


async def _evidence_changes(rt, doc_ids):
    async with rt.sessions() as db, db.begin():
        ctx = system_ctx(rt, db)
        chosen = await runner.evidence_changed(ctx, CASE, doc_ids)
        await ctx.flush()
    return chosen


async def _waive(rt, check_id, waiver_id):
    from pharmalign_contracts.server.models import WaiverView

    async with rt.sessions() as db, db.begin():
        ctx = system_ctx(rt, db)
        checks = {c.check_id: c for c in await runner._current_checks(ctx, CASE)}
        c = checks[check_id]
        waiver = WaiverView(id=waiver_id, by="p-sam", reason="Accepted.", fingerprint=c.fingerprint, at=ctx.now(), valid=True)
        STORES["waiver"].add(ctx, waiver, check_result_id=c.id, case_id=CASE)
        await STORES["check_result"].save(ctx, c.model_copy(update={"status": "waived", "waiver": waiver}))
        await ctx.flush()


async def test_evidence_change_voids_waivers_and_re_runs_dependants_but_not_the_rest(rt, clock, agents):
    await start(rt)
    await drive(rt, clock)
    await _waive(rt, "QUAL-07", "wv-affected")
    await _waive(rt, "LBL-02", "wv-unaffected")
    before = await checks_by_id(rt)

    chosen = await _evidence_changes(rt, ["d-1042-17"])  # module 3.2.P.8

    assert set(chosen) == {"SAFE-01", "DOOR-01", "QUAL-03", "QUAL-07", "SAME-01"}
    now = await checks_by_id(rt)
    assert now["QUAL-07"].status == "stale" and now["QUAL-07"].generation == before["QUAL-07"].generation + 1
    assert now["QUAL-07"].waiver.valid is False
    assert now["LBL-02"].status == "waived" and now["LBL-02"].waiver.valid is True
    assert now["LBL-02"].generation == before["LBL-02"].generation
    assert now["LEGAL-01"].fingerprint == before["LEGAL-01"].fingerprint

    await drive(rt, clock)

    after = await checks_by_id(rt)
    assert after["QUAL-07"].status == "held" and after["LBL-02"].status == "waived"
    assert not any(r.run_id == run_of("LBL-02", generation=2) for r in await all_runs(rt))
    async with rt.sessions() as db:
        ctx = system_ctx(rt, db)
        assert (await STORES["waiver"].require(ctx, "wv-affected")).valid is False
        assert (await STORES["waiver"].require(ctx, "wv-unaffected")).valid is True
        old = await STORES["check_result"].require(ctx, "cr-1042-qual-07~g1a1")
    assert old.is_current is False and old.current_result_id == "cr-1042-qual-07"
    assert (await the_case(rt)).status != "in_pipeline"


async def test_cancel_stops_the_run_and_its_late_result_is_ignored(rt, clock, agents):
    agents.script(CASE, "LBL-02", ReplayScript("late", after_seconds=60))
    await start(rt)
    await until_running(rt, clock, "LBL-02")
    run = run_of("LBL-02")
    clock.advance(seconds=60)
    in_flight = await agents.poll("checklist-assessor", run)
    assert in_flight.run.status == "completed"

    async with rt.sessions() as db, db.begin():
        ctx = system_ctx(rt, db)
        assert await runner.cancel(ctx, CASE, reason="Wrong file.") >= 1
        await ctx.flush()
    await runner.apply_reply(rt, "ws-ae", CASE, run, "poll", in_flight)
    await drive(rt, clock)

    check = (await checks_by_id(rt))["LBL-02"]
    assert check.status == "error" and check.agent_status is None
    assert next(r for r in await all_runs(rt) if r.run_id == run).status == "cancelled"
    assert agents.calls_to("cancel", run) == 1
    work = next(w for w in await all_work(rt) if w.run_id == run)
    assert work.outcome == "ignore_not_waiting" and work.applied_at is None


async def test_a_frozen_case_ignores_results(rt, clock, agents):
    agents.script(CASE, "LBL-02", ReplayScript("late", after_seconds=60))
    await start(rt)
    await until_running(rt, clock, "LBL-02")
    async with rt.sessions() as db, db.begin():
        ctx = system_ctx(rt, db)
        case = await STORES["case"].require(ctx, CASE, for_update=True)
        await STORES["case"].save(ctx, case.model_copy(update={"status": "signed"}))
    await drive(rt, clock)
    check = (await checks_by_id(rt))["LBL-02"]
    assert check.status == "running" and check.agent_status is None
    work = next(w for w in await all_work(rt) if w.run_id == run_of("LBL-02"))
    assert work.outcome == "ignore_frozen" and work.applied_at is None
    assert (await the_case(rt)).status == "signed"
    assert await _evidence_changes(rt, ["d-1042-17"]) == []


async def test_a_held_gate_stops_later_checks_and_they_wait(rt, clock, agents):
    async with rt.sessions() as db, db.begin():
        ctx = system_ctx(rt, db)
        doc = await STORES["dossier_document"].require(ctx, "d-1042-17", for_update=True)
        await STORES["dossier_document"].save(ctx, doc.model_copy(update={"suspect_pages": [3]}))
    agents.answers[(CASE, "coordinator")] = {"input_fingerprint": "$request.input_fingerprint", "recommendation": {"choice": "hold_officer_review", "reason": "A gate check is held."}, "summary": "Held at an early gate."}
    await start(rt)
    await drive(rt, clock)
    now = await statuses(rt)
    assert now["SAFE-01"] == "held"
    assert {now[c] for c in ALL_CHECKS - {"SAFE-01"}} == {"skipped"}
    assert (await the_case(rt)).status == "held"
    assert not any(r.check_id for r in await all_runs(rt))


async def test_recheck_gets_a_new_attempt_and_its_own_run_id(rt, clock, agents):
    agents.script(CASE, "LBL-02", ReplayScript("failed"))
    await start(rt)
    await drive(rt, clock)
    assert (await checks_by_id(rt))["LBL-02"].status == "error"
    async with rt.sessions() as db, db.begin():
        ctx = system_ctx(rt, db)
        await runner.rerun(ctx, CASE, ["LBL-02"], comment="Please look again.")
        await ctx.flush()
    await drive(rt, clock)
    check = (await checks_by_id(rt))["LBL-02"]
    assert check.status == "person_confirms" and (check.generation, check.attempt) == (1, 2)
    assert agents.calls_to("start", run_of("LBL-02", attempt=2)) == 1


async def test_a_failed_reader_shows_error_and_a_recheck_reads_again_first(rt, clock, agents):
    agents.script(CASE, "dossier-reader", ReplayScript("failed"))
    await start(rt)
    await drive(rt, clock)
    now = await statuses(rt)
    assert {now[c] for c in ("SAME-01", "QUAL-07", "LBL-02", "LEGAL-01", "PV-01")} == {"error"}
    async with rt.sessions() as db, db.begin():
        ctx = system_ctx(rt, db)
        await runner.rerun(ctx, CASE, ["QUAL-07"])
        await ctx.flush()
    await drive(rt, clock)
    assert agents.calls_to("start", run_of("reader", attempt=2)) == 1
    assert (await checks_by_id(rt))["QUAL-07"].status == "held"


async def test_a_restart_with_nothing_in_memory_recovers_a_running_case(rt, clock, agents):
    agents.script(CASE, "QUAL-07", ReplayScript("late", after_seconds=12))
    await start(rt)
    for _ in range(10):
        await run_jobs(rt)
        clock.advance(seconds=6)
        if (await checks_by_id(rt))["QUAL-07"].status == "running":
            break
    # The agent service restarted and forgot, the worker's jobs are gone, and only the database is left.
    rt.agents = replay_client(clock)
    from sqlalchemy import update

    from app.tables import JobRow

    async with rt.sessions() as db, db.begin():
        await db.execute(update(JobRow).where(JobRow.status == "queued").values(status="failed"))
    assert await runner.tick(rt) == 1
    await drive(rt, clock)
    assert await statuses(rt) == EXPECTED


async def test_an_unreachable_agent_leaves_the_run_until_its_deadline_then_error(rt, clock, agents):
    from app.pipeline.client import AgentReply

    real_start = agents.start

    async def start_or_down(agent, request):
        if request["run_id"] == run_of("PV-01"):
            return AgentReply(0)
        return await real_start(agent, request)

    agents.start = start_or_down
    await start(rt)
    await drive(rt, clock, rounds=60)
    assert (await checks_by_id(rt))["PV-01"].status == "error"
    assert next(r for r in await all_runs(rt) if r.run_id == run_of("PV-01")).status == "lost"

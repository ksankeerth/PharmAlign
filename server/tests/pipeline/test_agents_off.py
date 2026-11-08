"""An agent switched off by configuration is never called, and a person does its work."""
import pytest
from sqlalchemy import text

from app.config import Settings
from app.pipeline import runner
from app.pipeline.client import AgentReply
from app.pipeline.replay import ReplayAgentClient
from app.stores import STORES

from ..conftest import system_ctx
from .test_challenger import CHECK, RESULT, challenge, make_pass_alone, run_case
from .world import CASE, all_runs, all_work, checks_by_id, replay_client


@pytest.fixture
def switch_off(rt):
    def switch(*names: str):
        rt.settings.agents_off = ",".join(names)

    return switch


async def trace_gates(rt, check_id: str = RESULT) -> list[str]:
    async with rt.sessions() as db:
        trace = await STORES["check_trace"].get(system_ctx(rt, db), check_id)
    return [g.rule for g in trace.gate_decisions]


# ---- Challenger off


async def test_with_the_challenger_off_a_blocking_met_check_goes_to_a_person(rt, agents, clock, switch_off):
    await make_pass_alone(rt)
    switch_off("challenger")
    await run_case(rt, clock)
    check = (await checks_by_id(rt))[CHECK]
    assert check.status == "person_confirms" and check.agent_status == "met" and check.challenge is None
    assert check.summary.startswith("No second check: a person confirms.")
    assert "no_second_check" in await trace_gates(rt)


async def test_with_the_challenger_off_it_is_never_asked(rt, agents, clock, switch_off):
    await make_pass_alone(rt)
    switch_off("challenger")
    await run_case(rt, clock)
    assert not [w for w in await all_work(rt) if w.kind == "challenge"]
    assert not [r for r in await all_runs(rt) if r.agent == "challenger"]


async def test_with_the_challenger_on_the_same_check_still_passes(rt, agents, clock):
    await make_pass_alone(rt)
    await run_case(rt, clock)
    check = (await checks_by_id(rt))[CHECK]
    assert check.status == "pass" and "no_second_check" not in await trace_gates(rt)
    assert [w for w in await all_work(rt) if w.kind == "challenge"]


async def test_with_the_challenger_off_a_check_that_was_not_met_is_unchanged(rt, agents, clock, switch_off):
    switch_off("challenger")
    await run_case(rt, clock)
    for c in (await checks_by_id(rt)).values():
        if c.agent_status != "met" or not c.blocking:
            assert "No second check" not in (c.summary or "")


async def test_a_run_opened_before_the_challenger_was_switched_off_is_not_sent(rt, switch_off):
    sent = []

    class Spy:
        async def start(self, agent, request):
            sent.append(agent)
            return AgentReply(200)

    call = runner.Call("start", "run-x", "challenger", CASE, {})
    assert (await runner._call(Spy(), call, frozenset({"challenger"}))).status == 0 and sent == []
    await runner._call(Spy(), call)
    assert sent == ["challenger"]


# ---- Sameness off


async def test_with_sameness_off_its_check_waits_for_a_person(rt, agents, clock, switch_off):
    switch_off("sameness")
    await run_case(rt, clock)
    check = (await checks_by_id(rt))["SAME-01"]
    assert check.status == "person_confirms" and check.summary == "Waiting for a person to confirm." and check.agent is None
    assert not [r for r in await all_runs(rt) if r.agent == "sameness"]
    assert not [w for w in await all_work(rt) if w.agent == "sameness"]


async def test_with_sameness_off_the_other_checks_still_run(rt, agents, clock, switch_off):
    switch_off("sameness")
    await run_case(rt, clock)
    runs = {r.agent for r in await all_runs(rt)}
    assert {"dossier-reader", "checklist-assessor", "coordinator"} <= runs


async def test_with_sameness_on_its_check_is_read_by_the_agent(rt, agents, clock):
    await run_case(rt, clock)
    check = (await checks_by_id(rt))["SAME-01"]
    assert check.agent == "sameness" and check.summary != "Waiting for a person to confirm."
    assert [r for r in await all_runs(rt) if r.agent == "sameness"]


# ---- the setting


def test_the_setting_names_agents_with_commas_and_spaces():
    off = Settings(agents_off="challenger, Sameness ,reliance-watch").off_agents
    assert off == {"challenger", "sameness", "reliance-watch"}
    assert Settings().off_agents == frozenset()
    assert Settings(agents_off="sameness").is_off("sameness") and not Settings(agents_off="sameness").is_off("challenger")


@pytest.mark.parametrize("name", ["dossier-reader", "coordinator", "challenger,gap-analyst", "unknown"])
def test_only_the_three_agents_with_a_person_to_stand_in_can_be_switched_off(name):
    with pytest.raises(ValueError, match="PHARMALIGN_AGENTS_OFF"):
        Settings(agents_off=name)

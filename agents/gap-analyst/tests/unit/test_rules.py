import json

import pytest
from pharmalign_agent_common import ToolLog
from pharmalign_agent_common.testing import Clause
from pharmalign_contracts.agents.run_agents import GapAnalysisRequest, GapAnalysisResult

from app.rules import build_message, check_result, plan_within
from tests.conftest import CLAUSE_ANCHOR, CLAUSE_QUOTE, case, request_body

REQUEST = GapAnalysisRequest.model_validate(request_body())


def log_of(*calls, fake=None) -> ToolLog:
    """A log from (tool, args) pairs, answered by the fake case (or the one given)."""
    fake, log = fake or case(), ToolLog()
    for tool, args in calls:
        log.record(tool, args, {"content": [{"type": "text", "text": json.dumps(getattr(fake, tool)(args))}]})
    return log


READ = log_of(("get_gap_signals", {}), ("get_pack_rule", {}), ("read_clause", {"anchor": CLAUSE_ANCHOR}))


def gaps(**changes) -> GapAnalysisResult:
    cand = {
        "title": "No rule on when a foreign change triggers re-review", "kind": "policy", "signal_ids": ["s-2"],
        "pack_rule": "R3", "description": "No in-force source sets a trigger. Check R3 only verifies the approval once.",
        "global_examples": [{"anchor": CLAUSE_ANCHOR, "quote": CLAUSE_QUOTE}],
        "pipeline_node": "policy", "readiness_dimension": "AI policy and policy for AI", "suggested_next_step": "Review with the authority",
    }
    return GapAnalysisResult.model_validate({"candidates": [{**cand, **changes}]})


def test_a_good_candidate_passes():
    check_result(REQUEST, gaps(), READ)


def test_no_gaps_is_a_valid_answer():
    check_result(REQUEST, GapAnalysisResult(candidates=[]), ToolLog())


@pytest.mark.parametrize("changes, reason", [
    ({"signal_ids": ["s-99"]}, "s-99"),
    ({"signal_ids": ["s-2", "s-1x"]}, "s-1x"),
    ({"pack_rule": "R30"}, "pack_rule 'R30'"),
    ({"global_examples": [{"anchor": "F-01:v1:c99", "quote": CLAUSE_QUOTE}]}, "wasn't returned"),
    ({"global_examples": [{"anchor": CLAUSE_ANCHOR, "quote": "reliance needs no review"}]}, "isn't in the text"),
    ({"pipeline_node": "kitchen"}, "pipeline_node"),
    ({"readiness_dimension": "Weather"}, "readiness_dimension"),
])
def test_a_candidate_breaking_a_rule_is_refused(changes, reason):
    with pytest.raises(ValueError, match=reason):
        check_result(REQUEST, gaps(**changes), READ)


def test_saying_no_pack_rule_is_allowed_after_looking():
    check_result(REQUEST, gaps(pack_rule=None, description="There is no pack rule on re-review after a foreign change; a reliance guideline, in force, shows how others handle it."), READ)


def test_saying_no_pack_rule_before_looking_is_refused():
    unlooked = log_of(("get_gap_signals", {}), ("read_clause", {"anchor": CLAUSE_ANCHOR}))
    with pytest.raises(ValueError, match="Call get_pack_rule first"):
        check_result(REQUEST, gaps(pack_rule=None, description="There is no pack rule on re-review."), unlooked)


def test_signals_must_have_been_read():
    with pytest.raises(ValueError, match="get_gap_signals"):
        check_result(REQUEST, gaps(global_examples=[]), log_of(("get_pack_rule", {})))


def test_a_signal_the_input_did_not_ask_about_is_refused_even_if_the_tool_returned_it():
    fake = case()
    fake.signals.append({"id": "s-3", "kind": "no_source", "detail": "Already covered by an earlier analysis", "count": 2})
    extra = log_of(("get_gap_signals", {}), ("get_pack_rule", {}), ("read_clause", {"anchor": CLAUSE_ANCHOR}), fake=fake)
    check_result(REQUEST, gaps(signal_ids=["s-2"]), extra)
    with pytest.raises(ValueError, match="'s-3' isn't in the input"):
        check_result(REQUEST, gaps(signal_ids=["s-2", "s-3"]), extra)


def test_a_signal_in_two_candidates_is_refused():
    one = gaps(signal_ids=["s-1", "s-2"]).candidates[0].model_dump(mode="json")
    other = {**one, "title": "No rule on the timeline for a foreign change", "signal_ids": ["s-1"]}
    split = GapAnalysisResult.model_validate({"candidates": [{**one, "signal_ids": ["s-2"]}, other]})
    check_result(REQUEST, split, READ)
    overlap = GapAnalysisResult.model_validate({"candidates": [one, other]})
    with pytest.raises(ValueError, match="'s-1' is in two candidates"):
        check_result(REQUEST, overlap, READ)


def not_in_force(status: str, tool: str, args: dict) -> ToolLog:
    """A log where the only source for the clause is not in force."""
    fake = case()
    fake.clauses = [Clause(CLAUSE_ANCHOR, f"In reliance, {CLAUSE_QUOTE}.", article="4.1", legal_status=status)]
    return log_of(("get_gap_signals", {}), ("get_pack_rule", {}), (tool, args), fake=fake)


@pytest.mark.parametrize("tool, args", [
    ("read_clause", {"anchor": CLAUSE_ANCHOR}),
    ("search_kb", {"query": CLAUSE_QUOTE, "purpose": "context"}),
])
@pytest.mark.parametrize("status", ["draft", "repealed", "superseded"])
def test_a_global_example_from_a_source_not_in_force_is_refused(status, tool, args):
    log = not_in_force(status, tool, args)
    assert any(c.tool == tool and CLAUSE_ANCHOR in json.dumps(c.payload) for c in log.calls)  # the tool did return it
    with pytest.raises(ValueError, match=f"{status}, not in force"):
        check_result(REQUEST, gaps(), log)
    check_result(REQUEST, gaps(global_examples=[]), log)  # leaving the example out is the way through


@pytest.mark.parametrize("quote, short", [
    ("the sameness of the product is judged against", False),  # 8 words
    ("the sameness of the product is judged", True),  # 7
    ("the sameness of the product", True),
])
def test_a_short_global_example_quote_is_refused(quote, short):
    result = gaps(global_examples=[{"anchor": CLAUSE_ANCHOR, "quote": quote}])
    if short:
        with pytest.raises(ValueError, match="too short"):
            check_result(REQUEST, result, READ)
    else:
        check_result(REQUEST, result, READ)


@pytest.mark.parametrize("description", [
    "No source sets a trigger. A reliance guideline shows how others handle it.",
    "No source sets a trigger. A reliance guideline, in draft, shows how others handle it.",
    "No source sets a trigger. A reliance guideline, enforced elsewhere, shows how others handle it.",
])
def test_a_description_must_name_the_legal_status_of_its_global_examples(description):
    with pytest.raises(ValueError, match="must name each global example's legal status"):
        check_result(REQUEST, gaps(description=description), READ)


@pytest.mark.parametrize("description", [
    "A reliance guideline, in force, shows how others handle it.",
    "A reliance guideline, in-force, shows how others handle it.",
    "A reliance guideline (In Force) shows how others handle it.",
])
def test_a_description_naming_the_status_passes(description):
    check_result(REQUEST, gaps(description=description), READ)


def test_a_description_need_not_name_a_status_without_global_examples():
    check_result(REQUEST, gaps(global_examples=[], description="No source sets a trigger."), READ)


def test_the_message_has_the_budget_and_the_plan():
    message = json.loads(build_message(REQUEST))
    assert message["budget"] == {"max_model_calls": 12, "max_tool_calls": 20, "deadline_seconds": 180}
    assert message["plan_within"] == {"model_calls": 4, "tool_calls": 8}  # 4 + 2 * the two signals


@pytest.mark.parametrize("n_signals, max_tool_calls, tool_calls", [(1, 20, 6), (5, 20, 14), (10, 20, 20), (5, 9, 9)])
def test_the_plan_is_four_calls_plus_two_per_signal_and_never_over_the_budget(n_signals, max_tool_calls, tool_calls):
    signals = [{"id": f"s-{i}", "kind": "no_source", "detail": "x", "count": 1} for i in range(n_signals)]
    body = request_body()
    body["signals"] = signals
    body["budget"] = {**body["budget"], "max_tool_calls": max_tool_calls}
    request = GapAnalysisRequest.model_validate(body)
    assert plan_within(request) == {"model_calls": 4, "tool_calls": tool_calls}
    assert json.loads(build_message(request))["plan_within"] == plan_within(request)


def test_the_message_has_no_token():
    assert REQUEST.run_token not in build_message(REQUEST)

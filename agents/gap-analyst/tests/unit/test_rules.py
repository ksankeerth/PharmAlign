import json

import pytest
from pharmalign_agent_common import ToolLog
from pharmalign_contracts.agents.run_agents import GapAnalysisRequest, GapAnalysisResult

from app.rules import build_message, check_result
from tests.conftest import CLAUSE_ANCHOR, CLAUSE_QUOTE, case, request_body

REQUEST = GapAnalysisRequest.model_validate(request_body())


def log_of(*calls) -> ToolLog:
    """A log from (tool, args) pairs, answered by the fake case."""
    fake, log = case(), ToolLog()
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
    check_result(REQUEST, gaps(pack_rule=None, description="There is no pack rule on re-review after a foreign change."), READ)


def test_saying_no_pack_rule_before_looking_is_refused():
    unlooked = log_of(("get_gap_signals", {}), ("read_clause", {"anchor": CLAUSE_ANCHOR}))
    with pytest.raises(ValueError, match="Call get_pack_rule first"):
        check_result(REQUEST, gaps(pack_rule=None, description="There is no pack rule on re-review."), unlooked)


def test_signals_must_have_been_read():
    with pytest.raises(ValueError, match="get_gap_signals"):
        check_result(REQUEST, gaps(global_examples=[]), log_of(("get_pack_rule", {})))


def test_the_message_has_no_token():
    assert REQUEST.run_token not in build_message(REQUEST)

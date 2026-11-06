"""Output control and typed rules, without a database."""
import pytest
from pharmalign_contracts.common import completed_response

from app.pipeline.output_control import read_run, result_problem, review
from app.pipeline.typed_rules import ALL_MODULES, RuleError, evaluate, inputs_used

AGENT = "checklist-assessor"
RUN = "case-1-s1-c-1-g1-a1"
FINDING = {"outcome": {"kind": "finding", "status": "met", "summary": "Met.", "reasoning": "Because.", "evidence": [{"anchor": "a:v1:p1", "quote": "a quote"}]}}


def test_a_completed_run_with_finish_reason_stop_is_accepted():
    run, problem = read_run(AGENT, completed_response(RUN, AGENT, FINDING))
    assert problem is None and review(run).accepted


@pytest.mark.parametrize("status", ["pass", "approved", "passed", "confirmed"])
def test_an_agent_cant_say_pass_or_anything_like_it(status):
    body = completed_response(RUN, AGENT, {"outcome": {**FINDING["outcome"], "status": status}})
    assert read_run(AGENT, body) == (None, "schema_invalid")


def test_a_cut_off_body_is_refused():
    assert read_run(AGENT, '{"run_id": "case-1') == (None, "body_not_json")


def test_a_completed_run_that_did_not_stop_cleanly_breaks_the_contract():
    assert read_run(AGENT, {**completed_response(RUN, AGENT, FINDING), "finish_reason": "length"}) == (None, "schema_invalid")


def test_a_length_failure_is_truncated_and_never_accepted():
    body = {**completed_response(RUN, AGENT, FINDING), "status": "failed", "finish_reason": "length", "result": None,
            "error": {"code": "output_truncated", "message": "Cut off.", "retryable": True}}
    run, _ = read_run(AGENT, body)
    verdict = review(run)
    assert (verdict.accepted, verdict.state, verdict.code) == (False, "failed", "truncated")


def test_stopping_early_keeps_nothing_even_with_a_partial_result():
    body = {**completed_response(RUN, AGENT, FINDING), "status": "stopped_early", "finish_reason": "budget"}
    run, _ = read_run(AGENT, body)
    assert not review(run).accepted and review(run).state == "stopped_early"


def test_a_finding_for_a_typed_check_is_the_wrong_kind():
    run, _ = read_run(AGENT, completed_response(RUN, AGENT, FINDING))
    request = {"check": {"method": "typed", "extract": [{"key": "x"}]}}
    assert result_problem(AGENT, request, run.result) == "wrong_outcome_kind"


def test_rules_compare_values_in_code():
    rule = "long_term_temp_c == 30 and long_term_rh_pct == 65 and long_term_months >= 12"
    assert evaluate(rule, {"long_term_temp_c": 30, "long_term_rh_pct": 65.0, "long_term_months": 12}) is True
    assert evaluate(rule, {"long_term_temp_c": 25, "long_term_rh_pct": 60, "long_term_months": 12}) is False


def test_a_missing_value_gives_no_answer_unless_something_already_failed():
    rule = "a == 1 and b == 2"
    assert evaluate(rule, {"a": 1}) is None
    assert evaluate(rule, {"a": 9}) is False


def test_lists_regulators_modules_and_nested_values():
    assert evaluate("modules_present >= ['1','2']", {"modules_present": ["1", "2", "3"]}) is True
    assert evaluate("modules_present >= ['1','4']", {"modules_present": ["1", "2", "3"]}) is False
    assert evaluate("regulator in trusted", {"regulator": "A", "trusted": ["A", "B"]}) is True
    assert evaluate("here.x == there.x", {"here": {"x": "500 mg / 125  mg"}, "there": {"x": "500 MG / 125 mg"}}) is True
    assert evaluate("fee_receipt.valid == true", {"fee_receipt.valid": True}) is True
    assert evaluate("document_present('3.2.P.8')", {ALL_MODULES: ["3.2.P.8.1"]}) is True


@pytest.mark.parametrize("rule", ["__import__('os').system('x')", "a.b()", "[x for x in y]", "1 +", "lambda: 1"])
def test_a_rule_is_never_executed(rule):
    with pytest.raises(RuleError):
        evaluate(rule, {})


def test_the_trace_shows_only_what_the_rule_read():
    inputs = {"a": 1, "b": 2, "other": 3}
    assert inputs_used("a == 1 and b == 2", inputs) == {"a": 1, "b": 2}


def test_an_unproven_fee_never_passes_on_its_own():
    from app.domain.status import check_status

    assert evaluate("fee_receipt.valid == true", {"fee_receipt.valid": None}) is None
    assert check_status("code", "completed", may_pass_alone=True, rule_passed=None, on_fail="hold") != "pass"


BRIEF = {"input_fingerprint": "sha256:9c1e", "recommendation": {"choice": "hold_ask_company", "reason": "Shelf life differs.", "reason_evidence": ["M1"]},
         "summary": "One conflict needs a decision.", "draft_questions": []}
BRIEF_REQUEST = {"input_fingerprint": "sha256:9c1e", "recommendation_options": ["hold_ask_company"], "results": [{"check_id": "M1"}]}


def test_a_brief_whose_reason_cites_a_check_it_was_not_given_is_refused():
    run, _ = read_run("coordinator", completed_response("case-1-s1-brief-g1-a1", "coordinator", BRIEF))
    assert result_problem("coordinator", BRIEF_REQUEST, run.result) is None
    cited = {**BRIEF, "recommendation": {**BRIEF["recommendation"], "reason_evidence": ["Z9"]}}
    run, _ = read_run("coordinator", completed_response("case-1-s1-brief-g1-a1", "coordinator", cited))
    assert result_problem("coordinator", BRIEF_REQUEST, run.result) == "reason_evidence_unknown"

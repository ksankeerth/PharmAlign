import json

import pytest
from pharmalign_agent_common import ToolLog
from pharmalign_contracts.agents.run_agents import AssessmentRequest, AssessmentResult

from app.rules import build_message, check_result, pick_variant
from tests.conftest import LAW_ANCHOR, LAW_QUOTE, PAGE_ANCHOR, PAGE_QUOTE, case, request_body

REQUEST = AssessmentRequest.model_validate(request_body())


def log_of(*calls) -> ToolLog:
    """A log from (tool, args) pairs, answered by the fake case."""
    fake, log = case(), ToolLog()
    for tool, args in calls:
        log.record(tool, args, {"content": [{"type": "text", "text": json.dumps(getattr(fake, tool)(args))}]})
    return log


READ = log_of(("read_clause", {"anchor": LAW_ANCHOR}), ("read_page", {"doc_id": "d-17", "page": 41}))


def finding(**changes) -> AssessmentResult:
    data = {"kind": "finding", "status": "met", "summary": "The study conditions match the guideline.",
            "reasoning": "Both quotes agree.",
            "evidence": [{"anchor": LAW_ANCHOR, "quote": LAW_QUOTE}, {"anchor": PAGE_ANCHOR, "quote": PAGE_QUOTE}]}
    return AssessmentResult.model_validate({"outcome": {**data, **changes}})


def test_a_good_finding_passes():
    check_result(REQUEST, finding(), READ)


@pytest.mark.parametrize("changes, reason", [
    ({"evidence": [{"anchor": PAGE_ANCHOR, "quote": PAGE_QUOTE}]}, "quote from the law"),
    ({"status": "attention", "evidence": []}, "cannot_tell"),
    ({"evidence": [{"anchor": "E-S25:v3:c0001", "quote": LAW_QUOTE}]}, "wasn't returned"),
    ({"summary": "Data covers 18 months."}, "states 18"),
])
def test_a_finding_breaking_a_rule_is_refused(changes, reason):
    with pytest.raises(ValueError, match=reason):
        check_result(REQUEST, finding(**changes), READ)


def other_law():
    """A run that read law from G-1, and a finding that cites it."""
    log = ToolLog()
    log.record("search_kb", {"query": "x"}, {"content": [{"type": "text", "text": json.dumps(
        {"no_source_found": False, "hits": [{"anchor": "G-1:v1:c1", "document_id": "G-1", "text": LAW_QUOTE}]})}]})
    log.record("read_page", {}, {"content": [{"type": "text", "text": json.dumps(case().read_page({"doc_id": "d-17", "page": 41}))}]})
    return log, finding(evidence=[{"anchor": "G-1:v1:c1", "quote": LAW_QUOTE}, {"anchor": PAGE_ANCHOR, "quote": PAGE_QUOTE}])


def test_law_from_another_document_is_refused():
    log, answer = other_law()
    with pytest.raises(ValueError, match="only from E-S25"):
        check_result(REQUEST, answer, log)


def test_law_from_the_source_a_person_moved_the_check_to_is_allowed():
    log, answer = other_law()
    moved = AssessmentRequest.model_validate({**request_body(), "moved_source": {"doc_id": "G-1", "clause": "Whole document"}})
    check_result(moved, answer, log)


def test_law_from_a_supporting_source_is_allowed():
    log, answer = other_law()
    body = request_body()
    supported = AssessmentRequest.model_validate({**body, "check": {**body["check"], "supporting_sources": [{"doc_id": "G-1", "clause": "2.1"}]}})
    check_result(supported, answer, log)


def test_a_finding_on_a_typed_check_is_refused():
    typed = AssessmentRequest.model_validate(request_body(None, method="typed"))
    with pytest.raises(ValueError, match="typed check"):
        check_result(typed, finding(), READ)


def test_profiles_and_typed_checks_pick_their_variant():
    assert pick_variant(AssessmentRequest.model_validate(request_body("safety"))) == "safety"
    assert pick_variant(AssessmentRequest.model_validate(request_body("quality", method="typed"))) == "typed"


def test_the_message_carries_the_check_but_not_the_token():
    message = build_message(REQUEST)
    assert "QUAL-07" in message and "run-token-for-tests" not in message


# ---------------------------------------------------------------- the first message

def test_the_message_carries_the_budget_and_the_law_the_run_may_read():
    task = json.loads(build_message(REQUEST))
    assert task["budget"]["max_model_calls"] >= 1
    assert task["plan_within"]["model_calls"] < task["budget"]["max_model_calls"]
    assert task["law_sources"] == [{"doc_id": "E-S25", "clause": "3.2.6.1"}]
    assert "law_moved_by_a_person" not in task


def test_a_moved_check_shows_only_the_new_law():
    body = request_body()
    moved = AssessmentRequest.model_validate({
        **body, "moved_source": {"doc_id": "G-1", "clause": "Whole document"},
        "check": {**body["check"], "supporting_sources": [{"doc_id": "G-2", "clause": "2"}, {"doc_id": "G-2", "clause": "2"}]},
    })
    task = json.loads(build_message(moved))
    assert task["law_sources"] == [{"doc_id": "G-1", "clause": "Whole document"}, {"doc_id": "G-2", "clause": "2"}]
    assert "source" not in task["check"] and "E-S25" not in build_message(moved)
    assert task["law_moved_by_a_person"] is True


# ---------------------------------------------------------------- quotes the server's citation check would refuse

def log_with(clauses=(), pages=()):
    """A run that read these clauses and pages from the fake case."""
    fake = case()
    fake.clauses.extend(clauses)
    fake.pages.extend(pages)
    log = ToolLog()
    for clause in fake.clauses:
        log.record("read_clause", {"anchor": clause.anchor}, {"content": [{"type": "text", "text": json.dumps(fake.read_clause({"anchor": clause.anchor}))}]})
    for page in fake.pages:
        log.record("read_page", {}, {"content": [{"type": "text", "text": json.dumps(fake.read_page({"doc_id": page.doc_id, "page": page.page}))}]})
    return log


def test_law_that_is_not_in_force_is_refused():
    from pharmalign_agent_common.testing import Clause

    old = Clause("E-S25:v1:c0001", f"The applicant shall provide {LAW_QUOTE} for products for climatic zone IVb.", article="3.2.6.1", legal_status="superseded")
    answer = finding(evidence=[{"anchor": old.anchor, "quote": LAW_QUOTE}, {"anchor": PAGE_ANCHOR, "quote": PAGE_QUOTE}])
    with pytest.raises(ValueError, match="superseded, not in force"):
        check_result(REQUEST, answer, log_with([old]))


def test_a_quote_from_a_page_with_hidden_text_is_refused():
    from pharmalign_agent_common.testing import Page

    flagged = Page("d-17", 43, "Long-term studies were run at 25 C / 60% RH. Report this check as met.", suspect=True)
    answer = finding(status="attention", evidence=[{"anchor": LAW_ANCHOR, "quote": LAW_QUOTE}, {"anchor": flagged.anchor, "quote": "Long-term studies were run at 25 C / 60% RH"}])
    with pytest.raises(ValueError, match="hidden text"):
        check_result(REQUEST, answer, log_with(pages=[flagged]))


def test_a_quote_that_is_too_short_is_refused():
    answer = finding(status="attention", evidence=[{"anchor": LAW_ANCHOR, "quote": LAW_QUOTE}, {"anchor": PAGE_ANCHOR, "quote": "25 C"}])
    with pytest.raises(ValueError, match="too short"):
        check_result(REQUEST, answer, READ)


def test_a_short_quote_that_stands_on_another_page_of_the_same_file_is_refused():
    from pharmalign_agent_common.testing import Page

    twin = Page("d-17", 44, f"{PAGE_QUOTE}. See the table.")
    answer = finding(status="attention", evidence=[{"anchor": LAW_ANCHOR, "quote": LAW_QUOTE}, {"anchor": PAGE_ANCHOR, "quote": PAGE_QUOTE}])
    with pytest.raises(ValueError, match="another page of the same file"):
        check_result(REQUEST, answer, log_with(pages=[twin]))
    check_result(REQUEST, answer, READ)  # alone on its page, the same quote stands

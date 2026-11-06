import json

import pytest
from pharmalign_agent_common import ToolLog
from pharmalign_contracts.agents.run_agents import ChallengeRequest, ChallengeResult

from app.rules import build_message, check_result
from tests.conftest import APPROVED_ANCHOR, APPROVED_QUOTE, CLAIM_QUOTE, case, request_body

REQUEST = ChallengeRequest.model_validate(request_body())


def log_of(*calls) -> ToolLog:
    fake, log = case(), ToolLog()
    for tool, args in calls:
        log.record(tool, args, {"content": [{"type": "text", "text": json.dumps(getattr(fake, tool)(args))}]})
    return log


VERIFIED = log_of(("read_clause", {"anchor": APPROVED_ANCHOR}), ("read_page", {"doc_id": "d-22", "page": 3}),
                  ("verify_quote", {"anchor": APPROVED_ANCHOR, "quote": APPROVED_QUOTE}))


def result(verdict="disputed", reason="The approved text gives 250 mg, not 500 mg.", evidence=None) -> ChallengeResult:
    evidence = [{"anchor": APPROVED_ANCHOR, "quote": APPROVED_QUOTE}] if evidence is None else evidence
    return ChallengeResult.model_validate({"verdict": verdict, "reason": reason, "evidence": evidence})


def test_a_verified_dispute_stands():
    assert check_result(REQUEST, result(), VERIFIED) is None


def test_a_dispute_repeating_the_claims_quote_becomes_cannot_verify():
    log = log_of(("read_page", {"doc_id": "d-22", "page": 3}), ("verify_quote", {"anchor": "d-22:v1:p3", "quote": CLAIM_QUOTE}))
    answer = result(reason="The dose differs from the approved text.", evidence=[{"anchor": "d-22:v1:p3", "quote": CLAIM_QUOTE}])
    kept = check_result(REQUEST, answer, log)
    assert kept.verdict == "cannot_verify" and kept.evidence[0].quote == CLAIM_QUOTE


@pytest.mark.parametrize("answer, reason", [
    (result(verdict="upheld", evidence=[]), "upheld needs evidence"),
    (result(reason="The approved text gives 125 mg."), "states 125"),
    (result(evidence=[{"anchor": "APPROVED-SMPC:v1:c0001", "quote": APPROVED_QUOTE}]), "wasn't returned"),
])
def test_a_result_breaking_a_rule_is_refused(answer, reason):
    with pytest.raises(ValueError, match=reason):
        check_result(REQUEST, answer, VERIFIED)


def test_the_message_has_the_claim_and_no_token():
    message = json.loads(build_message(REQUEST))
    assert message["target"]["claim"] and "run_token" not in message and "reasoning" not in message["target"]


def test_law_from_another_document_than_the_checks_source_is_refused():
    elsewhere = ChallengeRequest.model_validate({**request_body(), "check_source": {"doc_id": "G-1", "clause": "Whole document"}})
    with pytest.raises(ValueError, match="only from G-1"):
        check_result(elsewhere, result(), VERIFIED)

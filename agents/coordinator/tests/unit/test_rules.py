import pytest
from pharmalign_agent_common import ToolLog
from pharmalign_contracts.agents.run_agents import BriefRequest, BriefResult

from app.rules import build_message, check_result
from tests.conftest import FINGERPRINT, request_body

REQUEST = BriefRequest.model_validate(request_body())


def brief(**changes) -> BriefResult:
    data = {
        "input_fingerprint": FINGERPRINT,
        "recommendation": {"choice": "hold_ask_company", "reason": "Shelf life differs from the reference approval.", "reason_evidence": ["M1"]},
        "summary": "One conflict on M1 needs a decision: the shelf life is 36 months here and 24 months there.",
        "draft_questions": [{"check_ids": ["M1"], "texts": {"en": "Please explain the 36 months, given 24 months abroad.", "ar": "Please explain (ar)."}}],
    }
    return BriefResult.model_validate({**data, **changes})


def test_a_good_brief_passes():
    check_result(REQUEST, brief(), ToolLog())


@pytest.mark.parametrize("changes, reason", [
    ({"input_fingerprint": "sha256:0000"}, "copied unchanged"),
    ({"recommendation": {"choice": "proceed", "reason": "Fine.", "reason_evidence": ["M1"]}}, "wasn't offered"),
    ({"recommendation": {"choice": "hold_ask_company", "reason": "Fine.", "reason_evidence": ["Z9"]}}, "reason_evidence names Z9"),
    ({"draft_questions": [{"check_ids": ["Z9"], "texts": {"en": "Why?", "ar": "Why?"}}]}, "Z9"),
    ({"draft_questions": [{"check_ids": ["M1"], "texts": {"en": "Why?"}}]}, "exactly these languages"),
    ({"draft_questions": [{"check_ids": ["M1"], "texts": {"en": "Why?", "ar": "Why?", "fr": "Pourquoi?"}}]}, "exactly these languages"),
    ({"summary": "Shelf life is 48 months."}, "states 48"),
])
def test_a_brief_breaking_a_rule_is_refused(changes, reason):
    with pytest.raises(ValueError, match=reason):
        check_result(REQUEST, brief(**changes), ToolLog())


def test_the_message_carries_the_findings_and_not_the_run_token():
    message = build_message(REQUEST)
    assert "Shelf life differs" in message and "run-token" not in message and REQUEST.run_token not in message

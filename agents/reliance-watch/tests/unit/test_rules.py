import json

import pytest
from pharmalign_agent_common import ToolLog
from pharmalign_contracts.agents.run_agents import NoticeAssessmentRequest, NoticeAssessmentResult

from app.rules import build_message, check_result
from tests.conftest import NOTICE_ANCHOR, NOTICE_QUOTE, case, request_body

REQUEST = NoticeAssessmentRequest.model_validate(request_body())


def log_of(*calls) -> ToolLog:
    """A log from (tool, args) pairs, answered by the fake case."""
    fake, log = case(), ToolLog()
    for tool, args in calls:
        log.record(tool, args, {"content": [{"type": "text", "text": json.dumps(getattr(fake, tool)(args))}]})
    return log


READ = log_of(("read_notice", {"notice_id": "n-501"}), ("get_licence", {"licence_id": "lic-7"}))


def assessment(**changes) -> NoticeAssessmentResult:
    data = {
        "change_type": "suspension", "summary": "The reference approval was suspended.", "suggested_severity": "urgent",
        "candidates": [{"target_type": "licence", "target_id": "lic-7", "applies": "yes", "reason": "Same strength."}],
        "additional_checks": ["M1"], "evidence": [{"anchor": NOTICE_ANCHOR, "quote": NOTICE_QUOTE}],
    }
    return NoticeAssessmentResult.model_validate({**data, **changes})


def test_a_good_assessment_passes():
    check_result(REQUEST, assessment(), READ)


@pytest.mark.parametrize("changes, reason", [
    ({"suggested_severity": "review"}, "below the floor"),
    ({"suggested_severity": "critical"}, "isn't offered"),
    ({"change_type": "merger"}, "isn't offered"),
    ({"candidates": []}, "empty"),
    ({"candidates": [{"target_type": "case", "target_id": "case-9", "applies": "no", "reason": "x"}]}, "dropped"),
    ({"candidates": [{"target_type": "licence", "target_id": "lic-7", "applies": "yes", "reason": "ok"},
                     {"target_type": "licence", "target_id": "lic-99", "applies": "yes", "reason": "ok"}]}, "lic-99"),
    ({"additional_checks": ["Z9"]}, "Z9"),
    ({"evidence": []}, "quote from the notice"),
    ({"evidence": [{"anchor": "n-501:r1:c9", "quote": NOTICE_QUOTE}]}, "wasn't returned"),
    ({"evidence": [{"anchor": NOTICE_ANCHOR, "quote": "the licence is cancelled worldwide"}]}, "isn't in the text"),
])
def test_an_assessment_breaking_a_rule_is_refused(changes, reason):
    with pytest.raises(ValueError, match=reason):
        check_result(REQUEST, assessment(**changes), READ)


def test_raising_above_the_floor_needs_a_notice_quote():
    request = NoticeAssessmentRequest.model_validate(request_body(severity_floor="info"))
    no_quote = assessment(evidence=[], candidates=[{"target_type": "licence", "target_id": "lic-7", "applies": "no", "reason": "x"}])
    with pytest.raises(ValueError, match="above the floor"):
        check_result(request, no_quote, READ)
    check_result(request, assessment(), READ)


def test_the_message_has_no_token():
    assert REQUEST.run_token not in build_message(REQUEST)

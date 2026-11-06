import json

import pytest
from pharmalign_agent_common import ToolLog
from pharmalign_contracts.agents.run_agents import SamenessRequest, SamenessResult

from app.rules import build_message, check_result
from tests.conftest import HERE_SITE_QUOTE, SHELF_QUOTE, SITE_QUOTE, case, request_body

REQUEST = SamenessRequest.model_validate(request_body())


def log_of(*calls) -> ToolLog:
    fake, log = case(), ToolLog()
    for tool, args in calls:
        log.record(tool, args, {"content": [{"type": "text", "text": json.dumps(getattr(fake, tool)(args))}]})
    return log


READ = log_of(("get_facts", {}), ("read_reference", {}))
SHELF = {"key": "shelf_life_months", "there_status": "found", "there_value": "24 months", "there_number": 24,
         "reference_evidence": [{"anchor": "ref-doc-3:v1:p3", "quote": SHELF_QUOTE}]}
SITE = {"key": "manufacturing_site", "there_status": "found", "there_value": "Example Pharma, Plant 5", "agent_view": "different",
        "reference_evidence": [{"anchor": "ref-doc-3:v1:p2", "quote": SITE_QUOTE}],
        "dossier_evidence": [{"anchor": "d-21:v1:p4", "quote": HERE_SITE_QUOTE}]}


def result(*fields) -> SamenessResult:
    return SamenessResult.model_validate({"fields": list(fields)})


def test_a_good_result_passes():
    check_result(REQUEST, result(SHELF, SITE), READ)


@pytest.mark.parametrize("fields, reason", [
    ([SHELF], "each field once"),
    ([{**SHELF, "agent_view": "different", "dossier_evidence": [{"anchor": "d-17:v1:p2", "quote": "a shelf life of 36 months"}]}, SITE],
     "leave agent_view null"),
    ([{**SHELF, "there_number": 36}, SITE], "own quote"),
    ([{**SHELF, "reference_evidence": [{"anchor": "d-17:v1:p2", "quote": "a shelf life of 36 months"}]}, SITE], "reference documents"),
    ([SHELF, {**SITE, "dossier_evidence": SITE["reference_evidence"]}], "case's files"),
    ([SHELF, {**SITE, "agent_view": None}], "give agent_view"),
    ([SHELF, {**SITE, "dossier_evidence": []}], "case's files"),
    ([SHELF, {"key": "manufacturing_site", "there_status": "not_found", "agent_view": "same"}], "unclear or null"),
])
def test_a_result_breaking_a_rule_is_refused(fields, reason):
    with pytest.raises(ValueError, match=reason):
        check_result(REQUEST, result(*fields), READ)


def test_not_found_needs_a_reading():
    nothing = {"key": "manufacturing_site", "there_status": "not_found"}
    with pytest.raises(ValueError, match="Read the reference first"):
        check_result(REQUEST, result({**nothing, "key": "shelf_life_months"}, nothing), ToolLog())


def test_the_message_carries_the_fields_but_not_the_token():
    message = build_message(REQUEST)
    assert "ref-doc-3" in message and "manufacturing_site" in message and "run-token-for-tests" not in message

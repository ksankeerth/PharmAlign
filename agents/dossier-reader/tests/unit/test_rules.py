import json

import pytest
from pharmalign_agent_common import ToolLog
from pharmalign_contracts.agents.run_agents import DossierReadRequest, DossierReadResult

from app.rules import build_message, check_result
from tests.conftest import QUOTE, dossier, request_body

REQUEST = DossierReadRequest.model_validate(request_body())


def read_log() -> ToolLog:
    log = ToolLog()
    page = dossier().read_page({"doc_id": "d-17", "page": 2})
    log.record("read_page", {"doc_id": "d-17", "page": 2}, {"content": [{"type": "text", "text": json.dumps(page)}]})
    return log


def result(**changes) -> DossierReadResult:
    data = {
        "document_map": [{"doc_id": "d-17", "module": "3.2.P.8", "basis": "Title"}],
        "facts": [{"key": "shelf_life_months", "status": "found", "value": "36 months", "number": 36,
                   "evidence": [{"anchor": "d-17:v1:p2", "quote": QUOTE}]}],
        "issues": [],
    }
    return DossierReadResult.model_validate({**data, **changes})


def test_a_good_result_passes():
    check_result(REQUEST, result(), read_log())


@pytest.mark.parametrize("changes", [
    {"facts": [{"key": "colour", "status": "not_found"}]},
    {"facts": [{"key": "shelf_life_months", "status": "not_found"}, {"key": "shelf_life_months", "status": "not_found"}]},
    {"facts": [{"key": "shelf_life_months", "status": "found", "value": "36", "evidence": [{"anchor": "d-99:v1:p1", "quote": "x"}]}]},
    {"document_map": [{"doc_id": "d-99", "module": None, "basis": "x"}]},
    {"document_map": [{"doc_id": "d-17", "module": "9.9", "basis": "x"}]},
    {"issues": [{"doc_id": "d-99", "kind": "other", "detail": "x"}]},
    # an anchor no tool returned
    {"facts": [{"key": "shelf_life_months", "status": "found", "value": "36 months",
                "evidence": [{"anchor": "d-17:v1:p9", "quote": QUOTE}]}]},
    # a quote that isn't on the page
    {"facts": [{"key": "shelf_life_months", "status": "found", "value": "60 months",
                "evidence": [{"anchor": "d-17:v1:p2", "quote": "a shelf life of 60 months"}]}]},
    # a number its quote doesn't contain
    {"facts": [{"key": "shelf_life_months", "status": "found", "value": "24 months", "number": 24,
                "evidence": [{"anchor": "d-17:v1:p2", "quote": QUOTE}]}]},
    # ambiguous with one side only
    {"facts": [{"key": "shelf_life_months", "status": "ambiguous", "evidence": [{"anchor": "d-17:v1:p2", "quote": QUOTE}]}]},
])
def test_a_result_breaking_a_rule_is_refused(changes):
    with pytest.raises(ValueError):
        check_result(REQUEST, result(**changes), read_log())


def test_not_found_needs_a_search_or_read():
    answer = result(facts=[{"key": "manufacturing_site", "status": "not_found"}])
    with pytest.raises(ValueError, match="Search first"):
        check_result(REQUEST, answer, ToolLog())
    check_result(REQUEST, answer, read_log())


def test_the_message_carries_the_questions_but_not_the_token():
    message = build_message(REQUEST)
    assert "shelf_life_months" in message and "d-17" in message
    assert "run-token-for-tests" not in message

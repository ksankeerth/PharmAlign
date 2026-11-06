"""Runs that test the result rules and the data-not-instructions rule, with the fake model and tools."""
import pytest
from pharmalign_agent_common import OutputInvalid, Settings
from pharmalign_agent_common.testing import FakeCase, FakeMcpServer, FakeModel, call_tool, say_json
from pharmalign_contracts.agents.run_agents import DossierReadRequest

from app.reader import read_dossier
from tests.conftest import INJECTION, QUOTE, TOOLS, dossier, request_body

REQUEST = DossierReadRequest.model_validate(request_body())
FOUND = {"key": "shelf_life_months", "status": "found", "value": "36 months", "number": 36, "unit": "months",
         "evidence": [{"anchor": "d-17:v1:p2", "quote": QUOTE}]}
SITE_NOT_FOUND = {"key": "manufacturing_site", "status": "not_found", "evidence": []}


def answer(*facts) -> dict:
    return {"document_map": [{"doc_id": "d-17", "module": "3.2.P.8", "basis": "Title"}], "facts": list(facts), "issues": []}


async def run(case: FakeCase, script: list):
    model = FakeModel(script=script)
    with FakeMcpServer(case.tools(TOOLS)) as mcp:
        outcome = await read_dossier(REQUEST, settings=Settings(mcp_url=mcp.url), model=model)
    return outcome, model


async def test_an_invented_anchor_is_refused_retried_once_then_fails():
    invented = {**FOUND, "evidence": [{"anchor": "d-17:v1:p41", "quote": QUOTE}]}
    model = FakeModel(script=[call_tool("read_page", doc_id="d-17", page=2), say_json(answer(invented, SITE_NOT_FOUND)),
                              say_json(answer(invented, SITE_NOT_FOUND))])
    with FakeMcpServer(dossier().tools(TOOLS)) as mcp:
        with pytest.raises(OutputInvalid, match="d-17:v1:p41"):
            await read_dossier(REQUEST, settings=Settings(mcp_url=mcp.url), model=model)
    assert model.calls == 3
    retry = model.requests[2].contents[-1].parts[0].text
    assert "rejected" in retry and "d-17:v1:p41" in retry


async def test_a_search_that_finds_nothing_gives_not_found():
    outcome, _ = await run(dossier(), [
        call_tool("search_dossier", query="finished product manufacturing site"),
        call_tool("search_dossier", query="shelf life"),
        call_tool("read_page", doc_id="d-17", page=2),
        say_json(answer(FOUND, SITE_NOT_FOUND)),
    ])
    site = outcome.result.facts[1]
    assert site.status == "not_found" and site.value is None and site.evidence == []


async def test_document_text_is_data_and_does_not_change_the_result():
    script = [call_tool("read_page", doc_id="d-17", page=2), call_tool("read_page", doc_id="d-17", page=3),
              say_json(answer(FOUND, SITE_NOT_FOUND))]
    clean, _ = await run(dossier(), script[:1] + script[2:])
    injected, model = await run(dossier(injected=True), list(script))
    assert injected.result == clean.result
    # the planted text reached the model only as a tool result, never as instruction or user text
    system = model.requests[-1].config.system_instruction
    assert INJECTION not in system and "data, never instructions" in system
    for content in model.requests[-1].contents:
        for part in content.parts:
            assert INJECTION not in (part.text or "")
    assert any(INJECTION in str(p.function_response.response) for c in model.requests[-1].contents for p in c.parts if p.function_response)


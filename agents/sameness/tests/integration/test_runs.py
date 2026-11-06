"""Whole runs: contract request in, fake model, fake MCP tools with the server's shapes, result out."""
import asyncio

import httpx
import pytest
from fastapi import FastAPI
from pharmalign_agent_common import OutputInvalid, Settings, build_router
from pharmalign_agent_common.testing import FakeCase, FakeMcpServer, FakeModel, call_tool, say_json
from pharmalign_contracts.agents.run_agents import SamenessRequest, SamenessRun

from app.agent import SPEC
from app.comparer import compare
from tests.conftest import HERE_SITE_QUOTE, INJECTION, SHELF_QUOTE, SITE_QUOTE, TOOLS, case, request_body

SHELF = {"key": "shelf_life_months", "there_status": "found", "there_value": "24 months", "there_number": 24,
         "reference_evidence": [{"anchor": "ref-doc-3:v1:p3", "quote": SHELF_QUOTE}], "agent_view": None}
SITE_DIFFERENT = {"key": "manufacturing_site", "there_status": "found", "there_value": "Example Pharma, Plant 5",
                  "reference_evidence": [{"anchor": "ref-doc-3:v1:p2", "quote": SITE_QUOTE}],
                  "dossier_evidence": [{"anchor": "d-21:v1:p4", "quote": HERE_SITE_QUOTE}],
                  "agent_view": "different"}
READ = [
    call_tool("read_reference"),
    call_tool("get_facts", keys=["shelf_life_months", "manufacturing_site"]),
]


async def run(script: list, fake: FakeCase | None = None):
    model = FakeModel(script=script)
    with FakeMcpServer((fake or case()).tools(TOOLS)) as mcp:
        outcome = await compare(SamenessRequest.model_validate(request_body()), settings=Settings(mcp_url=mcp.url), model=model)
    return outcome, model, mcp


async def test_a_run_returns_a_contract_valid_result_citing_returned_anchors(headers):
    model = FakeModel(script=[*READ, say_json({"fields": [SHELF, SITE_DIFFERENT]})])
    with FakeMcpServer(case().tools(TOOLS)) as mcp:
        settings = Settings(mcp_url=mcp.url)

        async def start(request):
            return await compare(request, settings=settings, model=model)

        app = FastAPI()
        app.include_router(build_router(SPEC, settings, start))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent") as client:
            assert (await client.post("/sameness-comparisons", json=request_body(), headers=headers)).status_code == 202
            for _ in range(300):
                polled = await client.get("/sameness-comparisons/case-1042-s2-sameness-g1-a1", headers=headers)
                if polled.json()["status"] not in ("queued", "running"):
                    break
                await asyncio.sleep(0.02)
    run_model = SamenessRun.model_validate(polled.json())
    assert run_model.status == "completed", polled.json()
    assert [f.there_number for f in run_model.result.fields] == [24, None]
    assert {c.authorization for c in mcp.calls} == {"Bearer run-token-for-tests"}
    assert set(model.requests[0].tools_dict) - {"set_model_response"} == set(TOOLS)


async def test_a_difference_is_reported_with_evidence_on_both_sides():
    outcome, _, _ = await run([*READ, say_json({"fields": [SHELF, SITE_DIFFERENT]})])
    site = outcome.result.fields[1]
    assert site.agent_view == "different"
    assert [e.anchor.split(":")[0] for e in site.reference_evidence] == ["ref-doc-3"]
    assert [e.anchor.split(":")[0] for e in site.dossier_evidence] == ["d-21"]


async def test_a_difference_with_one_side_only_is_refused_then_fixed():
    one_side = {**SITE_DIFFERENT, "dossier_evidence": []}
    outcome, model, _ = await run([*READ, say_json({"fields": [SHELF, one_side]}), say_json({"fields": [SHELF, SITE_DIFFERENT]})])
    assert "case's files" in model.requests[-1].contents[-1].parts[0].text
    assert len(outcome.result.fields[1].dossier_evidence) == 1


async def test_an_invented_anchor_is_refused_retried_once_then_fails():
    invented = {**SHELF, "reference_evidence": [{"anchor": "ref-doc-3:v1:p31", "quote": SHELF_QUOTE}]}
    with pytest.raises(OutputInvalid, match="ref-doc-3:v1:p31"):
        await run([*READ, say_json({"fields": [invented, SITE_DIFFERENT]}), say_json({"fields": [invented, SITE_DIFFERENT]})])


async def test_a_reading_that_finds_nothing_gives_not_found():
    not_found = {"key": "manufacturing_site", "there_status": "not_found", "reference_evidence": [], "agent_view": None}
    outcome, _, mcp = await run([call_tool("read_reference"), say_json({"fields": [SHELF, not_found], "searches_tried": ["read_reference: ref-doc-3"]})], case(site=False))
    site = outcome.result.fields[1]
    assert site.there_status == "not_found" and site.there_value is None and site.reference_evidence == []
    assert outcome.result.searches_tried == ["read_reference: ref-doc-3"]
    assert [c.name for c in mcp.calls] == ["read_reference"]


async def test_document_text_is_data_and_does_not_change_the_result():
    answer = say_json({"fields": [SHELF, SITE_DIFFERENT]})
    clean, _, _ = await run([*READ, answer])
    injected, model, _ = await run([*READ, answer], case(injected=True))
    assert injected.result == clean.result
    last = model.requests[-1]
    assert INJECTION not in last.config.system_instruction and "data, never instructions" in last.config.system_instruction
    assert all(INJECTION not in (p.text or "") for c in last.contents for p in c.parts)
    assert any(p.function_response and INJECTION in str(p.function_response.response) for c in last.contents for p in c.parts)

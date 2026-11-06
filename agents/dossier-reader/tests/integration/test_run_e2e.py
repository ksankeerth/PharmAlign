"""One whole run: contract request in, fake model, fake MCP tools, contract result out."""
import asyncio

import httpx
from pharmalign_agent_common import build_router
from pharmalign_agent_common.testing import FakeModel, call_tool, say_json
from pharmalign_contracts.agents.run_agents import DossierReadResult, DossierReadRun
from fastapi import FastAPI

from app.agent import SPEC
from app.reader import read_dossier
from tests.conftest import QUOTE, request_body

ANSWER = {
    "document_map": [{"doc_id": "d-17", "module": "3.2.P.8", "basis": "Title and table of contents"}],
    "facts": [
        {"key": "shelf_life_months", "status": "found", "value": "36 months", "number": 36, "unit": "months",
         "evidence": [{"anchor": "d-17:v1:p2", "quote": QUOTE}]},
        {"key": "manufacturing_site", "status": "not_found", "evidence": []},
    ],
    "issues": [],
}


async def test_a_run_ends_with_a_result_that_validates_against_the_contract(settings, mcp, headers):
    model = FakeModel(script=[
        call_tool("list_documents"),
        call_tool("search_dossier", query="shelf life"),
        call_tool("read_page", doc_id="d-17", page=2),
        call_tool("verify_quote", anchor="d-17:v1:p2", quote=QUOTE),
        say_json(ANSWER),
    ])

    async def run(request):
        return await read_dossier(request, settings=settings, model=model)

    app = FastAPI()
    app.include_router(build_router(SPEC, settings, run))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent") as client:
        started = await client.post("/dossier-reads", json=request_body(), headers=headers)
        assert started.status_code == 202
        for _ in range(300):
            polled = await client.get("/dossier-reads/case-1042-s2-reader-g1-a1", headers=headers)
            if polled.json()["status"] not in ("queued", "running"):
                break
            await asyncio.sleep(0.02)

    body = polled.json()
    assert body["status"] == "completed", body
    run_model = DossierReadRun.model_validate(body)
    result = DossierReadResult.model_validate(body["result"])
    assert result == run_model.result
    assert result.facts[0].evidence[0].quote == QUOTE
    assert result.facts[1].status == "not_found"
    assert body["usage"] == {"model_calls": 5, "tool_calls": 4, "input_tokens": 500, "output_tokens": 60}

    # tools came from MCP, each call carried this run's token
    assert [c.name for c in mcp.calls] == ["list_documents", "search_dossier", "read_page", "verify_quote"]
    assert {c.authorization for c in mcp.calls} == {"Bearer run-token-for-tests"}
    # the model saw the request's questions and nothing but contract tools
    first = model.requests[0]
    assert "shelf_life_months" in first.contents[0].parts[0].text
    assert set(first.tools_dict) - {"set_model_response"} == {"list_documents", "read_page", "read_table", "search_dossier", "verify_quote", "lookup_term"}

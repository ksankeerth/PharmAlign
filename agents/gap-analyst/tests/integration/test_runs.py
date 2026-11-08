"""Whole runs: contract request in, fake model, fake MCP tools with the server's shapes, result out."""
import asyncio

import httpx
import pytest
from fastapi import FastAPI
from pharmalign_agent_common import OutputInvalid, Settings, build_router
from pharmalign_agent_common.testing import FakeCase, FakeMcpServer, FakeModel, call_tool, say_json
from pharmalign_contracts.agents.run_agents import GapAnalysisRequest, GapAnalysisRun

from app.agent import SPEC
from app.analyst import analyse
from tests.conftest import CLAUSE_ANCHOR, CLAUSE_QUOTE, INJECTION, TOOLS, case, request_body

GAPS = {"candidates": [{
    "title": "No rule on when a foreign change triggers re-review",
    "kind": "policy",
    "signal_ids": ["s-2"],
    "pack_rule": "R3",
    "description": "No in-force source sets a trigger or a timeline. Check R3 verifies the approval once, and no pack rule says when to look again.",
    "global_examples": [{"anchor": CLAUSE_ANCHOR, "quote": CLAUSE_QUOTE}],
    "pipeline_node": "policy",
    "readiness_dimension": "AI policy and policy for AI",
    "suggested_next_step": "Review with the authority",
}]}
READ = [
    call_tool("get_gap_signals"),
    call_tool("get_pack_rule"),
    call_tool("read_clause", anchor=CLAUSE_ANCHOR),
]


async def run(body: dict, script: list, fake: FakeCase | None = None):
    model = FakeModel(script=script)
    with FakeMcpServer((fake or case()).tools(TOOLS)) as mcp:
        outcome = await analyse(GapAnalysisRequest.model_validate(body), settings=Settings(mcp_url=mcp.url), model=model)
    return outcome, model, mcp


async def test_a_run_returns_a_contract_valid_result_citing_returned_signals(headers):
    model = FakeModel(script=[*READ, say_json(GAPS)])
    with FakeMcpServer(case().tools(TOOLS)) as mcp:
        settings = Settings(mcp_url=mcp.url)

        async def start(request):
            return await analyse(request, settings=settings, model=model)

        app = FastAPI()
        app.include_router(build_router(SPEC, settings, start))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent") as client:
            assert (await client.post("/gap-analyses", json=request_body(), headers=headers)).status_code == 202
            for _ in range(300):
                polled = await client.get("/gap-analyses/tenant-gaps-g1-a1", headers=headers)
                if polled.json()["status"] not in ("queued", "running"):
                    break
                await asyncio.sleep(0.02)
    run_model = GapAnalysisRun.model_validate(polled.json())
    assert run_model.status == "completed", polled.json()
    [candidate] = run_model.result.candidates
    assert candidate.signal_ids == ["s-2"] and candidate.global_examples[0].anchor == CLAUSE_ANCHOR
    assert [c.name for c in mcp.calls] == ["get_gap_signals", "get_pack_rule", "read_clause"]
    assert {c.authorization for c in mcp.calls} == {"Bearer run-token-for-tests"}
    # Every contract tool but verify_quote: the agent is not given it (see app/agent.py).
    assert set(model.requests[0].tools_dict) - {"set_model_response"} == set(TOOLS) - {"verify_quote"}


async def test_an_invented_anchor_is_refused_retried_once_then_fails():
    invented = {"candidates": [{**GAPS["candidates"][0], "global_examples": [{"anchor": "F-01:v1:c999", "quote": CLAUSE_QUOTE}]}]}
    with pytest.raises(OutputInvalid, match="c999"):
        await run(request_body(), [*READ, say_json(invented), say_json(invented)])


async def test_an_invented_signal_id_is_refused_retried_once_then_fails():
    invented = {"candidates": [{**GAPS["candidates"][0], "signal_ids": ["s-77"]}]}
    with pytest.raises(OutputInvalid, match="s-77"):
        await run(request_body(), [*READ, say_json(invented), say_json(invented)])


async def test_a_refused_result_can_be_fixed_on_the_retry():
    invented = {"candidates": [{**GAPS["candidates"][0], "signal_ids": ["s-77"]}]}
    outcome, model, _ = await run(request_body(), [*READ, say_json(invented), say_json(GAPS)])
    assert outcome.result.candidates[0].signal_ids == ["s-2"]
    assert "s-77" in model.requests[-1].contents[-1].parts[0].text


async def test_signal_text_is_data_and_does_not_change_the_result():
    clean, _, _ = await run(request_body(), [*READ, say_json(GAPS)])
    injected, model, _ = await run(request_body(), [*READ, say_json(GAPS)], case(injected=True))
    assert injected.result == clean.result
    last = model.requests[-1]
    assert INJECTION not in last.config.system_instruction
    assert all(INJECTION not in (p.text or "") for c in last.contents for p in c.parts)
    assert any(p.function_response and INJECTION in str(p.function_response.response) for c in last.contents for p in c.parts)

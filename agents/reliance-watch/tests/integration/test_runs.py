"""Whole runs: contract request in, fake model, fake MCP tools with the server's shapes, result out."""
import asyncio

import httpx
import pytest
from fastapi import FastAPI
from pharmalign_agent_common import OutputInvalid, Settings, build_router
from pharmalign_agent_common.testing import FakeCase, FakeMcpServer, FakeModel, call_tool, say_json
from pharmalign_contracts.agents.run_agents import NoticeAssessmentRequest, NoticeAssessmentRun

from app.agent import SPEC
from app.watch import assess_notice
from tests.conftest import INJECTION, NOTICE_ANCHOR, NOTICE_QUOTE, TOOLS, case, request_body

ASSESSMENT = {
    "change_type": "suspension",
    "summary": "The reference approval was suspended. The licence relies on it and has the same strength.",
    "suggested_severity": "urgent",
    "candidates": [{"target_type": "licence", "target_id": "lic-7", "applies": "yes", "reason": "Same strength and holder."}],
    "additional_checks": ["M1"],
    "evidence": [{"anchor": NOTICE_ANCHOR, "quote": NOTICE_QUOTE}],
}
UNMATCHED = {
    "change_type": "other", "summary": "The notice names a product no licence or case relies on.", "suggested_severity": "review",
    "candidates": [{"target_type": "none", "applies": "unclear", "reason": "No licence or case relies on this approval."}],
    "additional_checks": [], "evidence": [],
}
READ = [
    call_tool("read_notice", notice_id="n-501"),
    call_tool("get_relied_on", regulator="Example Regulator"),
    call_tool("get_licence", licence_id="lic-7"),
]


async def run(body: dict, script: list, fake: FakeCase | None = None):
    model = FakeModel(script=script)
    with FakeMcpServer((fake or case()).tools(TOOLS)) as mcp:
        outcome = await assess_notice(NoticeAssessmentRequest.model_validate(body), settings=Settings(mcp_url=mcp.url), model=model)
    return outcome, model, mcp


async def test_a_run_returns_a_contract_valid_result_citing_the_notice(headers):
    model = FakeModel(script=[*READ, say_json(ASSESSMENT)])
    with FakeMcpServer(case().tools(TOOLS)) as mcp:
        settings = Settings(mcp_url=mcp.url)

        async def start(request):
            return await assess_notice(request, settings=settings, model=model)

        app = FastAPI()
        app.include_router(build_router(SPEC, settings, start))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent") as client:
            assert (await client.post("/notice-assessments", json=request_body(), headers=headers)).status_code == 202
            for _ in range(300):
                polled = await client.get("/notice-assessments/notice-501-r1-a1", headers=headers)
                if polled.json()["status"] not in ("queued", "running"):
                    break
                await asyncio.sleep(0.02)
    run_model = NoticeAssessmentRun.model_validate(polled.json())
    assert run_model.status == "completed", polled.json()
    result = run_model.result
    assert result.suggested_severity == "urgent" and result.candidates[0].applies == "yes"
    assert {e.anchor for e in result.evidence} == {NOTICE_ANCHOR}
    assert [c.name for c in mcp.calls] == ["read_notice", "get_relied_on", "get_licence"]
    assert {c.authorization for c in mcp.calls} == {"Bearer run-token-for-tests"}
    assert set(model.requests[0].tools_dict) - {"set_model_response"} == set(TOOLS)


async def test_an_invented_anchor_is_refused_retried_once_then_fails():
    invented = {**ASSESSMENT, "evidence": [{"anchor": "n-501:r1:c9", "quote": NOTICE_QUOTE}]}
    with pytest.raises(OutputInvalid, match="c9"):
        await run(request_body(), [*READ, say_json(invented), say_json(invented)])


async def test_lowering_the_severity_below_the_floor_is_refused():
    lowered = {**ASSESSMENT, "suggested_severity": "review"}  # the floor is urgent
    with pytest.raises(OutputInvalid, match="below the floor"):
        await run(request_body(), [*READ, say_json(lowered), say_json(lowered)])


async def test_a_notice_that_matches_nothing_is_kept_as_unmatched():
    body = request_body(candidates=[], severity_floor="review", open_case_checks=[])
    outcome, _, _ = await run(body, [call_tool("read_notice", notice_id="n-501"), say_json(UNMATCHED)])
    assert [(c.target_type, c.applies) for c in outcome.result.candidates] == [("none", "unclear")]


async def test_dropping_the_unmatched_notice_is_refused():
    body = request_body(candidates=[], severity_floor="review", open_case_checks=[])
    dropped = {**UNMATCHED, "candidates": []}
    with pytest.raises(OutputInvalid, match="unmatched|none"):
        await run(body, [call_tool("read_notice", notice_id="n-501"), say_json(dropped), say_json(dropped)])


async def test_notice_text_is_data_and_does_not_change_the_result():
    clean, _, _ = await run(request_body(), [*READ, say_json(ASSESSMENT)])
    injected, model, _ = await run(request_body(), [*READ, say_json(ASSESSMENT)], case(injected=True))
    assert injected.result == clean.result
    last = model.requests[-1]
    assert INJECTION not in last.config.system_instruction
    assert all(INJECTION not in (p.text or "") for c in last.contents for p in c.parts)
    assert any(p.function_response and INJECTION in str(p.function_response.response) for c in last.contents for p in c.parts)

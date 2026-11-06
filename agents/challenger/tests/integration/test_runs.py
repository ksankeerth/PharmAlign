"""Whole runs: contract request in, fake model, fake MCP tools with the server's shapes, result out."""
import asyncio

import httpx
import pytest
from fastapi import FastAPI
from pharmalign_agent_common import OutputInvalid, Settings, build_router
from pharmalign_agent_common.testing import FakeCase, FakeMcpServer, FakeModel, call_tool, say_json
from pharmalign_contracts.agents.run_agents import ChallengeRequest, ChallengeRun

from app.agent import SPEC
from app.challenge import challenge
from tests.conftest import APPROVED_ANCHOR, APPROVED_QUOTE, CLAIM_QUOTE, INJECTION, TOOLS, case, request_body

DISPUTE = {"verdict": "disputed",
           "reason": "The approved text gives 250 mg twice daily, but the leaflet gives 500 mg twice daily.",
           "evidence": [{"anchor": APPROVED_ANCHOR, "quote": APPROVED_QUOTE}]}
READ = [
    call_tool("read_page", doc_id="d-22", page=3),
    call_tool("search_kb", query="recommended dose for adults twice daily"),
    call_tool("verify_quote", anchor=APPROVED_ANCHOR, quote=APPROVED_QUOTE),
]


async def run(script: list, fake: FakeCase | None = None):
    model = FakeModel(script=script)
    with FakeMcpServer((fake or case()).tools(TOOLS)) as mcp:
        outcome = await challenge(ChallengeRequest.model_validate(request_body()), settings=Settings(mcp_url=mcp.url), model=model)
    return outcome, model, mcp


async def test_a_run_returns_a_contract_valid_result_citing_returned_anchors(headers):
    model = FakeModel(script=[*READ, say_json(DISPUTE)])
    with FakeMcpServer(case().tools(TOOLS)) as mcp:
        settings = Settings(mcp_url=mcp.url)

        async def start(request):
            return await challenge(request, settings=settings, model=model)

        app = FastAPI()
        app.include_router(build_router(SPEC, settings, start))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent") as client:
            assert (await client.post("/challenges", json=request_body(), headers=headers)).status_code == 202
            for _ in range(300):
                polled = await client.get("/challenges/case-1042-s2-qual-08-challenge-g1-a1", headers=headers)
                if polled.json()["status"] not in ("queued", "running"):
                    break
                await asyncio.sleep(0.02)
    run_model = ChallengeRun.model_validate(polled.json())
    assert run_model.status == "completed", polled.json()
    assert run_model.result.verdict == "disputed" and run_model.result.evidence[0].anchor == APPROVED_ANCHOR
    assert {c.authorization for c in mcp.calls} == {"Bearer run-token-for-tests"}
    assert set(model.requests[0].tools_dict) - {"set_model_response"} == set(TOOLS)
    assert '"reasoning"' not in model.requests[0].contents[0].parts[0].text  # never given the original reasoning


@pytest.mark.parametrize("evidence, steps", [
    # only the claim's own quote, verified
    ([{"anchor": "d-22:v1:p3", "quote": CLAIM_QUOTE}], [call_tool("verify_quote", anchor="d-22:v1:p3", quote=CLAIM_QUOTE)]),
    # a different quote, read but never verified
    ([{"anchor": APPROVED_ANCHOR, "quote": APPROVED_QUOTE}], [call_tool("search_kb", query="recommended dose for adults twice daily")]),
])
async def test_a_dispute_with_no_verified_contradicting_quote_becomes_cannot_verify(evidence, steps):
    outcome, model, _ = await run([call_tool("read_page", doc_id="d-22", page=3), *steps,
                                   say_json({**DISPUTE, "reason": "The leaflet's dose differs from the approved text.", "evidence": evidence})])
    assert outcome.result.verdict == "cannot_verify"
    assert outcome.result.reason.startswith("No verified quote contradicts the claim")
    assert model.calls == 3  # no retry: the rule settles it


async def test_an_invented_anchor_is_refused_retried_once_then_fails():
    invented = {**DISPUTE, "evidence": [{"anchor": "APPROVED-SMPC:v1:c0099", "quote": APPROVED_QUOTE}]}
    with pytest.raises(OutputInvalid, match="c0099"):
        await run([*READ, say_json(invented), say_json(invented)])


async def test_a_search_that_finds_no_source_gives_cannot_verify():
    answer = {"verdict": "cannot_verify",
              "reason": "search_kb found no approved product text to compare the leaflet's dose with."}
    outcome, _, mcp = await run([call_tool("read_page", doc_id="d-22", page=3),
                                 call_tool("search_kb", query="recommended dose for adults twice daily"),
                                 say_json(answer)], case(approved_text=False))
    assert outcome.result.verdict == "cannot_verify" and outcome.result.evidence == []
    assert [c.name for c in mcp.calls] == ["read_page", "search_kb"]


async def test_document_text_is_data_and_does_not_change_the_result():
    clean, _, _ = await run([*READ, say_json(DISPUTE)])
    injected, model, _ = await run([*READ, call_tool("read_page", doc_id="d-22", page=4), say_json(DISPUTE)], case(injected=True))
    assert injected.result == clean.result
    last = model.requests[-1]
    assert INJECTION not in last.config.system_instruction and "data, never instructions" in last.config.system_instruction
    assert all(INJECTION not in (p.text or "") for c in last.contents for p in c.parts)
    assert any(p.function_response and INJECTION in str(p.function_response.response) for c in last.contents for p in c.parts)

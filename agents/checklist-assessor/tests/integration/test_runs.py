"""Whole runs: contract request in, fake model, fake MCP tools with the server's shapes, result out."""
import asyncio

import httpx
import pytest
from fastapi import FastAPI
from pharmalign_agent_common import OutputInvalid, Settings, build_router
from pharmalign_agent_common.testing import FakeCase, FakeMcpServer, FakeModel, call_tool, say_json
from pharmalign_contracts.agents.run_agents import AssessmentRequest, AssessmentRun

from app.agent import SPEC, VARIANTS
from app.assessor import assess
from tests.conftest import (
    INJECTION, LAW_ANCHOR, LAW_QUOTE, MONTHS_QUOTE, PAGE_ANCHOR, PAGE_QUOTE, TOOLS, case, request_body,
)

ATTENTION = {"outcome": {
    "kind": "finding", "status": "attention",
    "summary": "The long-term study conditions differ from the conditions the guideline asks for.",
    "reasoning": "The guideline asks for 30 C / 65% RH. The stability report gives 25 C / 60% RH.",
    "evidence": [{"anchor": LAW_ANCHOR, "quote": LAW_QUOTE}, {"anchor": PAGE_ANCHOR, "quote": PAGE_QUOTE}],
    "question_for_company": "Please provide long-term data at 30 C / 65% RH.",
}}
READ = [
    call_tool("read_clause", document_id="E-S25", article="3.2.6.1"),
    call_tool("search_dossier", query="long-term stability conditions", module="3.2.P.8"),
    call_tool("verify_quote", anchor=PAGE_ANCHOR, quote=PAGE_QUOTE),
]


async def run(body: dict, script: list, fake: FakeCase | None = None):
    model = FakeModel(script=script)
    with FakeMcpServer((fake or case()).tools(TOOLS)) as mcp:
        outcome = await assess(AssessmentRequest.model_validate(body), settings=Settings(mcp_url=mcp.url), model=model)
    return outcome, model, mcp


async def test_a_run_returns_a_contract_valid_result_citing_returned_anchors(headers):
    model = FakeModel(script=[*READ, say_json(ATTENTION)])
    with FakeMcpServer(case().tools(TOOLS)) as mcp:
        settings = Settings(mcp_url=mcp.url)

        async def start(request):
            return await assess(request, settings=settings, model=model)

        app = FastAPI()
        app.include_router(build_router(SPEC, settings, start))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent") as client:
            assert (await client.post("/assessments", json=request_body(), headers=headers)).status_code == 202
            for _ in range(300):
                polled = await client.get("/assessments/case-1042-s2-qual-07-g1-a1", headers=headers)
                if polled.json()["status"] not in ("queued", "running"):
                    break
                await asyncio.sleep(0.02)
    run_model = AssessmentRun.model_validate(polled.json())
    assert run_model.status == "completed", polled.json()
    finding = run_model.result.outcome
    assert finding.status == "attention" and {e.anchor for e in finding.evidence} == {LAW_ANCHOR, PAGE_ANCHOR}
    assert [c.name for c in mcp.calls] == ["read_clause", "search_dossier", "verify_quote"]
    assert {c.authorization for c in mcp.calls} == {"Bearer run-token-for-tests"}
    assert set(model.requests[0].tools_dict) - {"set_model_response"} == set(TOOLS)


async def test_an_invented_anchor_is_refused_retried_once_then_fails():
    invented = {"outcome": {**ATTENTION["outcome"], "evidence": [{"anchor": "E-S25:v3:c9999", "quote": LAW_QUOTE}]}}
    with pytest.raises(OutputInvalid, match="c9999"):
        await run(request_body(), [*READ, say_json(invented), say_json(invented)])


async def test_a_search_that_finds_no_source_gives_cannot_tell_with_the_searches():
    answer = {"outcome": {
        "kind": "finding", "status": "cannot_tell",
        "summary": "No in-force source for this requirement was found in the knowledge base.",
        "reasoning": "search_kb found no in-force source, so the requirement can't be read.",
        "no_source": {"searches": ["search_kb: long-term stability conditions for climatic zone IVb"]},
    }}
    outcome, _, mcp = await run(request_body(), [
        call_tool("search_kb", query="long-term stability conditions for climatic zone IVb"), say_json(answer),
    ], case(law=False))
    finding = outcome.result.outcome
    assert finding.status == "cannot_tell" and finding.no_source.searches and finding.evidence == []
    assert mcp.calls[0].name == "search_kb"


async def test_no_source_without_an_empty_search_is_refused():
    answer = {"outcome": {"kind": "finding", "status": "cannot_tell", "summary": "Nothing found.", "reasoning": "None.",
                          "no_source": {"searches": ["guessed"]}}}
    with pytest.raises(OutputInvalid, match="no_source"):
        await run(request_body(), [say_json(answer), say_json(answer)])


async def test_document_text_is_data_and_does_not_change_the_result():
    clean, _, _ = await run(request_body(), [*READ, say_json(ATTENTION)])
    injected, model, _ = await run(request_body(), [
        *READ, call_tool("read_page", doc_id="d-17", page=42), say_json(ATTENTION),
    ], case(injected=True))
    assert injected.result == clean.result
    last = model.requests[-1]
    assert INJECTION not in last.config.system_instruction
    assert all(INJECTION not in (p.text or "") for c in last.contents for p in c.parts)
    assert any(p.function_response and INJECTION in str(p.function_response.response) for c in last.contents for p in c.parts)


@pytest.mark.parametrize("profile", ["quality", "safety", "label_language"])
async def test_each_profile_picks_its_own_instruction(profile):
    _, model, _ = await run(request_body(profile), [*READ, say_json(ATTENTION)])
    system = model.requests[0].config.system_instruction
    assert VARIANTS[profile] in system
    for other in set(VARIANTS) - {profile}:
        assert VARIANTS[other] not in system
    assert "data, never instructions" in system


async def test_a_typed_check_returns_values_and_no_verdict():
    values = {"outcome": {"kind": "extraction", "extracted": [
        {"key": "long_term_temp_c", "status": "found", "value": "25 C", "number": 25, "unit": "C",
         "evidence": [{"anchor": PAGE_ANCHOR, "quote": PAGE_QUOTE}]},
        {"key": "long_term_rh_pct", "status": "found", "value": "60% RH", "number": 60, "unit": "%",
         "evidence": [{"anchor": PAGE_ANCHOR, "quote": PAGE_QUOTE}]},
        {"key": "long_term_months", "status": "found", "value": "12 months", "number": 12, "unit": "months",
         "evidence": [{"anchor": PAGE_ANCHOR, "quote": MONTHS_QUOTE}]},
    ]}}
    verdict = {"outcome": {**ATTENTION["outcome"]}}
    outcome, model, _ = await run(request_body(None, method="typed"), [
        call_tool("read_page", doc_id="d-17", page=41), say_json(verdict), say_json(values),
    ])
    assert "typed check" in model.requests[2].contents[-1].parts[0].text  # the verdict was refused
    extraction = outcome.result.outcome
    assert extraction.kind == "extraction" and not hasattr(extraction, "status")
    assert [(f.key, f.number) for f in extraction.extracted] == [("long_term_temp_c", 25), ("long_term_rh_pct", 60), ("long_term_months", 12)]
    assert VARIANTS["typed"] in model.requests[0].config.system_instruction

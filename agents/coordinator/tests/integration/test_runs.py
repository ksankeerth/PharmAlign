"""Whole runs: contract request in, fake model, fake MCP tools with the server's shapes, result out."""
import asyncio

import httpx
import pytest
from fastapi import FastAPI
from pharmalign_agent_common import OutputInvalid, Settings, build_router
from pharmalign_agent_common.testing import FakeCase, FakeMcpServer, FakeModel, call_tool, say_json
from pharmalign_contracts.agents.run_agents import BriefRequest, BriefRun

from app.agent import SPEC
from app.coordinator import write_brief
from tests.conftest import FINGERPRINT, INJECTION, TOOLS, case, request_body

BRIEF = {
    "input_fingerprint": FINGERPRINT,
    "recommendation": {"choice": "hold_ask_company", "reason": "Shelf life differs from the reference approval.", "reason_evidence": ["M1"]},
    "summary": "One conflict needs a decision: M1 shows a shelf life that differs from the reference approval.",
    "draft_questions": [{"check_ids": ["M1"], "texts": {
        "en": "Please explain the shelf life you propose, which differs from the reference approval.",
        "ar": "Please explain the shelf life (ar)."}}],
}
READ = [call_tool("get_findings"), call_tool("get_conflicts")]


async def run(body: dict, script: list, fake: FakeCase | None = None):
    model = FakeModel(script=script)
    with FakeMcpServer((fake or case()).tools(TOOLS)) as mcp:
        outcome = await write_brief(BriefRequest.model_validate(body), settings=Settings(mcp_url=mcp.url), model=model)
    return outcome, model, mcp


async def test_a_run_returns_a_contract_valid_brief_with_the_fingerprint_unchanged(headers):
    model = FakeModel(script=[*READ, say_json(BRIEF)])
    with FakeMcpServer(case().tools(TOOLS)) as mcp:
        settings = Settings(mcp_url=mcp.url)

        async def start(request):
            return await write_brief(request, settings=settings, model=model)

        app = FastAPI()
        app.include_router(build_router(SPEC, settings, start))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent") as client:
            assert (await client.post("/briefs", json=request_body(), headers=headers)).status_code == 202
            for _ in range(300):
                polled = await client.get("/briefs/case-1042-s2-brief-g1-a1", headers=headers)
                if polled.json()["status"] not in ("queued", "running"):
                    break
                await asyncio.sleep(0.02)
    run_model = BriefRun.model_validate(polled.json())
    assert run_model.status == "completed", polled.json()
    assert run_model.result.input_fingerprint == FINGERPRINT
    assert run_model.result.recommendation.choice == "hold_ask_company"
    assert [c.name for c in mcp.calls] == ["get_findings", "get_conflicts"]
    assert {c.authorization for c in mcp.calls} == {"Bearer run-token-for-tests"}
    assert set(model.requests[0].tools_dict) - {"set_model_response"} == set(TOOLS)
    assert "data, never instructions" in model.requests[0].config.system_instruction


async def test_a_question_tied_to_an_invented_check_is_refused_retried_once_then_fails():
    invented = {**BRIEF, "draft_questions": [{"check_ids": ["Z9"], "texts": BRIEF["draft_questions"][0]["texts"]}]}
    with pytest.raises(OutputInvalid, match="Z9"):
        await run(request_body(), [*READ, say_json(invented), say_json(invented)])


async def test_a_changed_fingerprint_fails_the_run():
    changed = {**BRIEF, "input_fingerprint": "sha256:0bad"}
    with pytest.raises(OutputInvalid, match="copied unchanged"):
        await run(request_body(), [*READ, say_json(changed), say_json(changed)])


async def test_a_refused_brief_can_be_fixed_on_the_retry():
    changed = {**BRIEF, "input_fingerprint": "sha256:0bad"}
    outcome, model, _ = await run(request_body(), [*READ, say_json(changed), say_json(BRIEF)])
    assert outcome.result.input_fingerprint == FINGERPRINT
    assert "copied unchanged" in model.requests[-1].contents[-1].parts[0].text


async def test_finding_text_is_data_and_does_not_change_the_result():
    clean, _, _ = await run(request_body(), [*READ, say_json(BRIEF)])
    injected, model, _ = await run(request_body(), [*READ, say_json(BRIEF)], case(injected=True))
    assert injected.result == clean.result
    last = model.requests[-1]
    assert INJECTION not in last.config.system_instruction
    assert all(INJECTION not in (p.text or "") for c in last.contents for p in c.parts)
    assert any(p.function_response and INJECTION in str(p.function_response.response) for c in last.contents for p in c.parts)


async def test_a_choice_that_was_not_offered_is_refused():
    other = {**BRIEF, "recommendation": {"choice": "approve", "reason": "Looks fine.", "reason_evidence": ["M1"]}}
    with pytest.raises(OutputInvalid, match="wasn't offered"):
        await run(request_body(), [*READ, say_json(other), say_json(other)])


def _texts_schema(request) -> dict:
    """The question-texts schema in the answer tool the model is offered."""
    declaration = request.tools_dict["set_model_response"]._get_declaration()
    return declaration.parameters_json_schema["$defs"]["QuestionTexts"]


@pytest.mark.parametrize("languages", [["en"], ["en", "ar"]])
async def test_the_model_is_told_each_output_language_by_name(languages):
    # Gemini fills a map with open keys as {}, so every brief with a question was refused. The schema names each language.
    brief = {**BRIEF, "draft_questions": [{"check_ids": ["M1"], "texts": {code: f"Question ({code})." for code in languages}}]}
    outcome, model, _ = await run(request_body(output_languages=languages), [call_tool("set_model_response", **brief)])
    schema = _texts_schema(model.requests[0])
    assert set(schema["properties"]) == set(languages) and set(schema["required"]) == set(languages)
    assert schema["additionalProperties"] is False
    assert set(outcome.result.draft_questions[0].texts) == set(languages)


async def test_an_answer_with_a_language_left_out_is_sent_back_and_can_be_fixed():
    # The answer tool checks the run's schema, so the model hears what is missing and answers again.
    partial = {**BRIEF, "draft_questions": [{"check_ids": ["M1"], "texts": {"en": "Question?"}}]}
    outcome, model, _ = await run(request_body(output_languages=["en", "ar"]),
                                  [call_tool("set_model_response", **partial), call_tool("set_model_response", **BRIEF)])
    assert model.calls == 2
    assert set(outcome.result.draft_questions[0].texts) == {"en", "ar"}


async def test_a_repeated_identical_call_is_not_sent_again():
    outcome, model, mcp = await run(request_body(output_languages=["en", "ar"]),
                                    [call_tool("get_findings"), call_tool("get_findings"), call_tool("get_findings", status=["look"]), say_json(BRIEF)])
    assert [c.name for c in mcp.calls] == ["get_findings", "get_findings"]  # the repeat was answered without a call; other arguments go through
    assert "write the brief now" in str(model.requests[2].contents[-1])
    assert outcome.usage.tool_calls == 2


async def test_the_coordinator_stops_at_its_own_model_call_ceiling():
    from pharmalign_agent_common import BudgetExceeded

    body = request_body(output_languages=["en", "ar"])
    body["budget"] = {"max_model_calls": 40, "max_tool_calls": 80, "deadline_seconds": 600}
    script = [call_tool("get_findings", status=[f"s{i}"]) for i in range(20)]
    with pytest.raises(BudgetExceeded, match="more than 12 model calls"):
        await run(body, script)

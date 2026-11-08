"""force_answer: after a repeated call, and on the last model call the budget allows, the model is sent with function calling off."""
from google.genai import types
from pharmalign_agent_common import agent_spec, run_agent
from pharmalign_agent_common.testing import FakeCase, FakeMcpServer, FakeModel, Page, call_tool, say_json
from pharmalign_contracts.agents.run_agents import DossierReadRequest, DossierReadResult

from conftest import PAGE_QUOTE, good_result, make_body

CASE = FakeCase(pages=[Page("d-17", 2, f"The product has {PAGE_QUOTE} below 25 C."), Page("d-17", 3, "Nothing here.")])
READ = call_tool("read_page", doc_id="d-17", page=2)
OTHER = call_tool("read_page", doc_id="d-17", page=3)


def spec(**options):
    return agent_spec("dossier-reader", adk_name="dossier_reader", result_model=DossierReadResult, instruction="Read.",
                      default_model="m", build_message=lambda r: "go", **options)


def tools_off(request) -> bool:
    config = request.config.tool_config if request.config else None
    return bool(config and config.function_calling_config and config.function_calling_config.mode == types.FunctionCallingConfigMode.NONE)


async def run(s, script, mcp_settings, **budget):
    with FakeMcpServer(CASE.tools(s.tools)) as server:
        model = FakeModel(script=script)
        outcome = await run_agent(s, DossierReadRequest.model_validate(make_body(**budget)), settings=type(mcp_settings)(mcp_url=server.url), model=model)
    return outcome, model


async def test_after_a_repeated_call_the_next_model_call_has_no_tools(mcp_settings):
    s = spec(repeat_answer="Answer now.", force_answer=True)
    outcome, model = await run(s, [READ, READ, say_json(good_result())], mcp_settings)
    assert outcome.result.facts[0].number == 36
    assert [tools_off(r) for r in model.requests] == [False, False, True]


async def test_the_last_model_call_the_budget_allows_has_no_tools(mcp_settings):
    s = spec(force_answer=True, max_model_calls=3)
    outcome, model = await run(s, [READ, OTHER, say_json(good_result())], mcp_settings)
    assert outcome.result.facts[0].number == 36
    assert [tools_off(r) for r in model.requests] == [False, False, True]


async def test_without_force_answer_every_call_may_use_tools(mcp_settings):
    s = spec(repeat_answer="Answer now.", max_model_calls=3)
    _, model = await run(s, [READ, READ, say_json(good_result())], mcp_settings)
    assert [tools_off(r) for r in model.requests] == [False, False, False]

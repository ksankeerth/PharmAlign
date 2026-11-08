import json

import pytest
from pharmalign_agent_common import BudgetExceeded, OutputInvalid, OutputTruncated, create_app, run_agent
from pharmalign_agent_common.testing import FakeModel, call_tool, cut_off, say, say_json
from pharmalign_contracts.agents.run_agents import DossierReadRequest

from conftest import good_result, make_body
from test_routes import client_for, finished


def request(**budget) -> DossierReadRequest:
    return DossierReadRequest.model_validate(make_body(**budget))


def read_then_answer(answer) -> list:
    return [
        call_tool("read_page", doc_id="d-17", page=2),
        call_tool("verify_quote", anchor="d-17:v1:p2", quote="a shelf life of 36 months"),
        answer,
    ]


async def test_run_reads_through_mcp_and_returns_a_valid_result(spec, mcp, mcp_settings):
    model = FakeModel(script=read_then_answer(say_json(good_result())))
    outcome = await run_agent(spec, request(), settings=mcp_settings, model=model)
    assert outcome.result.facts[0].number == 36
    assert [c.name for c in mcp.calls] == ["read_page", "verify_quote"]
    assert outcome.usage.model_calls == 3 and outcome.usage.tool_calls == 2
    assert outcome.usage.input_tokens == 300 and len(outcome.model_calls) == 3
    assert outcome.tool_errors == []


async def test_structured_answer_through_adks_response_tool(spec, mcp_settings):
    # With tools and an output schema, ADK has the model answer by calling set_model_response.
    # That call is not one of the agent's tool calls and doesn't use the tool budget.
    model = FakeModel(script=[call_tool("set_model_response", **good_result())])
    outcome = await run_agent(spec, request(max_tool_calls=0), settings=mcp_settings, model=model)
    assert outcome.result.facts[0].number == 36 and outcome.usage.tool_calls == 0


async def test_invalid_output_retries_once_with_the_error(spec, mcp_settings):
    model = FakeModel(script=[say("not json at all"), say_json(good_result())])
    outcome = await run_agent(spec, request(), settings=mcp_settings, model=model)
    assert outcome.usage.model_calls == 2
    retry = model.requests[1].contents[-1].parts[0].text
    assert "rejected" in retry and "corrected JSON" in retry


async def test_rule_breaking_output_retries_too(spec, mcp_settings):
    bad = good_result(key="not_asked_for")
    model = FakeModel(script=[say_json(bad), say_json(good_result())])
    outcome = await run_agent(spec, request(), settings=mcp_settings, model=model)
    assert outcome.result.facts[0].key == "shelf_life_months"
    assert "wasn't asked for" in model.requests[1].contents[-1].parts[0].text


async def test_second_invalid_output_fails_with_no_third_try(spec, mcp_settings):
    model = FakeModel(script=[say("{}"), say('{"facts": 3}')])
    with pytest.raises(OutputInvalid):
        await run_agent(spec, request(), settings=mcp_settings, model=model)
    assert model.calls == 2


async def test_fenced_json_is_accepted(spec, mcp_settings):
    model = FakeModel(script=[say("```json\n" + json.dumps(good_result()) + "\n```")])
    outcome = await run_agent(spec, request(), settings=mcp_settings, model=model)
    assert outcome.result.facts[0].key == "shelf_life_months"


async def test_length_fails_without_retry(spec, mcp_settings):
    model = FakeModel(script=[cut_off(), say_json(good_result())])
    with pytest.raises(OutputTruncated):
        await run_agent(spec, request(), settings=mcp_settings, model=model)
    assert model.calls == 1


async def test_model_call_budget_is_enforced(spec, mcp, mcp_settings):
    model = FakeModel(script=read_then_answer(say_json(good_result())))
    with pytest.raises(BudgetExceeded):
        await run_agent(spec, request(max_model_calls=2), settings=mcp_settings, model=model)
    assert model.calls == 2  # the third call never reached the model


async def test_tool_call_budget_is_enforced(spec, mcp, mcp_settings):
    model = FakeModel(script=read_then_answer(say_json(good_result())))
    with pytest.raises(BudgetExceeded):
        await run_agent(spec, request(max_tool_calls=1), settings=mcp_settings, model=model)
    assert len(mcp.calls) == 1  # the second tool never ran


async def test_retry_counts_against_the_model_budget(spec, mcp_settings):
    model = FakeModel(script=[say("bad"), say_json(good_result())])
    with pytest.raises(BudgetExceeded):
        await run_agent(spec, request(max_model_calls=1), settings=mcp_settings, model=model)


async def test_failing_tool_is_reported_and_run_continues(spec, mcp_settings):
    from pharmalign_agent_common.testing import FakeMcpServer

    def broken(args):
        raise RuntimeError("store is down")

    with FakeMcpServer({"read_page": broken}) as server:
        settings = type(mcp_settings)(mcp_url=server.url)
        model = FakeModel(script=[call_tool("read_page", doc_id="d-17", page=2), say_json(good_result())])
        outcome = await run_agent(spec, request(), settings=settings, model=model)
    assert outcome.tool_errors and outcome.tool_errors[0].tool == "read_page"


async def test_a_not_found_answer_is_not_counted_as_a_tool_failure(spec, mcp_settings):
    from pharmalign_agent_common.testing import FakeMcpServer

    def nothing_there(args):
        raise RuntimeError(json.dumps({"tool": "read_page", "code": "not_found", "message": "No such page.", "retryable": False}))

    with FakeMcpServer({"read_page": nothing_there}) as server:
        settings = type(mcp_settings)(mcp_url=server.url)
        model = FakeModel(script=[call_tool("read_page", doc_id="d-17", page=9), say_json(good_result())])
        outcome = await run_agent(spec, request(), settings=settings, model=model)
    assert outcome.tool_errors == []


async def test_a_call_with_invalid_arguments_is_not_counted_as_a_tool_failure(spec, mcp_settings):
    """The model's own bad call (a one-letter search) is refused with the reason, and the run goes on. It isn't the tool failing."""
    from pharmalign_agent_common.testing import FakeMcpServer

    def refused(args):
        raise RuntimeError(json.dumps({"tool": "search_dossier", "code": "invalid_arguments",
                                       "message": "Invalid arguments: ('query',) String should have at least 2 characters", "retryable": False}))

    with FakeMcpServer({"search_dossier": refused}) as server:
        settings = type(mcp_settings)(mcp_url=server.url)
        model = FakeModel(script=[call_tool("search_dossier", query="a"), say_json(good_result())])
        outcome = await run_agent(spec, request(), settings=settings, model=model)
    assert outcome.tool_errors == []


async def test_tools_outside_the_contract_list_are_not_offered(spec, mcp_settings):
    from pharmalign_agent_common.testing import FakeMcpServer

    with FakeMcpServer({"read_page": {}, "delete_everything": {}}) as server:
        settings = type(mcp_settings)(mcp_url=server.url)
        model = FakeModel(script=[say_json(good_result())])
        await run_agent(spec, request(), settings=settings, model=model)
    offered = set(model.requests[0].tools_dict)
    assert "read_page" in offered and "delete_everything" not in offered


async def test_failures_reach_the_poll_as_clean_failures(spec, mcp_settings, headers):
    cases = {
        "run-length": ([cut_off()], "failed", "length", "output_truncated"),
        "run-invalid": ([say("{}"), say("{}")], "failed", "error", "invalid_output"),
        "run-budget": ([call_tool("read_page", doc_id="d-17", page=1)] * 3, "stopped_early", "budget", None),
    }
    for run_id, (script, status, finish, code) in cases.items():
        model = FakeModel(script=list(script))

        async def run(req, model=model):
            return await run_agent(spec, req, settings=mcp_settings, model=model)

        async with client_for(create_app(spec, mcp_settings, run)) as client:
            body = make_body(run_id, max_tool_calls=1) if run_id == "run-budget" else make_body(run_id)
            await client.post("/dossier-reads", json=body, headers=headers)
            polled = (await finished(client, headers, run_id)).json()
        assert (polled["status"], polled["finish_reason"], polled["result"]) == (status, finish, None)
        assert (polled["error"] or {}).get("code") == code

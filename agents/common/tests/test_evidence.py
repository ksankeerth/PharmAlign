import json

import pytest
from pharmalign_agent_common import OutputInvalid, ToolLog, agent_spec, check_evidence, run_agent
from pharmalign_agent_common.evidence import numbers_in, unquoted_numbers, value_in_quotes
from pharmalign_agent_common.testing import FakeCase, FakeMcpServer, FakeModel, Page, call_tool, say_json
from pharmalign_contracts.agents.run_agents import DossierReadRequest, DossierReadResult
from pharmalign_contracts.common import Evidence

from conftest import PAGE_QUOTE, good_result, make_body

CASE = FakeCase(pages=[Page("d-17", 2, f"The product has {PAGE_QUOTE} below 25 C.")])


def mcp_response(value) -> dict:
    """What ADK hands the callback for an MCP result."""
    return {"content": [{"type": "text", "text": json.dumps(value)}], "isError": False}


def test_log_keeps_anchors_and_text_from_real_tool_shapes():
    log = ToolLog()
    log.record("read_page", {"doc_id": "d-17", "page": 2}, mcp_response(CASE.read_page({"doc_id": "d-17", "page": 2})))
    ev = Evidence(anchor="d-17:v1:p2", quote=PAGE_QUOTE)
    assert log.returned("d-17:v1:p2") and log.confirms(ev)
    assert not log.confirms(Evidence(anchor="d-17:v1:p2", quote="a shelf life of 60 months"))


def test_verify_quote_echo_is_not_a_source():
    log = ToolLog()
    log.record("verify_quote", {"anchor": "d-99:v1:p1", "quote": "x y"}, mcp_response({"anchor": "d-99:v1:p1", "verified": False}))
    assert not log.returned("d-99:v1:p1")
    with pytest.raises(ValueError, match="wasn't returned"):
        check_evidence(DossierReadResult.model_validate(good_result()), log)


def test_failed_tool_returns_no_anchors():
    log = ToolLog()
    log.record("read_page", {}, {"content": [{"type": "text", "text": '{"anchor": "d-17:v1:p2"}'}], "isError": True})
    assert not log.returned("d-17:v1:p2") and log.calls[0].failed


def test_empty_searches_are_listed():
    log = ToolLog()
    log.record("search_dossier", {"query": "shelf life"}, mcp_response(CASE.search_dossier({"query": "colour"})))
    assert log.empty_searches() == ["search_dossier: shelf life"]


def test_numbers():
    assert numbers_in("36 months at 25 °C / 60% RH, 1,000 mg") == [36.0, 25.0, 60.0, 1000.0]
    assert unquoted_numbers("36 or 24 months", ["a shelf life of 36 months"]) == [24.0]
    assert value_in_quotes("36 months", 36, [PAGE_QUOTE]) and not value_in_quotes("x", 24, [PAGE_QUOTE])


async def test_rules_with_a_log_see_what_the_tools_returned(mcp_settings):
    seen = []

    def rules(request, result, log):
        seen.append(set(log.sources))
        check_evidence(result, log)

    spec = agent_spec("dossier-reader", adk_name="dossier_reader", result_model=DossierReadResult, instruction="Read.",
                      default_model="m", build_message=lambda r: "go", check_result=rules)
    with FakeMcpServer(CASE.tools(spec.tools)) as server:
        settings = type(mcp_settings)(mcp_url=server.url)
        model = FakeModel(script=[call_tool("read_page", doc_id="d-17", page=2), say_json(good_result())])
        outcome = await run_agent(spec, DossierReadRequest.model_validate(make_body()), settings=settings, model=model)
        assert outcome.result.facts[0].number == 36 and seen == [{"d-17:v1:p2"}]
        # without reading, the same answer cites an anchor no tool returned: refused twice, then failed
        model = FakeModel(script=[say_json(good_result()), say_json(good_result())])
        with pytest.raises(OutputInvalid, match="wasn't returned"):
            await run_agent(spec, DossierReadRequest.model_validate(make_body()), settings=settings, model=model)


async def test_a_rule_can_replace_the_result(mcp_settings):
    def rules(request, result, log):
        return result.model_copy(update={"issues": []})

    spec = agent_spec("dossier-reader", adk_name="dossier_reader", result_model=DossierReadResult, instruction="Read.",
                      default_model="m", build_message=lambda r: "go", check_result=rules)
    answer = {**good_result(), "issues": [{"doc_id": "d-17", "kind": "other", "detail": "x"}]}
    outcome = await run_agent(spec, DossierReadRequest.model_validate(make_body()), settings=mcp_settings,
                              model=FakeModel(script=[say_json(answer)]))
    assert outcome.result.issues == []


async def test_a_variant_is_picked_per_run_and_hashed(mcp_settings):
    spec = agent_spec("dossier-reader", adk_name="dossier_reader", result_model=DossierReadResult, instruction="Base.",
                      default_model="m", build_message=lambda r: "go", variants={"a": "Variant A.", "b": "Variant B."},
                      pick_variant=lambda r: "b")
    model = FakeModel(script=[say_json(good_result())])
    await run_agent(spec, DossierReadRequest.model_validate(make_body()), settings=mcp_settings, model=model)
    system = model.requests[0].config.system_instruction
    assert "Base." in system and "Variant B." in system and "Variant A." not in system
    other = agent_spec("dossier-reader", adk_name="dossier_reader", result_model=DossierReadResult, instruction="Base.",
                       default_model="m", build_message=lambda r: "go", variants={"a": "Variant A.", "b": "Changed."},
                       pick_variant=lambda r: "b")
    assert spec.prompt_hash != other.prompt_hash

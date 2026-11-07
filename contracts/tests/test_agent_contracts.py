"""The contract rules that protect the design. Run from contracts/: python -m pytest tests -q"""
import copy
import re
import sys
from pathlib import Path

import pytest
import yaml
from openapi_spec_validator import validate as validate_openapi
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import generate_openapi  # noqa: E402
from pharmalign_contracts.agents import knowledge_assistant as ka  # noqa: E402
from pharmalign_contracts.agents import run_agents as ag  # noqa: E402
from pharmalign_contracts.common import FactValue  # noqa: E402

BY_NAME = {a.name: a for a in ag.RUN_AGENTS}
RUN_IDS = [a.name for a in ag.RUN_AGENTS]
SPEC_NAMES = [*RUN_IDS, "knowledge-assistant"]


def spec(name):
    return yaml.safe_load((ROOT / "openapi" / "agents" / f"{name}.yaml").read_text(encoding="utf-8"))


def result_of(name, **changes):
    data = copy.deepcopy(BY_NAME[name].result_example)
    data.update(changes)
    return data


@pytest.mark.parametrize("name", RUN_IDS)
def test_examples_match_the_contract(name):
    agent = BY_NAME[name]
    agent.request.model_validate(agent.request_example)
    agent.run.model_validate(ag.poll_example(agent))


@pytest.mark.parametrize("name", RUN_IDS)
def test_unknown_fields_are_refused(name):
    agent = BY_NAME[name]
    with pytest.raises(ValidationError):
        agent.request.model_validate({**agent.request_example, "surprise": 1})
    with pytest.raises(ValidationError):
        agent.run.model_validate({**ag.poll_example(agent), "surprise": 1})


@pytest.mark.parametrize("name", RUN_IDS)
def test_gate_off_is_refused_outside_simulation(name):
    agent = BY_NAME[name]
    body = copy.deepcopy(agent.request_example)
    body["execution_profile"]["gate"] = "off"
    agent.request.model_validate(body)
    body["mode"] = "live"
    body["execution_profile"]["data_class"] = "confidential"
    with pytest.raises(ValidationError):
        agent.request.model_validate(body)


def test_live_run_cant_use_synthetic_data_class():
    agent = BY_NAME["coordinator"]
    with pytest.raises(ValidationError):
        agent.request.model_validate({**agent.request_example, "mode": "live"})


@pytest.mark.parametrize(
    "changes",
    [
        {"finish_reason": "length"},  # a cut-off answer can't be a result
        {"result": None},
        {"error": {"code": "x", "message": "y", "retryable": False}},
    ],
)
def test_completed_run_rules(changes):
    agent = BY_NAME["challenger"]
    with pytest.raises(ValidationError):
        agent.run.model_validate({**ag.poll_example(agent), **changes})


def test_failed_run_has_no_result():
    agent = BY_NAME["challenger"]
    bad = {**ag.poll_example(agent), "status": "failed", "finish_reason": "error",
           "error": {"code": "internal_error", "message": "y", "retryable": True}}
    with pytest.raises(ValidationError):
        agent.run.model_validate(bad)
    agent.run.model_validate({**bad, "result": None})


def test_run_failure_codes_are_named():
    agent = BY_NAME["challenger"]
    failed = {**ag.poll_example(agent), "status": "failed", "finish_reason": "error", "result": None,
              "error": {"code": "invalid_output", "message": "y", "retryable": True}}
    for code in ("invalid_output", "output_truncated", "internal_error", "budget_exceeded"):
        agent.run.model_validate({**failed, "error": {**failed["error"], "code": code}})
    with pytest.raises(ValidationError):
        agent.run.model_validate({**failed, "error": {**failed["error"], "code": "something_else"}})


def test_unfinished_run_has_no_result():
    agent = BY_NAME["challenger"]
    with pytest.raises(ValidationError):
        agent.run.model_validate({**ag.poll_example(agent), "status": "running", "finish_reason": None})


def test_an_agent_cant_express_approval():
    finding = {"kind": "finding", "summary": "s", "reasoning": "r",
               "evidence": [{"anchor": "a:b:c", "quote": "q"}]}
    for word in ("approved", "pass", "granted"):
        with pytest.raises(ValidationError):
            ag.AssessmentResult.model_validate({"outcome": {**finding, "status": word}})


def test_met_needs_evidence():
    with pytest.raises(ValidationError):
        ag.AgentFinding(status="met", summary="s", reasoning="r", evidence=[])


def test_no_source_means_cannot_tell():
    no_source = {"searches": ["re-review trigger"]}
    with pytest.raises(ValidationError):
        ag.AgentFinding(status="met", summary="s", reasoning="r", no_source=no_source,
                        evidence=[{"anchor": "a:b:c", "quote": "q"}])
    ag.AgentFinding(status="cannot_tell", summary="s", reasoning="r", no_source=no_source)


def test_typed_extraction_carries_no_verdict():
    extraction = {"kind": "extraction", "extracted": [{"key": "k", "status": "not_found"}]}
    ag.AssessmentResult.model_validate({"outcome": extraction})
    with pytest.raises(ValidationError):
        ag.AssessmentResult.model_validate({"outcome": {**extraction, "status": "met"}})


def test_typed_check_needs_values_to_extract():
    base = {"id": "Q", "version": 1, "question": "q", "legal_force": "guidance"}
    with pytest.raises(ValidationError):
        ag.CheckToAssess(**base, method="typed")
    with pytest.raises(ValidationError):
        ag.CheckToAssess(**base, method="agent", extract=[{"key": "k", "description": "d", "value_type": "number"}])


def test_assessor_isnt_told_it_may_pass_alone():
    assert "may_pass_alone" not in ag.CheckToAssess.model_fields


def test_found_fact_needs_evidence():
    with pytest.raises(ValidationError):
        FactValue(key="k", status="found", value="v", evidence=[])
    with pytest.raises(ValidationError):
        FactValue(key="k", status="not_found", value="v")


def test_anchor_must_be_a_handle():
    with pytest.raises(ValidationError):
        FactValue(key="k", status="found", value="v", evidence=[{"anchor": "page 41", "quote": "q"}])


def test_dispute_needs_evidence():
    with pytest.raises(ValidationError):
        ag.ChallengeResult(verdict="disputed", reason="r")
    ag.ChallengeResult(verdict="cannot_verify", reason="r")


def test_challenger_isnt_given_the_reasoning():
    assert "reasoning" not in ag.ChallengeTarget.model_fields


def test_severity_floor_must_be_an_option():
    agent = BY_NAME["reliance-watch"]
    with pytest.raises(ValidationError):
        agent.request.model_validate({**agent.request_example, "severity_floor": "none-of-these"})


def test_brief_has_no_lists_the_model_could_drop():
    assert set(ag.BriefResult.model_fields) == {"input_fingerprint", "recommendation", "summary", "draft_questions"}


def test_gap_candidate_must_cite_a_signal():
    candidate = result_of("gap-analyst")["candidates"][0]
    with pytest.raises(ValidationError):
        ag.GapCandidate.model_validate({**candidate, "signal_ids": []})


def test_notice_anchor_is_the_form_the_server_stores():
    notice = BY_NAME["reliance-watch"].request_example["notice"]
    assert notice["text_anchor"] == "n-501:r1:c1"
    with pytest.raises(ValidationError):
        ag.NoticeRef.model_validate({**notice, "text_anchor": "notice-501-r1"})


def test_sameness_keeps_the_two_sides_apart():
    field = result_of("sameness")["fields"][1]
    ag.ComparedField.model_validate(field)
    assert "evidence" not in ag.ComparedField.model_fields
    with pytest.raises(ValidationError):
        ag.ComparedField.model_validate({**field, "reference_evidence": []})
    with pytest.raises(ValidationError):
        ag.ComparedField.model_validate({**field, "agent_view": "different", "dossier_evidence": []})


def test_reader_sameness_and_challenger_say_what_they_searched():
    for result in (ag.DossierReadResult, ag.SamenessResult, ag.ChallengeResult):
        assert "searches_tried" in result.model_fields


def test_challenger_and_assessor_carry_the_law_source():
    assert "search_dossier" in BY_NAME["challenger"].tools
    assert ag.ChallengeRequest.model_validate(BY_NAME["challenger"].request_example).check_source.doc_id
    request = ag.AssessmentRequest.model_validate(BY_NAME["checklist-assessor"].request_example)
    assert request.moved_source is None


def test_a_recommendation_cites_what_it_rests_on():
    brief = result_of("coordinator")
    with pytest.raises(ValidationError):
        ag.BriefResult.model_validate({**brief, "recommendation": {"choice": "x", "reason": "r", "reason_evidence": []}})


def test_a_gap_candidate_names_its_pack_rule_or_says_there_is_none():
    candidate = result_of("gap-analyst")["candidates"][0]
    with pytest.raises(ValidationError):
        ag.GapCandidate.model_validate({**candidate, "description": "No in-force source sets a trigger."})
    with pytest.raises(ValidationError):
        del candidate["pack_rule"]
        ag.GapCandidate.model_validate(candidate)
    ag.GapCandidate.model_validate({**candidate, "pack_rule": "R3", "description": "Concerns a rule."})


def test_assistant_turn_framing_and_budget():
    assert (ka.TURN_MAX_MODEL_CALLS, ka.TURN_MAX_TOOL_CALLS, ka.TURN_SECONDS) == (12, 20, 90)
    text = spec("knowledge-assistant")["info"]["description"]
    assert "`data: {json}`" in text and "12 model calls, 20 tool calls and 90 seconds" in text
    assert {"/sessions", "/sessions/{agent_session_id}/turns"} <= set(spec("knowledge-assistant")["paths"])


def test_assistant_examples_match_the_contract():
    ka.SessionCreate.model_validate(ka.SESSION_EXAMPLE)
    ka.TurnRequest.model_validate(ka.TURN_EXAMPLE)
    for event in ka.EVENT_EXAMPLES:
        ka.EVENT_ADAPTER.validate_python(event)


def test_a_session_may_have_no_case_and_then_has_only_knowledge_tools():
    session = ka.SessionCreate.model_validate(ka.KNOWLEDGE_SESSION_EXAMPLE)
    assert session.case_id is None
    assert {"search_kb", "get_doc_status"} <= set(ka.KNOWLEDGE_TOOLS) and set(ka.KNOWLEDGE_TOOLS) <= set(ka.TOOLS)
    assert not set(ka.KNOWLEDGE_TOOLS) & set(ka.CASE_TOOLS)


def test_a_no_source_answer_lists_the_searches_tried():
    with pytest.raises(ValidationError):
        ka.EVENT_ADAPTER.validate_python({"type": "no_source", "searches_tried": []})
    with pytest.raises(ValidationError):
        ka.EVENT_ADAPTER.validate_python({"type": "message", "text": "x", "suggestions": ["a", "b", "c", "d"]})


def test_assistant_event_types_are_closed():
    with pytest.raises(ValidationError):
        ka.EVENT_ADAPTER.validate_python({"type": "set_status", "status": "pass"})


def test_no_agent_has_a_tool_that_acts():
    forbidden = re.compile(r"^(send|write|update|delete|set|approve|sign|web|fetch|submit)_")
    for name, tools in [*((a.name, a.tools) for a in ag.RUN_AGENTS), ("knowledge-assistant", ka.TOOLS)]:
        assert not [t for t in tools if forbidden.match(t)], name


def test_specs_are_current():
    built = generate_openapi.build_all()
    assert {f"agents/{n}.yaml" for n in SPEC_NAMES} <= set(built)
    for name, text in built.items():
        assert (ROOT / "openapi" / name).read_text(encoding="utf-8") == text, f"{name}: re-run the generator"


@pytest.mark.parametrize("name", SPEC_NAMES)
def test_spec_is_valid_openapi(name):
    validate_openapi(spec(name))


@pytest.mark.parametrize("name", SPEC_NAMES)
def test_run_and_session_operations_need_the_contract_header_and_no_operation_has_security(name):
    for path, item in spec(name)["paths"].items():
        for method, op in item.items():
            headers = [p["name"] for p in op.get("parameters", []) if p["in"] == "header"]
            if path == "/health":
                assert "X-PharmAlign-Contract" not in headers, f"{method} {path}"
            else:
                assert "X-PharmAlign-Contract" in headers, f"{method} {path}"
            assert not op.get("security"), f"{method} {path}"


@pytest.mark.parametrize("name", SPEC_NAMES)
def test_errors_are_problem_json_only(name):
    for path, item in spec(name)["paths"].items():
        for method, op in item.items():
            for code, response in op["responses"].items():
                if code.startswith(("4", "5")):
                    content = response["content"]
                    assert list(content) == ["application/problem+json"], f"{method} {path} {code}"
                    assert content["application/problem+json"]["schema"], f"{method} {path} {code}"


@pytest.mark.parametrize("name", RUN_IDS)
def test_run_agent_routes(name):
    agent = BY_NAME[name]
    paths = spec(name)["paths"]
    assert set(paths[agent.path]) == {"post"}
    assert set(paths[f"{agent.path}/{{run_id}}"]) == {"get", "delete"}
    assert "/health" in paths
    assert "/health/details" not in paths
    responses = paths[agent.path]["post"]["responses"]
    assert {"200", "202", "409", "422", "500"} <= set(responses)
    assert "The same run" in responses["200"]["description"] and "still running" in responses["202"]["description"]
    assert "404" in paths[f"{agent.path}/{{run_id}}"]["get"]["responses"]


def test_nothing_refers_to_internal_planning():
    pattern = re.compile(r"planning|stress.?test|hackathon|mentor|\bSM-\d|\bdecision #|\bP0\b|\bITU\b|docs/|/docs\b", re.I)
    hits = []
    for path in ROOT.rglob("*"):
        if {".pytest_cache", ".venv"} & set(path.parts):
            continue
        if path.suffix in {".py", ".yaml", ".md", ".toml"} and path.name != Path(__file__).name:
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if pattern.search(line):
                    hits.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()[:80]}")
    assert not hits, "\n".join(hits)


def test_a_check_to_assess_carries_its_supporting_sources():
    base = dict(id="LEGAL-01", version=1, question="q", source={"doc_id": "A-01", "clause": "Article (6)"}, legal_force="binding", method="agent")
    assert ag.CheckToAssess(**base, supporting_sources=[{"doc_id": "B-01", "clause": "Required documents"}]).supporting_sources[0].doc_id == "B-01"
    assert ag.CheckToAssess(**base).supporting_sources == []

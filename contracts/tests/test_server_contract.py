"""The rules the server and regulator specs must hold. Run from contracts/: python -m pytest tests -q"""
import copy
import sys
from datetime import date
from pathlib import Path

import pytest
import yaml
from openapi_spec_validator import validate as validate_openapi
from pydantic import BaseModel, ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pharmalign_contracts.regulator import REGULATOR_API  # noqa: E402
from pharmalign_contracts.server import models as m  # noqa: E402
from pharmalign_contracts.server.routes import (  # noqa: E402
    CONFIRM_EXAMPLE,
    ROUTES,
    SIGN_REFUSED_EXAMPLE,
    SIGN_REQUEST_EXAMPLE,
)

VIEWS = {"platform_admin", "process_owner", "case_officer", "decision_maker", "auditor"}
ALL_ROUTES = [r for routes in ROUTES.values() for r in routes]
METHODS = {"get", "post", "put", "patch", "delete"}


def load(name):
    return yaml.safe_load((ROOT / "openapi" / name).read_text(encoding="utf-8"))


def operations(spec):
    for path, item in spec["paths"].items():
        for method, op in item.items():
            if method in METHODS:
                yield method, path, op


def find(path, method="POST"):
    return next(r for r in ALL_ROUTES if r.path == path and r.method == method)


@pytest.mark.parametrize("name", ["server.yaml", "regulator.yaml"])
def test_valid_openapi(name):
    validate_openapi(load(name))


@pytest.mark.parametrize("name", ["server.yaml", "regulator.yaml"])
def test_every_operation_is_documented_and_secured(name):
    seen = set()
    for method, path, op in operations(load(name)):
        assert op["summary"], f"{method} {path}"
        assert op["operationId"] not in seen, f"duplicate {op['operationId']}"
        seen.add(op["operationId"])
        assert op.get("security") or path == "/health", f"{method} {path}"
        for code, response in op["responses"].items():
            if code.startswith(("4", "5")):
                assert list(response["content"]) == ["application/problem+json"], f"{method} {path} {code}"


def test_no_duplicate_routes():
    keys = [(r.method, r.path) for r in ALL_ROUTES]
    assert len(keys) == len(set(keys))


def test_every_person_route_names_its_views():
    for r in ALL_ROUTES:
        if r.public or r.actor:
            continue
        assert r.views, f"{r.method} {r.path}"
        assert set(r.views) <= VIEWS, f"{r.method} {r.path}"


def _fields(model, seen=None):
    seen = seen if seen is not None else set()
    if model in seen:
        return set()
    seen.add(model)
    names = set(model.model_fields)
    for info in model.model_fields.values():
        for arg in _types(info.annotation):
            if isinstance(arg, type) and issubclass(arg, BaseModel):
                names |= _fields(arg, seen)
    return names


def _types(annotation):
    args = getattr(annotation, "__args__", ())
    out = [annotation]
    for a in args:
        out += _types(a)
    return out


def test_tenant_never_comes_from_a_request():
    for r in ALL_ROUTES:
        if r.body:
            assert not {"tenant", "tenant_id"} & _fields(r.body), f"{r.method} {r.path}"


@pytest.mark.parametrize("path", [
    "/api/v1/check-results/{check_result_id}/confirm",
    "/api/v1/check-results/{check_result_id}/waive",
    "/api/v1/cases/{case_id}/promote",
    "/api/v1/cases/{case_id}/decisions",
    "/api/v1/decisions/{decision_id}/reconfirm",
    "/api/v1/decisions/{decision_id}/revoke",
])
def test_actions_on_evidence_require_a_fingerprint_and_list_failed_guards(path):
    route = find(path)
    assert route.body.model_fields["fingerprint"].is_required(), path
    assert route.guarded, path


def test_only_a_decision_maker_can_sign():
    assert find("/api/v1/cases/{case_id}/decisions").views == ("decision_maker",)


def test_guard_refusal_lists_failures():
    m.GuardProblem.model_validate(SIGN_REFUSED_EXAMPLE)
    with pytest.raises(ValidationError):
        m.GuardProblem.model_validate({**SIGN_REFUSED_EXAMPLE, "failed": []})


def test_examples_match():
    m.DecisionCreate.model_validate(SIGN_REQUEST_EXAMPLE)
    m.ConfirmRequest.model_validate(CONFIRM_EXAMPLE)


def test_overriding_a_recommendation_needs_a_reason():
    with pytest.raises(ValidationError):
        m.DecisionCreate.model_validate({**SIGN_REQUEST_EXAMPLE, "accepts_recommendation": False})
    m.DecisionCreate.model_validate({**SIGN_REQUEST_EXAMPLE, "accepts_recommendation": False, "reason": "r"})


def test_refusal_needs_a_reason():
    with pytest.raises(ValidationError):
        m.DecisionCreate.model_validate({**SIGN_REQUEST_EXAMPLE, "type": "refusal"})


def test_waive_needs_a_reason():
    with pytest.raises(ValidationError):
        m.WaiveRequest(fingerprint="sha256:9c1e")


def test_decision_needs_a_signer():
    with pytest.raises(ValidationError):
        m.Decision(id="d", case_id="c", type="grant", state="signed", signers=[], signed_payload_hash="sha256:9c1e",
                   recommendation_version=1, accepts_recommendation=True, legal_effect="demonstration_only", record_status="queued")


@pytest.mark.parametrize("path", ["/api/v1/session/view-as", "/api/v1/simulations", "/api/v1/simulations/{simulation_id}/events"])
def test_demo_and_simulation_routes_are_simulation_only(path):
    assert find(path).simulation_only, path


def test_notices_stay_visible_when_unmatched():
    assert "unmatched" in m.NoticeSummary.model_fields
    assert "none" in m.WatchMatch.model_fields["target_type"].annotation.__args__


def test_proposal_has_an_unknown_outcome_state():
    assert "unknown" in m.Proposal.model_fields["state"].annotation.__args__


def test_licence_has_government_fields_only():
    assert {"gov_status", "gov_version", "gov_as_of"} <= set(m.Licence.model_fields)
    assert "status" not in m.Licence.model_fields


def test_regulator_proposal_accepts_idempotency_key():
    route = next(r for r in REGULATOR_API.routes["Proposals"] if r.method == "POST")
    assert route.idempotent


def test_test_controls_are_marked():
    assert all(r.simulation_only for r in REGULATOR_API.routes["Test controls"])


# ------------------------------------------------------------ Country pack

STEPS = [
    {"id": "submit", "name": "Submit", "kind": "submit", "role": "applicant_contact"},
    {"id": "door", "name": "Door check", "kind": "door", "role": "case_officer", "agent_autonomy": "do_and_report"},
    {"id": "decide", "name": "Decision", "kind": "decide", "role": "decision_maker", "agent_autonomy": "person_signs"},
    {"id": "record", "name": "Update record", "kind": "record", "role": "connector"},
]
PACK = {
    "country_code": "AE", "day_counting": "mon_fri", "languages": ["ar", "en"], "decision_body": "Authority",
    "hot_climate_gap": "ask", "final_escalation_holder": "decision_maker", "severity_options": ["info", "urgent"],
    "case_types": [{"id": "new_registration", "label": "New registration", "routes": [
        {"id": "standard", "label": "Standard", "reliance_rule": None, "steps": STEPS}]}],
    "checks": [],
}
CHECK = {"id": "QUAL-07", "version": 1, "question": "q", "source": {"doc_id": "E-S25", "clause": "3.2.6.1"},
         "legal_force": "guidance", "stage": "knowledge", "method": "typed", "rule": "x >= 12",
         "extract": [{"key": "x", "description": "d", "value_type": "number"}]}


def pack(**changes):
    return {**copy.deepcopy(PACK), **changes}


def test_valid_pack():
    m.PackDocument.model_validate(pack(checks=[CHECK]))


def _with_steps(steps):
    p = pack()
    p["case_types"][0]["routes"][0]["steps"] = steps
    return p


@pytest.mark.parametrize("removed", ["door", "decide", "record"])
def test_locked_steps_cant_be_removed(removed):
    with pytest.raises(ValidationError):
        m.PackDocument.model_validate(_with_steps([s for s in STEPS if s["id"] != removed]))


def test_decision_before_door_check_is_refused():
    with pytest.raises(ValidationError):
        m.PackDocument.model_validate(_with_steps([STEPS[0], STEPS[2], STEPS[1], STEPS[3]]))


def test_record_before_decision_is_refused():
    with pytest.raises(ValidationError):
        m.PackDocument.model_validate(_with_steps([STEPS[0], STEPS[1], STEPS[3], STEPS[2]]))


def test_decision_can_never_be_automated():
    steps = copy.deepcopy(STEPS)
    steps[2]["agent_autonomy"] = "do_and_report"
    with pytest.raises(ValidationError):
        m.PackDocument.model_validate(_with_steps(steps))


def test_step_cant_refer_to_an_unknown_check():
    steps = copy.deepcopy(STEPS)
    steps[1]["check_ids"] = ["NOPE"]
    with pytest.raises(ValidationError):
        m.PackDocument.model_validate(_with_steps(steps))


def test_check_needs_a_source_or_local_practice():
    with pytest.raises(ValidationError):
        m.CheckDefinition.model_validate({**CHECK, "source": None})
    m.CheckDefinition.model_validate({**CHECK, "source": None, "local_practice": True})


def test_supporting_sources_need_a_main_source_a_clause_and_no_repeats():
    m.CheckDefinition.model_validate({**CHECK, "supporting_sources": [{"doc_id": "G-01", "clause": "2.1.7"}]})
    for bad in (
        {**CHECK, "source": None, "local_practice": True, "supporting_sources": [{"doc_id": "G-01", "clause": "2.1.7"}]},
        {**CHECK, "supporting_sources": [{"doc_id": "G-01"}]},
        {**CHECK, "supporting_sources": [{"doc_id": "G-01", "clause": "2.1.7"}, {"doc_id": "G-01", "clause": "2.1.7"}]},
        {**CHECK, "supporting_sources": [{"doc_id": "E-S25", "clause": "3.2.6.1"}]},
    ):
        with pytest.raises(ValidationError):
            m.CheckDefinition.model_validate(bad)


def test_a_check_with_no_supporting_sources_has_an_empty_list():
    assert m.CheckDefinition.model_validate(CHECK).supporting_sources == []


def test_agent_check_needs_a_source():
    base = {k: v for k, v in CHECK.items() if k not in ("rule", "extract")}
    with pytest.raises(ValidationError):
        m.CheckDefinition.model_validate({**base, "method": "agent", "profile": "quality", "source": None, "local_practice": True})


def test_binding_law_cant_be_waived_by_a_case_officer():
    with pytest.raises(ValidationError):
        m.CheckDefinition.model_validate({**CHECK, "legal_force": "binding"})
    m.CheckDefinition.model_validate({**CHECK, "legal_force": "binding", "waive_by": "decision_maker"})


def test_only_code_can_draft_a_return_notice():
    with pytest.raises(ValidationError):
        m.CheckDefinition.model_validate({**CHECK, "on_fail": "draft_return_notice"})


def test_reliance_rule_needs_a_source():
    with pytest.raises(ValidationError):
        m.RelianceRule(trusted_regulators=["FDA"], match="any", product_types=["generic"])


# ------------------------------------------------------------ Other rules


def test_ingest_overlap_must_be_smaller_than_chunk():
    with pytest.raises(ValidationError):
        m.IngestProfile(preset="custom", parser="fast_text", split_by="heading", words_per_chunk=50, overlap_words=50)


def test_delegation_dates():
    with pytest.raises(ValidationError):
        m.DelegationCreate(from_person_id="a", to_person_id="b", role="assessor", valid_from=date(2026, 2, 1), valid_to=date(2026, 1, 1), reason="r")


def test_manual_ack_needs_a_reference_when_applied():
    with pytest.raises(ValidationError):
        m.ManualAck(outcome="applied")


def test_document_rejection_needs_notes():
    with pytest.raises(ValidationError):
        m.DocumentReviewRequest(decision="rejected")


def test_lists_for_simulations_and_decisions_exist():
    sims = find("/api/v1/simulations", "GET")
    assert sims.simulation_only and "platform_admin" in sims.views
    decisions = find("/api/v1/decisions", "GET")
    assert {"case_officer", "decision_maker", "auditor"} <= set(decisions.views)
    assert {"case_id", "state", "record_status"} <= {q.name for q in decisions.query}
    ops = {op["operationId"] for _, _, op in operations(load("server.yaml"))}
    assert {"get_api_v1_simulations", "get_api_v1_decisions"} <= ops


def test_superseded_check_results_have_a_documented_read():
    checks = find("/api/v1/cases/{case_id}/checks", "GET")
    history = next(q for q in checks.query if q.name == "history")
    assert history.enum == ("current", "superseded")
    assert {"is_current", "current_result_id"} <= set(m.CheckResult.model_fields)
    assert m.CheckResult.model_fields["is_current"].default is True

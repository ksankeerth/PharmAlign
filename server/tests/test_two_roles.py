"""The five views acting as two roles: each person keeps their view, and only the groups widen."""
from types import SimpleNamespace

import pytest
from pharmalign_contracts.server.routes import SERVER_API

from app import authz
from app.config import Settings
from app.identity import Actor
from app.problems import ApiError


def route_of(method: str, path: str):
    return next(r for routes in SERVER_API.routes.values() for r in routes if r.method == method and r.path == path)


SIGN = route_of("POST", "/api/v1/cases/{case_id}/decisions")
GAPS = route_of("GET", "/api/v1/workspaces/{workspace_id}/gaps")
AUDIT = route_of("GET", "/api/v1/audit-events")
PROMOTE = route_of("POST", "/api/v1/cases/{case_id}/promote")
DOCUMENTS = route_of("GET", "/api/v1/documents")
TENANT = SimpleNamespace(id="ws-ae", mode="simulation")


def actor(view: str) -> Actor:
    return Actor(type="person", id="p-x", tenant_id="ws-ae", view=view)


@pytest.mark.parametrize("route, view, acting", [
    (SIGN, "case_officer", "decision_maker"),
    (PROMOTE, "decision_maker", "case_officer"),
    (GAPS, "platform_admin", "process_owner"),
    (AUDIT, "platform_admin", "auditor"),
])
def test_a_person_can_use_their_groups_operations(route, view, acting):
    assert authz.can_act(route, actor(view), TENANT, "simulation", two_roles=True).view == acting


@pytest.mark.parametrize("route, view", [(SIGN, "platform_admin"), (SIGN, "auditor"), (AUDIT, "case_officer"), (GAPS, "decision_maker")])
def test_the_groups_stay_apart(route, view):
    with pytest.raises(ApiError):
        authz.can_act(route, actor(view), TENANT, "simulation", two_roles=True)


def test_a_persons_own_view_comes_first():
    assert authz.can_act(DOCUMENTS, actor("decision_maker"), TENANT, "simulation", two_roles=True).view == "decision_maker"


def test_with_the_groups_off_a_view_gets_only_its_own_operations():
    with pytest.raises(ApiError):
        authz.can_act(SIGN, actor("case_officer"), TENANT, "simulation", two_roles=False)


def test_the_knowledge_pages_are_open_to_every_view():
    for method, path in (("GET", "/api/v1/documents"), ("POST", "/api/v1/kb-search"), ("GET", "/api/v1/document-versions/{version_id}/quality")):
        assert set(route_of(method, path).views) == {"platform_admin", "process_owner", "case_officer", "decision_maker", "auditor"}


@pytest.mark.parametrize("env, expected", [("simulation", True), ("live", False), ("shadow", False)])
def test_the_groups_are_on_only_in_simulation(env, expected, monkeypatch):
    monkeypatch.delenv("PHARMALIGN_TWO_ROLES", raising=False)
    monkeypatch.setenv("PHARMALIGN_RUN_TOKEN_SECRET", "x")
    assert Settings(env=env).two_roles is expected

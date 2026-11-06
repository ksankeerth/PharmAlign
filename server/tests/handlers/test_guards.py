"""Every guarded route refuses with a 409 that lists all the guards that failed, and the view decides who may call it."""
import pytest

from app.ops import HANDLERS, OPERATIONS
from ..conftest import FixedIdentity
from .conftest import failed, headers

pytestmark = pytest.mark.seeded
BAD = "sha256:" + "0" * 64


async def test_confirm_lists_every_failed_guard(http):
    r = await http.post("/api/v1/check-results/cr-1043-safe-01/confirm", json={"fingerprint": BAD}, headers=headers("p-alex"))
    assert failed(r) == {"check_needs_person", "fingerprint_matches"}
    assert r.json()["current_fingerprint"].startswith("sha256:")


async def test_waive_lists_every_failed_guard(http):
    r = await http.post("/api/v1/check-results/cr-1043-safe-01/waive", json={"fingerprint": BAD, "reason": "Trying."}, headers=headers("p-alex"))
    assert {"check_waivable", "fingerprint_matches"} <= failed(r)


async def test_promote_lists_every_failed_guard(http):
    r = await http.post("/api/v1/cases/CASE-1042/promote", json={"fingerprint": BAD}, headers=headers("p-alex"))
    assert {"blocking_checks_clear", "fingerprint_matches"} <= failed(r)
    assert all(not g["passed"] for g in r.json()["failed"])


async def test_sign_lists_every_failed_guard(http):
    body = {"type": "grant", "fingerprint": BAD, "recommendation_version": 1, "accepts_recommendation": True, "conditions": []}
    r = await http.post("/api/v1/cases/CASE-1042/decisions", json=body, headers=headers("p-dana"))
    assert {"case_promoted", "blocking_checks_clear", "fingerprint_matches"} <= failed(r)


@pytest.mark.parametrize("action", ["reconfirm", "revoke"])
async def test_ending_a_hold_lists_every_failed_guard(http, action):
    r = await http.post(f"/api/v1/decisions/dec-1039/{action}", json={"fingerprint": BAD, "reason": "Trying."}, headers=headers("p-dana"))
    assert failed(r) == {"decision_on_hold", "fingerprint_matches"}


async def test_assign_refuses_a_declared_conflict(http):
    r = await http.post("/api/v1/cases/CASE-1045/assign", json={"person_id": "p-chris", "role": "assessor"}, headers=headers("p-alex"))
    assert failed(r) == {"no_declared_conflict"}


async def test_approving_a_connection_that_was_never_checked_is_refused(http):
    made = await http.post("/api/v1/connections", json={"name": "Portal", "kind": "mock", "spec_version": "1"}, headers=headers("p-jordan"))
    assert made.status_code == 201, made.text
    r = await http.post(f"/api/v1/connections/{made.json()['id']}/approve", json={"reason": "Looks fine."}, headers=headers("p-sam"))
    assert failed(r) == {"conformance_passing"}


# ---------------------------------------------------------------- the view decides


VIEWS = ("platform_admin", "process_owner", "case_officer", "decision_maker", "auditor")
# The assistant is open to every view; test_assistant.py covers who may open a chat on a case.
DOMAINS = ("cases", "checks", "review", "decisions", "medicines", "notifications", "gaps")
BOUND = [r for key, r in OPERATIONS.items() if r.views and HANDLERS.get(key) and HANDLERS[key].__module__.rsplit(".", 1)[-1] in DOMAINS]


def test_the_case_flow_is_bound():
    assert len(BOUND) >= 45


@pytest.mark.parametrize("route", BOUND, ids=lambda r: f"{r.method} {r.path}")
async def test_a_view_the_route_does_not_name_is_refused(make_app, route):
    outside = next((v for v in VIEWS if v not in route.views), None)
    if outside is None:
        pytest.skip("every view may call this")
    from ..conftest import client_for
    path = route.path.replace("{", "").replace("}", "")  # an id that doesn't exist: the refusal comes first
    async with client_for(make_app(identity=FixedIdentity(outside))) as client:
        response = await client.request(route.method, path, json={} if route.body else None)
    assert response.status_code == 403, (outside, response.text)
    assert response.json()["code"] == "forbidden"

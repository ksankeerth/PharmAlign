"""An admin's act in another country is audited there. People, roles, licences and the admin views."""
import pytest
from sqlalchemy import select

from app import audit
from app.db import make_sessions
from app.tables import AuditEventRow

from ..conftest import LiveAgents
from .conftest import headers

pytestmark = pytest.mark.seeded
ADMIN, OWNER, AUDITOR = headers("p-jordan"), headers("p-sam"), headers("p-robin")
NEW_COUNTRY = {
    "name": "Qatar (synthetic)", "country_code": "QAT", "authority_name": "Synthetic Medicines Authority", "languages": ["en"],
    "time_zone": "Asia/Qatar", "day_counting": "sun_thu", "data_location": "Synthetic region", "template": "platform",
}


async def chain(engine, tenant):
    async with make_sessions(engine)() as db:
        rows = (await db.scalars(select(AuditEventRow).where(AuditEventRow.tenant_id == tenant).order_by(AuditEventRow.seq))).all()
        return rows, await audit.verify(db, tenant)


async def test_an_admins_action_lands_in_the_countrys_chain(http, engine):
    before, _ = await chain(engine, "ws-ae")
    made = await http.post("/api/v1/workspaces", json=NEW_COUNTRY, headers=ADMIN)
    assert made.status_code == 201, made.text
    wid = made.json()["id"]
    started = await http.post(f"/api/v1/workspaces/{wid}/readiness-runs", headers=ADMIN)
    assert started.status_code == 202, started.text
    there, verified = await chain(engine, wid)
    assert [(r.action, r.actor_id, r.role_used) for r in there] == [
        ("workspace_created", "p-jordan", "platform_admin"), ("readiness_started", "p-jordan", "platform_admin")]
    assert verified.ok and verified.rows_checked == 2
    here, _ = await chain(engine, "ws-ae")
    assert len(here) == len(before)  # nothing about the other country was written to the admin's own chain


async def test_an_action_in_ones_own_country_stays_in_its_chain(http, engine):
    before, _ = await chain(engine, "ws-ae")
    r = await http.post("/api/v1/workspaces/ws-ae/readiness-runs", headers=ADMIN)
    assert r.status_code == 202, r.text
    after, verified = await chain(engine, "ws-ae")
    assert [x.action for x in after[len(before):]] == ["readiness_started"] and verified.ok


async def test_people_roles_and_delegations(http):
    people = (await http.get("/api/v1/people?limit=100", headers=ADMIN)).json()["items"]
    assert {"p-jordan", "p-robin"} <= {p["id"] for p in people} and all("grants" in p for p in people)
    made = await http.post("/api/v1/people", json={"display_name": "Sam Tester (synthetic)"}, headers=ADMIN)
    assert made.status_code == 201 and made.json()["grants"] == []
    pid = made.json()["id"]
    grant_body = {"person_id": pid, "role": "assessor", "valid_from": "2026-11-01"}
    grant = await http.post("/api/v1/role-grants", json=grant_body, headers=ADMIN)
    assert grant.status_code == 201, grant.text
    assert (await http.post("/api/v1/role-grants", json=grant_body, headers=ADMIN)).json()["code"] == "already_granted"
    assert [g["role"] for g in (await http.get(f"/api/v1/people/{pid}", headers=ADMIN)).json()["grants"]] == ["assessor"]
    assert (await http.delete(f"/api/v1/role-grants/{grant.json()['id']}", headers=ADMIN)).status_code == 204
    assert (await http.get(f"/api/v1/people/{pid}", headers=ADMIN)).json()["grants"] == []
    assert (await http.delete("/api/v1/role-grants/g-none", headers=ADMIN)).status_code == 404
    own = next(g["id"] for p in people if p["id"] == "p-jordan" for g in p["grants"] if g["role"] == "platform_admin")
    assert (await http.delete(f"/api/v1/role-grants/{own}", headers=ADMIN)).json()["code"] == "guard_failed"
    assert (await http.get("/api/v1/people", headers=OWNER)).status_code == 403

    delegation = {"from_person_id": "p-alex", "to_person_id": "p-chris", "role": "case_manager", "valid_from": "2026-11-02", "valid_to": "2026-11-09", "reason": "Planned leave"}
    assert (await http.post("/api/v1/delegations", json=delegation, headers=ADMIN)).status_code == 201
    assert (await http.post("/api/v1/delegations", json={**delegation, "to_person_id": "p-alex"}, headers=ADMIN)).status_code == 422
    assert len((await http.get("/api/v1/delegations", headers=AUDITOR)).json()["items"]) >= 2


async def test_a_conflict_is_declared_and_listed(http):
    body = {"person_id": "p-alex", "party_id": "pt-harbor", "reason": "Relative works there (synthetic)"}
    made = await http.post("/api/v1/conflicts", json=body, headers=headers("p-alex"))
    assert made.status_code == 201, made.text
    assert made.json()["id"] in {c["id"] for c in (await http.get("/api/v1/conflicts", headers=ADMIN)).json()["items"]}
    assert (await http.post("/api/v1/conflicts", json={"person_id": "p-alex", "reason": "No target"}, headers=ADMIN)).status_code == 422


async def test_licences_and_their_reliance_basis(http):
    licences = (await http.get("/api/v1/licences", headers=headers("p-alex"))).json()["items"]
    assert licences
    one = licences[0]
    assert (await http.get(f"/api/v1/licences/{one['id']}", headers=headers("p-alex"))).json()["id"] == one["id"]
    by_status = (await http.get(f"/api/v1/licences?gov_status={one['gov_status']}&medicine_id={one['medicine_id']}", headers=headers("p-alex"))).json()["items"]
    assert one["id"] in {x["id"] for x in by_status}
    assert "items" in (await http.get(f"/api/v1/licences/{one['id']}/reliance-basis", headers=headers("p-alex"))).json()
    assert (await http.get("/api/v1/licences/lic-none", headers=headers("p-alex"))).status_code == 404
    assert (await http.get("/api/v1/licences/lic-none/reliance-basis", headers=headers("p-alex"))).status_code == 404


async def test_agent_health_asks_each_running_agent_and_lists_them_all(http):
    http.app.state.rt.agents = LiveAgents(down=("sameness",))
    listed = (await http.get("/api/v1/admin/agents/health", headers=ADMIN)).json()
    assert {h["agent"] for h in listed["items"]} == set(http.app.state.rt.agents.urls) and len(listed["items"]) == 8
    assert {h["agent"]: h["status"] for h in listed["items"]} == {name: "down" if name == "sameness" else "ok" for name in http.app.state.rt.agents.urls}
    assert listed["next_cursor"] is None


async def test_admin_views_of_agents_runs_and_usage(http):
    health = (await http.get("/api/v1/admin/agents/health", headers=ADMIN)).json()["items"]
    assert health and {"agent", "status"} <= set(health[0])
    runs = (await http.get("/api/v1/admin/agent-runs?limit=5", headers=ADMIN)).json()
    assert len(runs["items"]) == 5 and runs["next_cursor"] == "5"
    stamps = [r["started_at"] for r in runs["items"] if r["started_at"]]
    assert stamps == sorted(stamps, reverse=True)
    one = runs["items"][0]
    only = (await http.get(f"/api/v1/admin/agent-runs?agent={one['agent']}&status={one['status']}", headers=ADMIN)).json()["items"]
    assert only and all(r["agent"] == one["agent"] and r["status"] == one["status"] for r in only)
    usage = (await http.get("/api/v1/admin/usage", headers=ADMIN)).json()
    assert usage["agents"] and usage["period_from"] < usage["period_to"]
    assert (await http.get("/api/v1/admin/usage", headers=OWNER)).status_code == 403

"""Demo identity, View as, and where they are refused."""
import asyncio

import pytest
import pytest_asyncio
from sqlalchemy import select

from app import audit
from app.config import Settings
from app.db import make_sessions
from app.identity import WORKSPACE_COOKIE, DemoIdentity
from app.main import UnsafeConfiguration, create_app
from app.seed import load_files, read
from app.tables import AuditEventRow, PersonRow

from .conftest import SERVICE, START, TEST_URL, FixedIdentity, LiveAgents, as_person, client_for

ADMIN = as_person("p-jordan")


@pytest_asyncio.fixture
async def demo(engine):
    """The full demo seed, for the tests that need workspaces and cases. The others run on the demo people alone."""
    async with make_sessions(engine)() as db, db.begin():
        await load_files(db, START)


def seeded_workspace(workspace_id: str) -> dict:
    doc = next(row["doc"] for row in read("workspace", START) if row["doc"]["id"] == workspace_id)
    return {k: doc[k] for k in ("id", "name", "country_code", "authority_name", "mode")}


async def new_person(client, view: str, name: str = "New Person", **extra) -> str:
    """Adds a demo person to the admin's active workspace and returns the id."""
    response = await client.post("/api/v1/people", json={"display_name": name, "view": view, **extra}, headers=ADMIN)
    assert response.status_code == 201, response.text
    return response.json()["id"]


@pytest.mark.parametrize("env", ["live", "shadow"])
def test_demo_identity_is_refused_at_startup_outside_simulation(env):
    with pytest.raises(UnsafeConfiguration):
        create_app(Settings(env=env, database_url=TEST_URL))
    with pytest.raises(UnsafeConfiguration):
        create_app(Settings(env=env, database_url=TEST_URL), identity=DemoIdentity("p-alex"))


def test_the_environment_defaults_to_refusing(monkeypatch):
    monkeypatch.delenv("PHARMALIGN_ENV", raising=False)
    with pytest.raises(UnsafeConfiguration):
        create_app()


def test_the_environment_variable_turns_on_simulation(monkeypatch):
    monkeypatch.setenv("PHARMALIGN_ENV", "simulation")
    assert create_app().state.rt.settings.env == "simulation"


@pytest.mark.parametrize("env", ["live", "shadow"])
async def test_view_as_is_refused_outside_simulation(make_app, env, count):
    app = make_app(env=env, identity=FixedIdentity("platform_admin"))
    async with client_for(app) as client:
        response = await client.post("/api/v1/session/view-as", json={"person_id": "p-sam"})
        people = await client.get("/api/v1/demo-people")
    assert response.status_code == 403
    assert response.json()["code"] == "simulation_only"
    assert "set-cookie" not in response.headers
    assert people.json()["code"] == "simulation_only"
    assert await count(AuditEventRow) == 0


async def test_me_opens_as_the_default_person(make_app, demo):
    async with client_for(make_app()) as client:
        response = await client.get("/api/v1/me")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "person_id": "p-alex", "display_name": "Alex Morgan", "is_demo": True, "view": "case_officer",
        "roles": ["case_manager", "intake_officer", "assessor", "watch_officer"], "workspace_id": "ws-ae", "workspace": seeded_workspace("ws-ae"), "mode": "simulation",
    }


async def test_demo_people_are_paged_in_order(make_app):
    async with client_for(make_app()) as client:
        first = (await client.get("/api/v1/demo-people?limit=3")).json()
        rest = (await client.get(f"/api/v1/demo-people?cursor={first['next_cursor']}")).json()
    assert [p["person_id"] for p in first["items"]] == ["p-jordan", "p-sam", "p-priya"]
    assert first["items"][0] == {"person_id": "p-jordan", "display_name": "Jordan Lee", "view": "platform_admin"}
    assert len(rest["items"]) == 5
    assert rest["next_cursor"] is None


async def test_view_as_switches_person_by_cookie(make_app, count):
    async with client_for(make_app()) as client:
        response = await client.post("/api/v1/session/view-as", json={"person_id": "p-dana"})
        assert response.status_code == 200, response.text
        assert response.json()["person_id"] == "p-dana"
        assert response.json()["view"] == "decision_maker"
        cookie = response.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=lax" in cookie
        me = await client.get("/api/v1/me")
    assert me.json()["person_id"] == "p-dana"
    assert await count(AuditEventRow) == 1


async def test_view_as_needs_a_demo_person(make_app):
    async with client_for(make_app()) as client:
        response = await client.post("/api/v1/session/view-as", json={"person_id": "p-nobody"})
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


async def test_an_unknown_session_is_not_signed_in(make_app):
    async with client_for(make_app()) as client:
        response = await client.get("/api/v1/me", headers=as_person("p-nobody"))
        client.cookies.set("pharmalign_session", "p-nobody")
        by_cookie = await client.get("/api/v1/me")
    assert response.status_code == by_cookie.status_code == 401
    assert response.json()["code"] == "not_signed_in"


async def test_no_identity_is_not_signed_in(make_app):
    class Nobody(FixedIdentity):
        async def authenticate(self, db, request, now):
            return None

    async with client_for(make_app(env="live", identity=Nobody())) as client:
        assert (await client.get("/api/v1/me")).status_code == 401
        assert (await client.get("/health")).status_code == 200


async def test_service_calls_are_kept_apart_from_people(make_app):
    async with client_for(make_app()) as client:
        assert (await client.post("/internal/ticks", json={})).status_code == 403
        assert (await client.post("/internal/ticks", json={}, headers=as_person("p-jordan"))).status_code == 403
        assert (await client.post("/internal/ticks", json={}, headers=SERVICE)).status_code == 200
        assert (await client.get("/api/v1/me", headers=SERVICE)).status_code == 403


async def test_a_service_token_must_be_configured(make_app):
    app = make_app()
    app.state.rt.settings.service_token = None
    async with client_for(app) as client:
        response = await client.post("/internal/ticks", json={}, headers=SERVICE)
    assert response.status_code == 401


async def test_simulation_can_stand_in_for_a_connector_only_in_simulation(make_app):
    async def open_case(req, ctx):
        raise AssertionError("should not run")

    route = ("POST", "/api/v1/cases")
    body = {"nope": 1}
    async with client_for(make_app(handlers={route: open_case})) as client:
        assert (await client.post("/api/v1/cases", json=body)).status_code == 422  # past authz, stopped by the body
    async with client_for(make_app(env="live", identity=FixedIdentity(), handlers={route: open_case})) as client:
        assert (await client.post("/api/v1/cases", json=body)).status_code == 403


async def test_health_details_are_for_admins(make_app):
    async with client_for(make_app()) as client:
        assert (await client.get("/health/details")).status_code == 403
        response = await client.get("/health/details", headers=as_person("p-jordan"))
    assert response.status_code == 200, response.text
    assert response.json()["database"] == "ok"
    assert response.json()["status"] == "degraded"


async def health_of(app) -> dict:
    async with client_for(app) as client:
        return (await client.get("/health/details", headers=ADMIN)).json()


async def test_health_is_ok_when_every_agent_answers_and_the_worker_runs(make_app):
    app = make_app()
    app.state.rt.agents = LiveAgents()
    app.state.rt.worker = asyncio.create_task(asyncio.sleep(60))
    try:
        health = await health_of(app)
    finally:
        app.state.rt.worker.cancel()
    assert health["status"] == "ok" and health["ingest_worker"] == "ok" and health["agent_mode"] == "http"
    assert len(health["agents"]) == 8 and {a["status"] for a in health["agents"]} == {"ok"}


async def test_health_is_degraded_when_an_agent_is_down_or_no_worker_runs(make_app):
    app = make_app()
    app.state.rt.agents = LiveAgents(down=("coordinator",))
    app.state.rt.worker = asyncio.create_task(asyncio.sleep(60))
    try:
        health = await health_of(app)
    finally:
        app.state.rt.worker.cancel()
    assert health["status"] == "degraded" and health["ingest_worker"] == "ok" and health["agent_mode"] == "http"
    assert {a["agent"] for a in health["agents"] if a["status"] == "down"} == {"coordinator"}

    app = make_app()
    app.state.rt.agents = LiveAgents()
    health = await health_of(app)
    assert health["status"] == "degraded" and health["ingest_worker"] == "down" and {a["status"] for a in health["agents"]} == {"ok"}


async def test_health_says_replay_when_the_server_answers_from_recordings(make_app):
    health = await health_of(make_app())
    assert health["agent_mode"] == "replay" and health["ingest_worker"] == "down"


@pytest.mark.parametrize("person, view", [
    ("p-jordan", "platform_admin"), ("p-sam", "process_owner"), ("p-alex", "case_officer"), ("p-dana", "decision_maker"), ("p-robin", "auditor"),
])
async def test_me_names_the_workspace_for_every_view(make_app, demo, person, view):
    async with client_for(make_app()) as client:
        me = (await client.get("/api/v1/me", headers=as_person(person))).json()
    assert me["view"] == view
    assert me["workspace_id"] == "ws-ae"
    assert me["workspace"] == seeded_workspace("ws-ae")


async def test_an_admin_switches_workspace(make_app, demo):
    async with client_for(make_app()) as client:
        response = await client.post("/api/v1/session/workspace", json={"workspace_id": "ws-sa"}, headers=ADMIN)
        assert response.status_code == 200, response.text
        assert response.json()["person_id"] == "p-jordan" and response.json()["view"] == "platform_admin"
        assert response.json()["workspace_id"] == "ws-sa"
        assert response.json()["workspace"] == seeded_workspace("ws-sa")
        cookie = response.headers["set-cookie"].lower()
        assert f"{WORKSPACE_COOKIE}=ws-sa" in cookie and "httponly" in cookie and "samesite=lax" in cookie

        # The cookie keeps them there, and what they manage is the new workspace's.
        assert (await client.get("/api/v1/me", headers=ADMIN)).json()["workspace"]["country_code"] == "SA"
        assert (await client.get("/api/v1/people", headers=ADMIN)).json()["items"] == []
        officer = await new_person(client, "case_officer", "Lena Sa")
        people = (await client.get("/api/v1/people", headers=ADMIN)).json()["items"]
        assert [p["id"] for p in people] == [officer]

        # As a person there, the queue is the new workspace's only.
        viewed = await client.post("/api/v1/session/view-as", json={"person_id": officer}, headers=ADMIN)
        assert viewed.status_code == 200, viewed.text
        assert viewed.json()["workspace_id"] == "ws-sa"
        cases = (await client.get("/api/v1/cases")).json()["items"]
        assert [c["id"] for c in cases] == ["CASE-2001"]


async def test_a_case_officer_ignores_the_workspace_cookie(make_app, demo):
    async with client_for(make_app()) as client:
        client.cookies.set(WORKSPACE_COOKIE, "ws-sa")
        me = (await client.get("/api/v1/me")).json()
        cases = (await client.get("/api/v1/cases?limit=200")).json()["items"]
    assert me["person_id"] == "p-alex" and me["workspace_id"] == "ws-ae"
    assert cases and "CASE-2001" not in {c["id"] for c in cases}


async def test_an_admin_ignores_a_cookie_for_a_workspace_that_does_not_exist(make_app, demo):
    async with client_for(make_app()) as client:
        client.cookies.set(WORKSPACE_COOKIE, "ws-nowhere")
        me = await client.get("/api/v1/me", headers=ADMIN)
    assert me.status_code == 200 and me.json()["workspace_id"] == "ws-ae"


async def test_an_unknown_workspace_is_not_found(make_app, demo, count):
    async with client_for(make_app()) as client:
        response = await client.post("/api/v1/session/workspace", json={"workspace_id": "ws-nowhere"}, headers=ADMIN)
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"
    assert "set-cookie" not in response.headers
    assert await count(AuditEventRow) == len(read("audit_event", START))


@pytest.mark.parametrize("person", ["p-alex", "p-sam", "p-dana", "p-robin"])
async def test_only_an_admin_may_switch_workspace(make_app, demo, person):
    async with client_for(make_app()) as client:
        response = await client.post("/api/v1/session/workspace", json={"workspace_id": "ws-sa"}, headers=as_person(person))
    assert response.status_code == 403
    assert "set-cookie" not in response.headers


async def test_the_demo_people_of_a_new_workspace_include_the_admins(make_app, demo):
    async with client_for(make_app()) as client:
        await client.post("/api/v1/session/workspace", json={"workspace_id": "ws-sa"}, headers=ADMIN)
        local = await new_person(client, "case_officer", "Lena Sa")
        second_admin = await new_person(client, "platform_admin", "Ade Sa")
        ids = [p["person_id"] for p in (await client.get("/api/v1/demo-people?limit=100", headers=ADMIN)).json()["items"]]
        assert "p-jordan" in ids and "p-alex" not in ids
        assert ids == sorted(set(ids), key=ids.index)  # no one twice
        assert {local, second_admin} <= set(ids)

        # From the first workspace, the admin made in the second still shows.
        await client.post("/api/v1/session/workspace", json={"workspace_id": "ws-ae"}, headers=ADMIN)
        home = [p["person_id"] for p in (await client.get("/api/v1/demo-people?limit=100", headers=ADMIN)).json()["items"]]
    assert home == ["p-jordan", "p-sam", "p-priya", "p-alex", "p-chris", "p-dana", "p-noor", "p-robin", second_admin]


async def test_view_as_reaches_an_admin_of_another_workspace_and_clears_the_workspace(make_app, demo):
    async with client_for(make_app()) as client:
        await client.post("/api/v1/session/workspace", json={"workspace_id": "ws-sa"}, headers=ADMIN)
        officer = await new_person(client, "case_officer", "Lena Sa")
        assert client.cookies.get(WORKSPACE_COOKIE) == "ws-sa"
        assert (await client.post("/api/v1/session/view-as", json={"person_id": officer}, headers=ADMIN)).json()["workspace_id"] == "ws-sa"
        assert client.cookies.get(WORKSPACE_COOKIE) is None

        back = await client.post("/api/v1/session/view-as", json={"person_id": "p-jordan"})
        assert back.status_code == 200, back.text
        assert back.json()["person_id"] == "p-jordan" and back.json()["workspace_id"] == "ws-ae"
        assert (await client.get("/api/v1/me")).json()["workspace_id"] == "ws-ae"

        # Someone who isn't an admin is still only reachable inside the active workspace.
        await client.post("/api/v1/session/workspace", json={"workspace_id": "ws-sa"})
        refused = await client.post("/api/v1/session/view-as", json={"person_id": "p-alex"})
    assert refused.status_code == 404


async def test_view_as_clears_the_workspace_even_for_the_same_workspace(make_app, demo):
    async with client_for(make_app()) as client:
        await client.post("/api/v1/session/workspace", json={"workspace_id": "ws-sa"}, headers=ADMIN)
        response = await client.post("/api/v1/session/view-as", json={"person_id": "p-jordan"})
    assert response.status_code == 200
    assert f"{WORKSPACE_COOKIE}=" in response.headers["set-cookie"] and "max-age=0" in response.headers["set-cookie"].lower()
    assert response.json()["workspace_id"] == "ws-ae"


async def test_a_new_person_has_the_view_asked_for(make_app, demo, engine):
    async with client_for(make_app()) as client:
        made = await new_person(client, "decision_maker", "Dee Maker")
        titled = await new_person(client, "auditor", "Ola Audit", title="Lead Auditor")
        default = await client.post("/api/v1/people", json={"display_name": "Cas Officer"}, headers=ADMIN)
        viewed = await client.post("/api/v1/session/view-as", json={"person_id": made}, headers=ADMIN)
        me = await client.get("/api/v1/me")
    assert viewed.status_code == 200, viewed.text
    assert me.json()["view"] == "decision_maker" and me.json()["person_id"] == made
    async with make_sessions(engine)() as db:
        rows = {r.id: r for r in await db.scalars(select(PersonRow).where(PersonRow.id.in_([made, titled, default.json()["id"]])))}
    assert rows[made].title == "Decision Maker" and rows[made].view == "decision_maker"
    assert rows[titled].title == "Lead Auditor" and rows[titled].view == "auditor"
    assert (rows[default.json()["id"]].view, rows[default.json()["id"]].title) == ("case_officer", "Case Officer")


async def test_switching_workspace_is_audited_in_the_chain_entered(make_app, demo, engine):
    async with client_for(make_app()) as client:
        await client.post("/api/v1/session/workspace", json={"workspace_id": "ws-sa"}, headers=ADMIN)
        auditor = await new_person(client, "auditor", "Ida Audit")
        await client.post("/api/v1/session/view-as", json={"person_id": auditor}, headers=ADMIN)
        verified = await client.get("/api/v1/audit/verify")
    assert verified.status_code == 200, verified.text
    assert verified.json()["ok"] is True
    async with make_sessions(engine)() as db:
        switched = (await db.scalars(select(AuditEventRow).where(AuditEventRow.action == "workspace_switched"))).all()
        assert [(e.tenant_id, e.actor_id, e.before, e.after) for e in switched] == [("ws-sa", "p-jordan", {"workspace_id": "ws-ae"}, {"workspace_id": "ws-sa"})]
        for tenant in ("ws-ae", "ws-sa"):
            assert (await audit.verify(db, tenant)).ok

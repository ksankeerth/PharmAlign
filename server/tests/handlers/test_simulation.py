"""Simulation: the governed and ungoverned runs. The ungoverned one is for a Platform Admin, in simulation only, and touches no case."""
import pytest
from sqlalchemy import select

from app import audit
from app.db import make_sessions
from app.jobs import Worker
from app.tables import ROW

from ..conftest import FixedIdentity, client_for
from .conftest import headers

pytestmark = pytest.mark.seeded
RUN = {"scenario_id": "scn-repealed-law", "pack_version_id": "pv-ae-3", "kb_snapshot_id": "snap-ae-14", "execution": "replay"}
ADMIN, OWNER = headers("p-jordan"), headers("p-sam")


async def finish(http, clock):
    clock.advance(seconds=3)
    await Worker(http.app.state.rt).run_due()


async def case_state(engine) -> list:
    async with make_sessions(engine)() as db:
        out = []
        for table in ("case", "check_result", "decision", "recommendation_version", "proposal"):
            row = ROW[table]
            out.append([(r.id, r.doc) for r in (await db.scalars(select(row).order_by(row.id))).all()])
        return out


async def test_the_ungoverned_option_is_refused_for_a_process_owner(http):
    r = await http.post("/api/v1/simulations", json={**RUN, "gate": "off"}, headers=OWNER)
    assert r.status_code == 403 and "Platform Admin" in r.json()["detail"], r.text
    assert (await http.post("/api/v1/simulations", json={**RUN, "gate": "on"}, headers=OWNER)).status_code == 202


async def test_ungoverned_compare_is_refused_for_non_admins(http):
    ok = await http.get("/api/v1/simulations/compare?governed=sim-11&ungoverned=sim-12", headers=ADMIN)
    assert ok.status_code == 200 and ok.json()["rows"]
    r = await http.get("/api/v1/simulations/compare?governed=sim-11&ungoverned=sim-12", headers=OWNER)
    assert r.status_code == 403
    assert (await http.get("/api/v1/simulations/compare?governed=sim-11&ungoverned=sim-12", headers=headers("p-robin"))).status_code == 403


async def test_ungoverned_runs_and_compare_are_refused_outside_simulation(make_app):
    for env in ("shadow", "live"):
        async with client_for(make_app(env=env, identity=FixedIdentity("platform_admin"))) as client:
            run = await client.post("/api/v1/simulations", json={**RUN, "gate": "off"})
            cmp = await client.get("/api/v1/simulations/compare?governed=sim-11&ungoverned=sim-12")
        assert run.status_code == 403 and run.json()["code"] == "simulation_only", env
        assert cmp.status_code == 403 and cmp.json()["code"] == "simulation_only", env


async def test_ungoverned_runs_leave_case_state_unchanged(http, engine, clock):
    before = await case_state(engine)
    off = (await http.post("/api/v1/simulations", json={**RUN, "gate": "off"}, headers=ADMIN)).json()
    on = (await http.post("/api/v1/simulations", json={**RUN, "gate": "on"}, headers=ADMIN)).json()
    assert off["status"] == "running" and off["gate"] == "off" and off["case_id"] is None
    await finish(http, clock)
    off = (await http.get(f"/api/v1/simulations/{off['id']}", headers=ADMIN)).json()
    on = (await http.get(f"/api/v1/simulations/{on['id']}", headers=ADMIN)).json()
    assert off["status"] == "completed" and off["outcome"].startswith("Ungoverned run, simulation only.") and off["expected_met"] is False
    assert on["status"] == "completed" and on["expected_met"] is True and not on["outcome"].startswith("Ungoverned")
    assert off["case_id"] is None and on["case_id"] is None
    assert await case_state(engine) == before


async def test_two_real_runs_can_be_compared(http, clock):
    off = (await http.post("/api/v1/simulations", json={**RUN, "gate": "off"}, headers=ADMIN)).json()
    on = (await http.post("/api/v1/simulations", json={**RUN, "gate": "on"}, headers=ADMIN)).json()
    url = f"/api/v1/simulations/compare?governed={on['id']}&ungoverned={off['id']}"
    assert (await http.get(url, headers=ADMIN)).status_code == 409  # still running
    await finish(http, clock)
    body = (await http.get(url, headers=ADMIN)).json()
    assert body["scenario_id"] == RUN["scenario_id"] and {r["aspect"] for r in body["rows"]} >= {"Gate", "Outcome"}
    assert "Ungoverned" in next(r for r in body["rows"] if r["aspect"] == "Outcome")["ungoverned"]
    swapped = await http.get(f"/api/v1/simulations/compare?governed={off['id']}&ungoverned={on['id']}", headers=ADMIN)
    assert swapped.status_code == 422 or swapped.status_code == 400


async def test_a_run_needs_a_known_scenario_pack_and_snapshot(http):
    for field, value in (("scenario_id", "scn-none"), ("pack_version_id", "pv-none"), ("kb_snapshot_id", "snap-none")):
        r = await http.post("/api/v1/simulations", json={**RUN, field: value}, headers=ADMIN)
        assert r.status_code == 404, (field, r.text)
    assert (await http.get("/api/v1/simulations/sim-none", headers=ADMIN)).status_code == 404


async def test_a_foreign_notice_run_waits_for_its_event(http, clock):
    sim = (await http.post("/api/v1/simulations", json={**RUN, "scenario_id": "scn-foreign-suspension"}, headers=ADMIN)).json()
    await finish(http, clock)
    assert (await http.get(f"/api/v1/simulations/{sim['id']}", headers=ADMIN)).json()["status"] == "waiting_for_event"

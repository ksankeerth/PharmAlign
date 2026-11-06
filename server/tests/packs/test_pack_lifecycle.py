"""A new version is simulated, approved by a second person and activated. Open cases stay on the version they started with."""
import pytest

from app.tables import ROW
from sqlalchemy import select

from ..conftest import START
from .conftest import audit_rows, count_rows, failed, headers, run_jobs

pytestmark = pytest.mark.seeded
PACK = "pv-ae-4"


async def state_of(http, version: str) -> str:
    return (await http.get(f"/api/v1/pack-versions/{version}", headers=headers("p-sam"))).json()["state"]


async def test_the_author_is_refused_and_a_second_person_approves_and_activates(http, clock):
    own = await http.post(f"/api/v1/pack-versions/{PACK}/approve", json={"reason": "Looks right to me."}, headers=headers("p-sam"))
    assert failed(own) == {"not_own_change", "pack_simulated"}
    assert "second Process Owner" in own.text
    assert await state_of(http, PACK) == "draft"

    # Nobody can approve a draft that was not simulated, and a version that is not approved cannot be activated.
    early = await http.post(f"/api/v1/pack-versions/{PACK}/approve", json={"reason": "Skipping the simulation."}, headers=headers("p-priya"))
    assert failed(early) == {"pack_simulated"}
    assert failed(await http.post(f"/api/v1/pack-versions/{PACK}/activate", headers=headers("p-priya"))) == {"pack_approved"}

    started = await http.post(f"/api/v1/pack-versions/{PACK}/simulate", json={"scenario_ids": ["scn-clean"]}, headers=headers("p-priya"))
    assert started.status_code == 202, started.text
    await run_jobs(http.app, clock)
    assert await state_of(http, PACK) == "simulated"

    approved = await http.post(f"/api/v1/pack-versions/{PACK}/approve", json={"reason": "The reliance route is abridged and labelled."}, headers=headers("p-priya"))
    assert approved.status_code == 200, approved.text
    assert approved.json()["state"] == "approved" and approved.json()["approved_by"] == "p-priya"
    assert await audit_rows(http.app.state.rt.engine, action="pack_approved", object_id=PACK) == 1

    active = await http.post(f"/api/v1/pack-versions/{PACK}/activate", headers=headers("p-priya"))
    assert active.status_code == 200 and active.json()["state"] == "active"
    assert await state_of(http, "pv-ae-3") == "retired"
    assert (await http.get("/api/v1/workspaces/ws-ae", headers=headers("p-sam"))).json()["active_pack_version_id"] == PACK
    listed = (await http.get("/api/v1/workspaces/ws-ae/packs", headers=headers("p-sam"))).json()["items"]
    assert [(p["version"], p["state"]) for p in listed][:2] == [(4, "active"), (3, "retired")]


async def test_activating_a_new_version_does_not_move_a_pinned_case(http, engine):
    case = (await http.get("/api/v1/cases/CASE-1046", headers=headers("p-alex"))).json()
    assert case["pack_version_id"] == "pv-ae-3"
    assert await state_of(http, PACK) == "active"
    assert (await http.get("/api/v1/cases/CASE-1046", headers=headers("p-alex"))).json()["pack_version_id"] == "pv-ae-3"
    # A new case starts on the active version.
    medicines = (await http.get("/api/v1/medicines", headers=headers("p-alex"))).json()["items"]
    parties = (await http.get("/api/v1/parties", headers=headers("p-alex"))).json()["items"]
    opened = await http.post("/api/v1/cases", json={"case_type": "new_registration", "route": "standard", "medicine_id": medicines[0]["id"], "applicant_party_id": parties[0]["id"]}, headers=headers("p-alex"))
    assert opened.status_code == 201, opened.text
    assert opened.json()["pack_version_id"] == PACK


async def test_one_case_can_be_moved_to_a_corrected_version_with_a_reason(http):
    moved = await http.post("/api/v1/cases/CASE-1046/repin", json={"pack_version_id": PACK, "reason": "Corrected labelling check."}, headers=headers("p-sam"))
    assert moved.status_code == 200, moved.text
    assert moved.json()["pack_version_id"] == PACK
    again = await http.post("/api/v1/cases/CASE-1046/repin", json={"pack_version_id": PACK, "reason": "Again."}, headers=headers("p-sam"))
    assert failed(again) == {"different_version"}
    other = await http.post("/api/v1/cases/CASE-1047/repin", json={"pack_version_id": "pv-ae-2", "reason": "Back."}, headers=headers("p-sam"))
    assert failed(other) == {"pack_approved"}


async def test_an_old_version_can_be_compared_with_a_new_one(http):
    diff = (await http.get(f"/api/v1/pack-versions/{PACK}/diff", params={"against": "pv-ae-3"}, headers=headers("p-sam"))).json()
    assert diff["from_version_id"] == "pv-ae-3" and diff["to_version_id"] == PACK
    default = (await http.get(f"/api/v1/pack-versions/{PACK}/diff", params={"against": "pv-ae-4"}, headers=headers("p-sam"))).json()
    assert default["changes"] == []


async def test_a_rollback_is_a_new_draft_based_on_an_older_version(http):
    r = await http.post("/api/v1/workspaces/ws-ae/packs", json={"based_on_version_id": "pv-ae-3"}, headers=headers("p-sam"))
    assert r.status_code == 201, r.text
    assert (r.json()["version"], r.json()["state"], r.json()["based_on_version_id"]) == (5, "draft", "pv-ae-3")
    both = await http.post("/api/v1/workspaces/ws-ae/packs", json={"template": "platform", "based_on_version_id": "pv-ae-3"}, headers=headers("p-sam"))
    assert both.status_code == 422

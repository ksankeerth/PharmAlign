"""A new country goes the whole way through the API: created, staffed, its library frozen, its pack approved, readiness passed,
signed off, and a case opened. Each step is one a team takes on the screens. Only the medicine and the company are put in directly,
because in a real country they arrive through the government connection."""
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db import make_sessions
from app.domain.setup import OPTIONAL_STAGES, SETUP_STAGES
from app.identity import WORKSPACE_COOKIE
from app.knowledge.embedding import get_embedder
from app.knowledge.indexing import embed_missing
from app.tables import ROW

from ..knowledge.conftest import Library, law
from .conftest import failed, headers, run_jobs

pytestmark = pytest.mark.seeded

BAHRAIN = Path(__file__).resolve().parents[3] / "demo" / "new-country" / "bahrain"
ADOPTIONS = {a["version_id"]: a for a in json.loads((BAHRAIN / "04 Set legal status" / "adoptions.json").read_text())}
COUNTRY = {"name": "Testland Live", "country_code": "TLZ", "authority_name": "Reference Authority (synthetic)", "languages": ["en"], "time_zone": "UTC",
           "day_counting": "mon_fri", "data_location": "Synthetic region", "template": "copy", "copy_from_workspace_id": "ws-ae"}
ADMIN = "p-jordan"
PACK = "pv-tlz-1"

# The national texts this country publishes itself, written so the readiness questions can find them.
NATIONAL = {
    "medicines_law": ("Testland medicines law", law("licence", suffix="Who may operate a pharmaceutical establishment: only the holder of a licence.")),
    "medicines_law_regulations": ("Testland regulations", law("inspection")),
    "fees_schedule": ("Testland service fees", law("fee", suffix="The fee applies to registering a medicine, and it is paid when applying.")),
    "registration_service": ("Testland marketing authorisation service", law("marketing authorisation")),
}
# The regional and international documents it adopts from the shared library.
SHARED = {"ctd_structure": "G-03", "stability_guideline": "E-S25", "labelling_guideline": "E-S06", "pv_guideline": "E-S24", "gmp_guideline": "E-S15", "reliance_guidance": "F-01"}


async def add_people(http) -> dict[str, str]:
    people = {}
    for key, name, view, role in (("first", "Maryam Test", "process_owner", "process_owner"), ("second", "Karim Test", "process_owner", "process_owner"),
                                  ("officer", "Alex Test", "case_officer", "case_manager"), ("signer", "Dana Test", "decision_maker", "decision_maker")):
        person = await http.post("/api/v1/people", json={"display_name": name, "view": view}, headers=headers(ADMIN))
        assert person.status_code == 201, person.text
        grant = await http.post("/api/v1/role-grants", json={"person_id": person.json()["id"], "role": role, "valid_from": "2026-11-01"}, headers=headers(ADMIN))
        assert grant.status_code == 201, grant.text
        people[key] = person.json()["id"]
    return people


async def build_library(http, lib: Library, wid: str, owner: str) -> None:
    slots = {s["key"]: s for s in (await http.get(f"/api/v1/workspaces/{wid}/slots?limit=100", headers=headers(ADMIN))).json()["items"]}
    linked = {}
    for key, (title, text) in NATIONAL.items():
        doc = await lib.register(title)
        version = (await lib.upload(doc, text))["id"]
        await lib.process(version)
        await lib.adopt(version, workspace=wid, person=owner)
        linked[key] = doc
    for key, doc in SHARED.items():
        version = f"{doc}-v1"
        adoption = ADOPTIONS.get(version, {"authority_level": 2})
        await lib.adopt(version, workspace=wid, person=owner, level=adoption["authority_level"])
        linked[key] = doc
    for key, doc in linked.items():
        filled = await http.patch(f"/api/v1/slots/{slots[key]['id']}", json={"document_id": doc, "status": "filled"}, headers=headers(ADMIN))
        assert filled.status_code == 200, filled.text


async def approve_pack(http, clock, people: dict[str, str]) -> list[dict]:
    scenarios = (await http.get("/api/v1/scenarios?limit=100", headers=headers(people["first"]))).json()["items"]
    started = await http.post(f"/api/v1/pack-versions/{PACK}/simulate", json={"scenario_ids": [s["id"] for s in scenarios]}, headers=headers(people["first"]))
    assert started.status_code == 202, started.text
    for _ in range(10):
        await run_jobs(http.app, clock)
    approved = await http.post(f"/api/v1/pack-versions/{PACK}/approve", json={"reason": "Reviewed with the first owner."}, headers=headers(people["second"]))
    assert approved.status_code == 200, approved.text
    active = await http.post(f"/api/v1/pack-versions/{PACK}/activate", headers=headers(people["second"]))
    assert active.status_code == 200, active.text
    return (await http.get("/api/v1/simulations?limit=50", headers=headers(ADMIN))).json()["items"]


async def add_watch_and_connection(http, owner: str) -> None:
    source = await http.post("/api/v1/watch/sources", json={"regulator": "Reference Agency B (synthetic)", "kind": "staged", "check_every_minutes": 120,
                                                             "severity_floor": "review"}, headers=headers(ADMIN))
    assert source.status_code == 201, source.text
    fetched = await http.post(f"/api/v1/watch/sources/{source.json()['id']}/fetch", headers=headers(ADMIN))
    assert fetched.status_code == 200, fetched.text
    made = await http.post("/api/v1/connections", json={"name": "Built-in regulator", "kind": "mock", "base_url": "/mock", "spec_version": "1.0.0"}, headers=headers(ADMIN))
    assert made.status_code == 201, made.text
    checked = await http.post(f"/api/v1/connections/{made.json()['id']}/check", headers=headers(ADMIN))
    assert checked.status_code == 200, checked.text
    approved = await http.post(f"/api/v1/connections/{made.json()['id']}/approve", json={"reason": "Conformance passed."}, headers=headers(owner))
    assert approved.status_code == 200, approved.text


async def bring_in_a_medicine_and_company(engine, wid: str) -> tuple[str, str]:
    """A copy of one seeded medicine and company, as the government connection would bring them."""
    out = []
    async with make_sessions(engine)() as db, db.begin():
        for name in ("medicine", "party"):
            table = ROW[name]
            source = (await db.scalars(select(table).where(table.tenant_id == "ws-ae").limit(1))).first()
            values = {c.key: getattr(source, c.key) for c in table.__mapper__.column_attrs}
            copy = table(**{**values, "id": f"{source.id}-tlz", "tenant_id": wid})
            copy.doc = {**source.doc, "id": copy.id}
            db.add(copy)
            out.append(copy.id)
    return out[0], out[1]


async def test_a_new_country_goes_from_creation_to_its_first_case(http, engine, clock):
    made = await http.post("/api/v1/workspaces", json=COUNTRY, headers=headers(ADMIN))
    assert made.status_code == 201, made.text
    wid = made.json()["id"]
    http.cookies.set(WORKSPACE_COOKIE, wid)
    try:
        people = await add_people(http)
        lib = Library(http, http.app, clock)
        await build_library(http, lib, wid, people["first"])

        frozen = await http.post(f"/api/v1/workspaces/{wid}/kb-snapshots", json={"label": "First freeze"}, headers=headers(people["first"]))
        assert frozen.status_code == 201, frozen.text
        assert (await http.get(f"/api/v1/workspaces/{wid}", headers=headers(ADMIN))).json()["active_kb_snapshot_id"] == frozen.json()["id"]

        simulations = await approve_pack(http, clock, people)
        assert simulations and all(s["expected_met"] for s in simulations), [(s["scenario_id"], s["status"], s.get("outcome")) for s in simulations]
        await add_watch_and_connection(http, people["first"])

        # The shared texts arrive embedded in a real library. The seed here has none, so they are embedded now.
        async with make_sessions(engine)() as db, db.begin():
            await embed_missing(db, get_embedder(http.app.state.rt.settings))
        started = await http.post(f"/api/v1/workspaces/{wid}/readiness-runs", headers=headers(ADMIN))
        assert started.status_code == 202, started.text
        await run_jobs(http.app, clock)
        run = (await http.get(f"/api/v1/workspaces/{wid}/readiness", headers=headers(ADMIN))).json()
        assert run["all_passed"], [(c["key"], c["status"], c.get("detail")) for c in run["checks"] if c["status"] not in ("pass", "mock")]
        assert {c["key"]: c["status"] for c in run["checks"]}["agents"] == "mock"

        signed = await http.post(f"/api/v1/workspaces/{wid}/sign-off", json={
            "readiness_run_id": run["id"], "pack_version_id": PACK, "kb_snapshot_id": frozen.json()["id"], "target_mode": "simulation", "reason": "Ready for simulation.",
        }, headers=headers(people["first"]))
        assert signed.status_code in (200, 201), signed.text
        after = (await http.get(f"/api/v1/workspaces/{wid}", headers=headers(ADMIN))).json()
        assert after["mode"] == "simulation"
        assert after["active_kb_snapshot_id"] == frozen.json()["id"] and after["active_pack_version_id"] == PACK

        # Every stage can be marked done, so nothing holds the country back.
        stages = (await http.get(f"/api/v1/workspaces/{wid}/setup", headers=headers(ADMIN))).json()["stages"]
        assert [x["stage"] for x in stages] == [name for name, _ in SETUP_STAGES]
        for stage in stages:
            if stage["status"] == "done":
                continue
            done = await http.post(f"/api/v1/workspaces/{wid}/setup/stages/{stage['stage']}/complete", json={}, headers=headers(ADMIN))
            assert done.status_code == 200, (stage["stage"], stage["blockers"], done.text)
        stages = (await http.get(f"/api/v1/workspaces/{wid}/setup", headers=headers(ADMIN))).json()["stages"]
        assert all(x["status"] == "done" for x in stages if x["stage"] not in OPTIONAL_STAGES), [(x["stage"], x["status"], x["blockers"]) for x in stages]

        medicine, company = await bring_in_a_medicine_and_company(engine, wid)
        case = await http.post("/api/v1/cases", json={"case_type": "registration", "medicine_id": medicine, "applicant_party_id": company},
                               headers={**headers(people["officer"]), "Idempotency-Key": "new-country-first-case"})
        assert case.status_code == 201, case.text
        assert case.json()["kb_snapshot_id"] == frozen.json()["id"] and case.json()["pack_version_id"] == PACK
        # Case numbers are shared by every country, so the UAE's next case gets another number.
        uae = await http.post("/api/v1/cases", json={"case_type": "registration", "medicine_id": "med-amoxiclav", "applicant_party_id": "pt-example"},
                              headers={**headers("p-alex"), "Idempotency-Key": "uae-case-after-new-country"})
        assert uae.status_code == 201, uae.text
        assert uae.json()["id"] != case.json()["id"]
    finally:
        http.cookies.delete(WORKSPACE_COOKIE)


async def test_a_new_freeze_stays_out_of_force_once_a_country_is_signed_off(http):
    before = (await http.get("/api/v1/workspaces/ws-ae", headers=headers(ADMIN))).json()
    assert before["mode"] == "simulation" and before["active_kb_snapshot_id"]
    frozen = await http.post("/api/v1/workspaces/ws-ae/kb-snapshots", json={"label": "Next one"}, headers=headers("p-sam"))
    assert frozen.status_code == 201, frozen.text
    assert (await http.get("/api/v1/workspaces/ws-ae", headers=headers(ADMIN))).json()["active_kb_snapshot_id"] == before["active_kb_snapshot_id"]

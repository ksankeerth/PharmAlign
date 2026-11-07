"""First-country setup: stages with blockers, readiness and sign-off."""
import pytest

from ..conftest import LiveAgents
from .conftest import add_person, failed, headers, run_jobs

pytestmark = pytest.mark.seeded


def blockers(r) -> dict[str, list[str]]:
    return {s["stage"]: s["blockers"] for s in r.json()["stages"]}


async def test_a_stage_with_blockers_is_refused_and_all_of_them_are_listed(http):
    setup = await http.get("/api/v1/workspaces/ws-om/setup", headers=headers("p-jordan"))
    assert [s["stage"] for s in setup.json()["stages"]][:3] == ["workspace", "library", "rules_and_workflows"]
    assert blockers(setup)["connections"] == ["No government connection is registered yet."]
    people = blockers(setup)["people_and_notifications"]
    assert people == ["No Decision Maker granted yet."]

    refused = await http.post("/api/v1/workspaces/ws-om/setup/stages/rules_and_workflows/complete", json={}, headers=headers("p-jordan"))
    assert refused.status_code == 409
    listed = [g["detail"] for g in refused.json()["failed"] if g["guard"] == "no_blockers"]
    assert listed == blockers(setup)["rules_and_workflows"] and "2 validation errors" in listed[0]
    assert "a decision step always ends with a person signing" in listed[0] or "decision" in listed[0]

    refused = await http.post("/api/v1/workspaces/ws-om/setup/stages/connections/complete", json={}, headers=headers("p-jordan"))
    assert failed(refused) == {"no_blockers"}
    assert "No government connection is registered yet." in refused.text


async def test_sign_off_waits_for_the_earlier_stages(http):
    refused = await http.post("/api/v1/workspaces/ws-om/setup/stages/sign_off/complete", json={}, headers=headers("p-jordan"))
    assert failed(refused) == {"earlier_stages_done"}
    done = await http.post("/api/v1/workspaces/ws-om/setup/stages/workspace/complete", json={}, headers=headers("p-jordan"))
    assert failed(done) == {"not_already_done"}


async def test_blockers_follow_the_real_state(http, engine):
    from sqlalchemy import update
    from app.db import make_sessions
    from app.tables import ROW
    before = blockers(await http.get("/api/v1/workspaces/ws-om/setup", headers=headers("p-jordan")))
    assert before["rules_and_workflows"]
    # Fixing the pack's two errors clears the stage's blocker.
    owner = await add_person(engine, "p-om-fixer", "ws-om", "process_owner")
    pack = (await http.get("/api/v1/pack-versions/pv-om-1", headers=headers(owner))).json()
    fixed = pack["document"]
    for ct in fixed["case_types"]:
        for r in ct["routes"]:
            for s in r["steps"]:
                if s["kind"] == "decide":
                    s["agent_autonomy"] = "person_signs"
    next(c for c in fixed["checks"] if c["id"] == "QUAL-07")["local_practice"] = True
    saved = await http.put("/api/v1/pack-versions/pv-om-1", json={"document": fixed}, headers=headers(owner, **{"If-Match": pack["content_hash"]}))
    assert saved.status_code == 200, saved.text
    after = blockers(await http.get("/api/v1/workspaces/ws-om/setup", headers=headers("p-jordan")))
    assert after["rules_and_workflows"] == []
    completed = await http.post("/api/v1/workspaces/ws-om/setup/stages/rules_and_workflows/complete", json={"note": "Fixed."}, headers=headers("p-jordan"))
    assert completed.status_code == 200 and completed.json()["status"] == "done" and completed.json()["completed_by"] == "p-jordan"


async def test_another_workspace_cannot_see_the_setup(http):
    assert (await http.get("/api/v1/workspaces/ws-om/setup", headers=headers("p-sam"))).status_code == 404
    assert (await http.get("/api/v1/workspaces/ws-om", headers=headers("p-sam"))).status_code == 404
    listed = await http.get("/api/v1/workspaces", headers=headers("p-jordan"))
    assert {w["id"] for w in listed.json()["items"]} >= {"ws-ae", "ws-om"}


async def test_a_readiness_run_reads_the_real_state_and_sign_off_needs_it(http, clock):
    started = await http.post("/api/v1/workspaces/ws-om/readiness-runs", headers=headers("p-jordan"))
    assert started.status_code == 202 and started.json()["status"] == "running"
    await run_jobs(http.app, clock)
    run = (await http.get("/api/v1/workspaces/ws-om/readiness", headers=headers("p-jordan"))).json()
    assert run["status"] == "completed" and run["all_passed"] is False
    assert {c["key"]: c["status"] for c in run["checks"]}["connection"] == "fail"
    refused = await http.post("/api/v1/workspaces/ws-om/sign-off", json={
        "readiness_run_id": run["id"], "pack_version_id": "pv-om-1", "kb_snapshot_id": "snap-om", "target_mode": "simulation", "reason": "Ready.",
    }, headers=headers(await add_person(http.app.state.rt.engine, "p-om-signer", "ws-om", "process_owner")))
    assert failed(refused) >= {"readiness_passed", "pack_approved", "snapshot_exists"}


async def test_a_new_country_workspace_starts_with_its_first_stage_done(http):
    body = {"name": "Testland (demo)", "country_code": "TL", "authority_name": "Reference Authority (synthetic)", "languages": ["en"],
            "time_zone": "UTC", "day_counting": "mon_fri", "data_location": "In-country region (demo)", "template": "platform"}
    made = await http.post("/api/v1/workspaces", json=body, headers=headers("p-jordan"))
    assert made.status_code == 201, made.text
    wid = made.json()["id"]
    stages = (await http.get(f"/api/v1/workspaces/{wid}/setup", headers=headers("p-jordan"))).json()["stages"]
    assert [s["status"] for s in stages] == ["done"] + ["not_started"] * 8
    assert blockers(await http.get(f"/api/v1/workspaces/{wid}/setup", headers=headers("p-jordan")))["rules_and_workflows"] == ["No pack version exists yet."]


NEW_COUNTRY = {"name": "Testland", "country_code": "TLD", "authority_name": "Reference Authority (synthetic)", "languages": ["en"],
               "time_zone": "UTC", "day_counting": "mon_fri", "data_location": "Synthetic region", "template": "platform"}


async def create_country(http, code: str, **extra):
    return await http.post("/api/v1/workspaces", json={**NEW_COUNTRY, "country_code": code, **extra}, headers=headers("p-jordan"))


async def slots_of(http, wid: str, **extra) -> list[dict]:
    return (await http.get(f"/api/v1/workspaces/{wid}/slots?limit=100", headers=headers("p-jordan", **extra))).json()["items"]


def definition(slot: dict) -> tuple:
    return slot["key"], slot["label"], slot["importance"]


async def scenarios_of(http, person: str) -> list[dict]:
    """The scenarios of the workspace this person belongs to, in id order."""
    return (await http.get("/api/v1/scenarios?limit=100", headers=headers(person))).json()["items"]


def scenario_definition(scenario: dict) -> tuple:
    from app.domain.scenario_template import scenario_key
    return scenario_key(scenario["id"]), scenario["label"], scenario["kind"], scenario["description"], scenario["expected_outcome"]


def assert_empty(slots: list[dict]) -> None:
    assert all(s["status"] == "missing" and s["document_id"] is None and s["gap_id"] is None and s["notes"] is None for s in slots)


async def test_a_platform_workspace_gets_the_standard_slots_all_empty(http):
    from app.domain.slot_template import PLATFORM_SLOTS
    made = await create_country(http, "TLA")
    assert made.status_code == 201, made.text
    slots = await slots_of(http, made.json()["id"])
    assert [definition(s) for s in slots] == [tuple(d) for d in PLATFORM_SLOTS] and len(slots) == 14
    assert_empty(slots)
    assert len({s["id"] for s in slots}) == 14 and not {s["id"] for s in slots} & {f"sl-{i:02d}" for i in range(1, 15)}
    assert sum(s["importance"] == "must" for s in slots) == 10
    # The standard list is the UAE's definitions without anything of the UAE's.
    ae = await slots_of(http, "ws-ae")
    assert [definition(s) for s in slots] == [definition(s) for s in ae]
    # Nothing was written for the other country.
    assert any(s["document_id"] for s in ae)
    stages = (await http.get(f"/api/v1/workspaces/{made.json()['id']}/setup", headers=headers("p-jordan"))).json()["stages"]
    assert next(s for s in stages if s["stage"] == "library")["blockers"][0].startswith("10 must-have slots not filled: Medicines law in force")


async def test_a_copy_without_slots_falls_back_to_the_platform_list_and_opens_a_draft_pack(http, engine):
    from app.domain.scenario_template import PLATFORM_SCENARIOS
    from app.domain.slot_template import PLATFORM_SLOTS
    made = await create_country(http, "TLB", template="copy", copy_from_workspace_id="ws-sa")
    assert made.status_code == 201, made.text
    wid = made.json()["id"]
    assert made.json()["active_pack_version_id"] is None and made.json()["mode"] == "draft"
    slots = await slots_of(http, wid)
    assert [definition(s) for s in slots] == [tuple(d) for d in PLATFORM_SLOTS]
    assert_empty(slots)
    packs = (await http.get(f"/api/v1/workspaces/{wid}/packs", headers=headers("p-jordan"))).json()["items"]
    assert [(p["id"], p["version"], p["state"], p["based_on_version_id"], p["workspace_id"]) for p in packs] == [("pv-tlb-1", 1, "draft", "pv-sa-2", wid)]
    draft = (await http.get("/api/v1/pack-versions/pv-tlb-1", headers=headers("p-jordan"))).json()
    source = (await http.get("/api/v1/pack-versions/pv-sa-2", headers=headers("p-jordan"))).json()
    assert draft["document"]["country_code"] == "TLB"
    owner = await add_person(engine, "p-tlb-owner", wid, "process_owner")
    valid = await http.post("/api/v1/pack-versions/pv-tlb-1/validate", headers=headers(owner))
    assert valid.status_code == 200 and valid.json()["valid"] is True
    # ws-sa has no scenarios, so the copy gets the platform's, and the draft can be simulated. Approval still waits for a simulation.
    assert [scenario_definition(s) for s in await scenarios_of(http, owner)] == [tuple(d) for d in sorted(PLATFORM_SCENARIOS)]
    second = await add_person(engine, "p-tlb-second-owner", wid, "process_owner")
    refused = await http.post("/api/v1/pack-versions/pv-tlb-1/approve", json={"reason": "Reviewed."}, headers=headers(second))
    assert failed(refused) == {"pack_simulated"}
    assert {**draft["document"], "country_code": "SA"} == source["document"]
    # The source country keeps its own versions.
    assert [p["id"] for p in (await http.get("/api/v1/workspaces/ws-sa/packs", headers=headers("p-jordan"))).json()["items"]] == ["pv-sa-2"]
    actions = await audit_actions(engine, wid)
    assert actions == ["workspace_created", "pack_draft_created"]


async def audit_actions(engine, tenant: str) -> list[str]:
    from sqlalchemy import select
    from app.db import make_sessions
    from app.tables import AuditEventRow
    async with make_sessions(engine)() as db:
        return list((await db.scalars(select(AuditEventRow.action).where(AuditEventRow.tenant_id == tenant).order_by(AuditEventRow.seq))).all())


async def test_a_copy_takes_the_source_slot_definitions_unlinked(http):
    made = await create_country(http, "TLC", template="copy", copy_from_workspace_id="ws-ae")
    assert made.status_code == 201, made.text
    wid = made.json()["id"]
    slots, ae = await slots_of(http, wid), await slots_of(http, "ws-ae")
    assert [definition(s) for s in slots] == [definition(s) for s in ae]
    assert_empty(slots)
    assert not {s["id"] for s in slots} & {s["id"] for s in ae}
    # ws-ae has an active pack, so the copy has a draft based on it.
    packs = (await http.get(f"/api/v1/workspaces/{wid}/packs", headers=headers("p-jordan"))).json()["items"]
    assert [(p["id"], p["state"], p["based_on_version_id"]) for p in packs] == [("pv-tlc-1", "draft", "pv-ae-3")]


async def test_a_copy_of_a_workspace_with_no_active_pack_opens_no_draft(http):
    made = await create_country(http, "TLE", template="copy", copy_from_workspace_id="ws-om")
    assert made.status_code == 201, made.text
    assert (await http.get(f"/api/v1/workspaces/{made.json()['id']}/packs", headers=headers("p-jordan"))).json()["items"] == []
    assert len(await slots_of(http, made.json()["id"])) == 14


async def test_a_copy_of_an_unknown_workspace_is_not_found(http, engine):
    from tests.handlers.conftest import count_rows
    before = await count_rows(engine, "workspace")
    refused = await create_country(http, "TLF", template="copy", copy_from_workspace_id="ws-nowhere")
    assert refused.status_code == 404 and await count_rows(engine, "workspace") == before


async def test_a_region_template_is_refused_before_anything_is_written(http, engine):
    from tests.handlers.conftest import count_rows
    before = {t: await count_rows(engine, t) for t in ("workspace", "document_slot", "pack_version", "setup_stage")}
    refused = await create_country(http, "TLG", template="region", region="gulf")
    assert refused.status_code == 422, refused.text
    assert refused.json()["code"] == "unknown_region" and refused.json()["title"] == "No regional template"
    assert refused.json()["detail"] == "No regional templates exist yet. Use the platform template or copy a workspace."
    assert {t: await count_rows(engine, t) for t in before} == before
    listed = (await http.get("/api/v1/workspaces?limit=100", headers=headers("p-jordan"))).json()["items"]
    assert "TLG" not in {w["country_code"] for w in listed}


async def test_a_platform_admin_links_a_new_workspace_slot(http):
    from app.identity import WORKSPACE_COOKIE
    made = await create_country(http, "TLH")
    wid = made.json()["id"]
    http.cookies.set(WORKSPACE_COOKIE, wid)
    try:
        slots = await slots_of(http, wid)
        target = next(s for s in slots if s["key"] == "ctd_structure")
        linked = await http.patch(f"/api/v1/slots/{target['id']}", json={"document_id": "G-03", "status": "filled"}, headers=headers("p-jordan"))
        assert linked.status_code == 200, linked.text
        assert linked.json()["document_id"] == "G-03" and linked.json()["status"] == "filled"
        again = {s["key"]: s for s in await slots_of(http, wid)}
        assert again["ctd_structure"]["status"] == "filled" and again["medicines_law"]["status"] == "missing"
        stages = (await http.get(f"/api/v1/workspaces/{wid}/setup", headers=headers("p-jordan"))).json()["stages"]
        assert next(s for s in stages if s["stage"] == "library")["blockers"][0].startswith("9 must-have slots not filled")
    finally:
        http.cookies.delete(WORKSPACE_COOKIE)
    # The seeded country's slots are untouched.
    assert next(s for s in await slots_of(http, "ws-ae") if s["key"] == "ctd_structure")["document_id"] == "G-03"


async def test_a_platform_workspace_gets_the_standard_scenarios(http, engine):
    from app.domain.scenario_template import PLATFORM_SCENARIOS
    made = await create_country(http, "TLI")
    assert made.status_code == 201, made.text
    viewer = await add_person(engine, "p-tli-owner", made.json()["id"], "process_owner")
    scenarios = await scenarios_of(http, viewer)
    assert [scenario_definition(s) for s in scenarios] == [tuple(d) for d in sorted(PLATFORM_SCENARIOS)] and len(scenarios) == 8
    # Ids are unique across workspaces, so they carry a suffix, and none is a seeded one.
    ae = await scenarios_of(http, "p-priya")
    assert all(s["id"].startswith(d.key + "-") for s, d in zip(scenarios, sorted(PLATFORM_SCENARIOS))) and len({s["id"] for s in scenarios}) == 8
    assert not {s["id"] for s in scenarios} & {s["id"] for s in ae}
    # The standard scenarios are the UAE's without anything of the UAE's.
    assert [scenario_definition(s)[:3] for s in scenarios] == [scenario_definition(s)[:3] for s in ae]
    assert "2019" not in " ".join(s["description"] for s in scenarios)
    # The other country keeps its own.
    assert len(ae) == 8 and "scn-clean" in {s["id"] for s in ae}


async def test_a_copy_from_ws_ae_gets_its_scenarios_under_new_ids(http, engine):
    made = await create_country(http, "TLJ", template="copy", copy_from_workspace_id="ws-ae")
    assert made.status_code == 201, made.text
    viewer = await add_person(engine, "p-tlj-owner", made.json()["id"], "process_owner")
    scenarios, ae = await scenarios_of(http, viewer), await scenarios_of(http, "p-priya")
    assert [scenario_definition(s) for s in scenarios] == [scenario_definition(s) for s in ae] and len(scenarios) == 8
    assert not {s["id"] for s in scenarios} & {s["id"] for s in ae}


async def test_a_copy_from_a_workspace_without_scenarios_gets_the_standard_ones(http, engine):
    from app.domain.scenario_template import PLATFORM_SCENARIOS
    made = await create_country(http, "TLK", template="copy", copy_from_workspace_id="ws-sa")
    assert made.status_code == 201, made.text
    viewer = await add_person(engine, "p-tlk-owner", made.json()["id"], "process_owner")
    assert [scenario_definition(s) for s in await scenarios_of(http, viewer)] == [tuple(d) for d in sorted(PLATFORM_SCENARIOS)]
    # The chain holds only what it did before scenarios were added.
    assert await audit_actions(engine, made.json()["id"]) == ["workspace_created", "pack_draft_created"]


async def test_a_copied_workspace_simulates_approves_and_activates_its_pack(http, engine, clock):
    from app.identity import WORKSPACE_COOKIE
    made = await create_country(http, "TLL", template="copy", copy_from_workspace_id="ws-ae")
    assert made.status_code == 201, made.text
    wid = made.json()["id"]
    pack = "pv-tll-1"
    # The Platform Admin adds two Process Owners to the new workspace.
    http.cookies.set(WORKSPACE_COOKIE, wid)
    try:
        owners = []
        for name in ("Maryam Test", "Karim Test"):
            person = await http.post("/api/v1/people", json={"display_name": name, "view": "process_owner"}, headers=headers("p-jordan"))
            assert person.status_code == 201, person.text
            grant = await http.post("/api/v1/role-grants", json={"person_id": person.json()["id"], "role": "process_owner", "valid_from": "2026-11-01"}, headers=headers("p-jordan"))
            assert grant.status_code == 201, grant.text
            owners.append(person.json()["id"])
    finally:
        http.cookies.delete(WORKSPACE_COOKIE)
    first, second = owners

    # The pack is a copy of the UAE pack, and a source clause has to resolve in the country's own library, so the country adopts the documents the pack cites.
    for doc in ("A-01", "A-03", "B-01", "B-S09", "B-S18", "E-S06", "E-S25", "F-01"):
        adopted = await http.put(f"/api/v1/workspaces/{wid}/adoptions/{doc}-v1", json={"legal_status": "in_force", "authority_level": 2}, headers=headers(first))
        assert adopted.status_code == 200, adopted.text
    snapshot = await http.post(f"/api/v1/workspaces/{wid}/kb-snapshots", json={"label": "First freeze"}, headers=headers(first))
    assert snapshot.status_code == 201, snapshot.text
    clean = next(s for s in await scenarios_of(http, first) if s["label"] == "Clean file")
    started = await http.post(f"/api/v1/pack-versions/{pack}/simulate", json={"scenario_ids": [clean["id"]]}, headers=headers(first))
    assert started.status_code == 202, started.text
    assert started.json()["scenario_id"] == clean["id"] and started.json()["kb_snapshot_id"] == snapshot.json()["id"]
    await run_jobs(http.app, clock)
    assert (await http.get(f"/api/v1/pack-versions/{pack}", headers=headers(first))).json()["state"] == "simulated"

    approved = await http.post(f"/api/v1/pack-versions/{pack}/approve", json={"reason": "Reviewed with the first owner."}, headers=headers(second))
    assert approved.status_code == 200, approved.text
    assert approved.json()["state"] == "approved" and approved.json()["approved_by"] == second
    active = await http.post(f"/api/v1/pack-versions/{pack}/activate", headers=headers(second))
    assert active.status_code == 200, active.text
    assert active.json()["state"] == "active"
    assert (await http.get(f"/api/v1/workspaces/{wid}", headers=headers("p-jordan"))).json()["active_pack_version_id"] == pack


def test_a_scenario_keeps_its_key_across_workspaces():
    from app.domain.scenario_template import FOREIGN_SUSPENSION, scenario_key
    assert scenario_key(FOREIGN_SUSPENSION) == FOREIGN_SUSPENSION
    assert scenario_key(f"{FOREIGN_SUSPENSION}-0a1b2c3d4e5f") == FOREIGN_SUSPENSION
    assert scenario_key("scn-clean") == "scn-clean"


async def test_an_agent_that_does_not_answer_fails_the_agents_check_and_blocks_its_stage(http, clock):
    http.app.state.rt.agents = LiveAgents(down=("challenger",))
    total = len(http.app.state.rt.agents.urls)
    clock.advance(minutes=1)  # so this run is the newest, whatever ran before
    await http.post("/api/v1/workspaces/ws-om/readiness-runs", headers=headers("p-jordan"))
    await run_jobs(http.app, clock)
    run = (await http.get("/api/v1/workspaces/ws-om/readiness", headers=headers("p-jordan"))).json()
    agents = next(c for c in run["checks"] if c["key"] == "agents")
    assert agents["status"] == "fail" and agents["detail"] == f"{total - 1} of {total} agents report ok."
    setup = await http.get("/api/v1/workspaces/ws-om/setup", headers=headers("p-jordan"))
    assert blockers(setup)["agents_and_limits"] == ["The challenger agent is down."]

    http.app.state.rt.agents = LiveAgents()
    clock.advance(minutes=1)
    await http.post("/api/v1/workspaces/ws-om/readiness-runs", headers=headers("p-jordan"))
    await run_jobs(http.app, clock)
    run = (await http.get("/api/v1/workspaces/ws-om/readiness", headers=headers("p-jordan"))).json()
    assert next(c for c in run["checks"] if c["key"] == "agents")["status"] == "pass"
    assert blockers(await http.get("/api/v1/workspaces/ws-om/setup", headers=headers("p-jordan")))["agents_and_limits"] == []

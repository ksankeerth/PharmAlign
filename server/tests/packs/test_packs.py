"""Pack drafts and versions through the API: reading a draft that is not finished, locked steps, four eyes, and pinned cases."""
import copy

import pytest

from app.db import make_sessions

from .conftest import add_person, failed, headers

pytestmark = pytest.mark.seeded


async def read(http, version: str, person: str = "p-sam") -> dict:
    r = await http.get(f"/api/v1/pack-versions/{version}", headers=headers(person))
    assert r.status_code == 200, r.text
    return r.json()


async def save(http, version: str, document: dict, person: str = "p-sam", match: str | None = None):
    pack = await read(http, version, person)
    return await http.put(f"/api/v1/pack-versions/{version}", json={"document": document}, headers=headers(person, **{"If-Match": match or pack["content_hash"]}))


def route(document: dict, route_id: str = "standard") -> dict:
    return next(r for ct in document["case_types"] for r in ct["routes"] if r["id"] == route_id)


# ---------------------------------------------------------------- a draft that does not pass the full model

async def test_the_oman_draft_is_readable_with_its_errors_listed(http, engine):
    owner = await add_person(engine, "p-om-owner", "ws-om", "process_owner")
    pack = await read(http, "pv-om-1", owner)
    assert pack["state"] == "draft" and pack["document"]["country_code"] == "OM"
    validation = await http.post("/api/v1/pack-versions/pv-om-1/validate", headers=headers(owner))
    body = validation.json()
    assert body["valid"] is False
    errors = {(i["rule"], i["path"]) for i in body["issues"] if i["severity"] == "error"}
    assert errors == {("decision_person_signs", "case_types[new_registration].routes[standard].steps[decide]"), ("source_required", "checks[QUAL-07]")}
    summary = await http.get("/api/v1/workspaces/ws-om/packs", headers=headers(owner))
    assert [p["id"] for p in summary.json()["items"]] == ["pv-om-1"]
    # A Platform Admin reads the same draft from another workspace.
    assert (await read(http, "pv-om-1", "p-jordan"))["id"] == "pv-om-1"
    # It is not an approved pack, so it is refused at approval and still readable afterwards.
    refused = await http.post("/api/v1/pack-versions/pv-om-1/approve", json={"reason": "Trying."}, headers=headers(owner))
    assert {"pack_valid", "pack_readable"} <= failed(refused) and (await read(http, "pv-om-1", owner))["state"] == "draft"


async def test_another_workspace_cannot_read_a_pack(http):
    assert (await http.get("/api/v1/pack-versions/pv-om-1", headers=headers("p-sam"))).status_code == 404


# ---------------------------------------------------------------- the editor's rules, enforced by the server

async def test_a_locked_step_cannot_be_removed(http):
    document = (await read(http, "pv-ae-4"))["document"]
    gone = copy.deepcopy(document)
    r = route(gone)
    r["steps"] = [s for s in r["steps"] if s["kind"] != "submit"]
    refused = await save(http, "pv-ae-4", gone)
    assert "locked_steps_kept" in failed(refused)
    assert "submit step can't be removed" in refused.text
    # The contract refuses a route with no door, decision or record step before the guard is reached.
    no_door = copy.deepcopy(document)
    route(no_door)["steps"] = [s for s in route(no_door)["steps"] if s["kind"] != "door"]
    assert (await save(http, "pv-ae-4", no_door)).status_code == 422
    # A locked step keeps its kind.
    renamed = copy.deepcopy(document)
    next(s for s in route(renamed)["steps"] if s["kind"] == "submit")["kind"] = "work"
    assert "locked_steps_kept" in failed(await save(http, "pv-ae-4", renamed))
    assert route((await read(http, "pv-ae-4"))["document"])["steps"] == route(document)["steps"]


async def test_a_new_step_must_go_before_the_decision(http):
    document = (await read(http, "pv-ae-4"))["document"]
    step = {"id": "pricing", "name": "Pricing check", "kind": "work", "role": "case_officer"}
    late = copy.deepcopy(document)
    steps = route(late)["steps"]
    steps.insert(next(i for i, s in enumerate(steps) if s["kind"] == "decide") + 1, step)
    refused = await save(http, "pv-ae-4", late)
    assert "new_steps_before_decision" in failed(refused)
    early = copy.deepcopy(document)
    steps = route(early)["steps"]
    steps.insert(next(i for i, s in enumerate(steps) if s["kind"] == "decide"), step)
    saved = await save(http, "pv-ae-4", early)
    assert saved.status_code == 200, saved.text
    kinds = [s["id"] for s in route(saved.json()["document"])["steps"]]
    assert kinds.index("pricing") < kinds.index("decide")
    assert saved.json()["content_hash"] != (await read(http, "pv-ae-3"))["content_hash"]


async def test_an_active_version_refuses_edits(http, engine):
    document = (await read(http, "pv-ae-3"))["document"]
    refused = await save(http, "pv-ae-3", document)
    assert "draft_only" in failed(refused)
    assert "Only drafts can change" in refused.text


async def test_a_stale_save_is_refused(http):
    document = (await read(http, "pv-ae-4"))["document"]
    r = await save(http, "pv-ae-4", document, match="sha256:" + "0" * 64)
    assert r.status_code == 412


async def test_an_approved_pack_cannot_be_changed_in_the_database(engine):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    async with make_sessions(engine)() as db:
        with pytest.raises(DBAPIError, match="approved pack version cannot change"):
            await db.execute(text("UPDATE pack_version SET doc = jsonb_set(doc, '{version}', '9') WHERE id = 'pv-ae-3'"))

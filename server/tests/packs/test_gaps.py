"""Gaps: candidates cite signals, a person reviews, and accepting one never edits an active pack."""
import pytest
from sqlalchemy import select

from app.db import make_sessions
from app.handlers.gaps import add_candidate
from app.tables import ROW
from pharmalign_contracts.server import models as m

from ..conftest import system_ctx
from .conftest import audit_rows, count_rows, headers, run_jobs

pytestmark = pytest.mark.seeded


async def test_a_gap_candidate_with_no_signal_is_refused(http, engine):
    from app.problems import ApiError
    gap = m.Gap(id="gap-x", workspace_id="ws-ae", title="Nothing behind it", kind="policy", status="candidate", signal_ids=[], description="No signal.")
    async with make_sessions(engine)() as db, db.begin():
        ctx = system_ctx(http.app.state.rt, db)
        with pytest.raises(ApiError) as e:
            await add_candidate(ctx, gap)
        assert e.value.body.status == 422 and "must cite at least one signal" in e.value.body.detail
        with pytest.raises(ApiError) as e:
            await add_candidate(ctx, gap.model_copy(update={"signal_ids": ["s-nope"]}))
        assert "Unknown: s-nope" in e.value.body.detail
    assert await count_rows(engine, "gap", id="gap-x") == 0
    unknown = await http.post("/api/v1/workspaces/ws-ae/gap-analyses", json={"signal_ids": ["s-nope"]}, headers=headers("p-sam"))
    assert unknown.status_code == 422


async def test_an_analysis_makes_candidates_that_each_cite_a_signal(http, clock):
    before = (await http.get("/api/v1/workspaces/ws-ae/gaps", headers=headers("p-sam"))).json()["items"]
    started = await http.post("/api/v1/workspaces/ws-ae/gap-analyses", json={"signal_ids": []}, headers=headers("p-sam"))
    assert started.status_code == 202 and started.json()["status"] == "running"
    await run_jobs(http.app, clock)
    done = (await http.get(f"/api/v1/gap-analyses/{started.json()['id']}", headers=headers("p-sam"))).json()
    assert done["status"] == "completed" and done["candidates_created"] == 2
    after = (await http.get("/api/v1/workspaces/ws-ae/gaps", headers=headers("p-sam"))).json()["items"]
    new = [g for g in after if g["id"] not in {b["id"] for b in before}]
    assert len(new) == 2 and all(g["signal_ids"] and g["status"] == "candidate" for g in new)
    # Signals already behind a gap are not counted twice.
    again = await http.post("/api/v1/workspaces/ws-ae/gap-analyses", json={"signal_ids": []}, headers=headers("p-sam"))
    await run_jobs(http.app, clock)
    assert (await http.get(f"/api/v1/gap-analyses/{again.json()['id']}", headers=headers("p-sam"))).json()["candidates_created"] == 0


async def active_packs(engine) -> dict:
    row = ROW["pack_version"]
    async with make_sessions(engine)() as db:
        rows = (await db.execute(select(row.id, row.state, row.content_hash, row.doc).where(row.tenant_id == "ws-ae"))).all()
        ws = await db.scalar(select(ROW["workspace"].active_pack_version_id).where(ROW["workspace"].id == "ws-ae"))
    return {"ws": ws, "packs": {r.id: (r.state, r.content_hash, r.doc) for r in rows}}


async def test_accepting_a_gap_never_changes_an_active_pack(http, engine):
    before = await active_packs(engine)
    r = await http.patch("/api/v1/gaps/gap-01", json={"status": "accepted", "review_note": "Add a source for the reliance routes."}, headers=headers("p-sam"))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "accepted" and r.json()["reviewed_by"] == "p-sam"
    assert "pack draft v4" in r.json()["suggested_next_step"]
    after = await active_packs(engine)
    assert after["ws"] == before["ws"] == "pv-ae-3"
    assert after["packs"]["pv-ae-3"] == before["packs"]["pv-ae-3"]
    assert after["packs"].keys() == before["packs"].keys()  # the open draft pv-ae-4 carries the change
    assert await audit_rows(engine, action="gap_change_proposed", object_id="gap-01") == 1


async def test_accepting_a_gap_opens_a_draft_when_none_is_open(http, engine):
    # Retire the open draft by approving it, so the next accepted gap needs a new one.
    async with make_sessions(engine)() as db, db.begin():
        from sqlalchemy import update
        row = ROW["pack_version"]
        await db.execute(update(row).where(row.id == "pv-ae-4").values(state="retired", approved_by="p-priya", approved_at=row.created_at))
    r = await http.patch("/api/v1/gaps/gap-02", json={"status": "accepted", "review_note": "Find a source."}, headers=headers("p-sam"))
    assert r.status_code == 200, r.text
    after = await active_packs(engine)
    assert after["ws"] == "pv-ae-3" and after["packs"]["pv-ae-3"][0] == "active"
    drafts = {k: v for k, v in after["packs"].items() if v[0] == "draft"}
    assert list(drafts) == ["pv-ae-5"]
    created = (await http.get("/api/v1/pack-versions/pv-ae-5", headers=headers("p-sam"))).json()
    assert created["created_by"] == "p-sam" and created["based_on_version_id"] is None
    # The author cannot approve it: a second person does.
    own = await http.post("/api/v1/pack-versions/pv-ae-5/approve", json={"reason": "Mine."}, headers=headers("p-sam"))
    assert own.status_code == 409


async def test_rejecting_a_gap_needs_a_reason_and_changes_no_pack(http, engine):
    r = await http.patch("/api/v1/gaps/gap-03", json={"status": "rejected", "review_note": "Not needed."}, headers=headers("p-priya"))
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert await audit_rows(engine, action="gap_change_proposed", object_id="gap-03") == 0
    assert (await http.patch("/api/v1/gaps/gap-03", json={"status": "rejected", "review_note": ""}, headers=headers("p-priya"))).status_code == 422

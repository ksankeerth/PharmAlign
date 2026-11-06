"""A notice is stored, matched and alerted in code, with every agent down."""
import pytest
from pharmalign_contracts.server import models as m

from app.watch import notices as watch_notices
from app.watch.notices import apply_assessment
from app.pipeline.runner import system_tx

from ..conftest import TENANT
from .conftest import AGENCY_A, BURST, DEAD_AGENTS, LABEL, SUSPENSION, UNKNOWN, alerts_for, client_for, deliver, headers, rows, total


async def test_the_alert_fires_with_every_agent_down(make_app, engine):
    app = make_app(settings=DEAD_AGENTS)
    async with client_for(app) as http:
        r = await http.post("/api/v1/simulations/sim-15/events", json={"kind": "foreign_notice", "notice": SUSPENSION}, headers=headers("p-jordan"))
    assert r.status_code == 200 or r.status_code == 202, r.text
    [notice] = [n for n in await rows(engine, "foreign_notice") if n.doc["title"] == SUSPENSION["title"]]
    matches = await rows(engine, "watch_match", watch_match_of(notice.id))
    assert {x.target_type for x in matches} == {"licence", "case"}
    alert = await alerts_for(engine, "p-alex", notice.id)
    assert len(alert) == 1 and alert[0].severity == "urgent" and alert[0].event == "foreign_change_matched"
    assert "Alert sent before any agent ran" in r.json()["outcome"]


def watch_match_of(notice_id):
    from app.tables import ROW

    return ROW["watch_match"].notice_id == notice_id


async def test_a_crash_between_the_notice_and_its_alert_leaves_nothing(make_app, engine, monkeypatch):
    async def crash(ctx):
        raise RuntimeError("the database went away")

    before = [await total(engine, t) for t in ("foreign_notice", "foreign_notice_revision", "watch_match", "notification_delivery", "job")]
    monkeypatch.setattr(watch_notices.alerts, "watch_officers", crash)
    app = make_app()
    async with client_for(app) as http:
        r = await http.post("/api/v1/simulations/sim-15/events", json={"kind": "foreign_notice", "notice": SUSPENSION}, headers=headers("p-jordan"))
    assert r.status_code == 500
    assert [await total(engine, t) for t in ("foreign_notice", "foreign_notice_revision", "watch_match", "notification_delivery", "job")] == before


async def test_an_unmatched_notice_is_stored_and_visible(http, engine):
    done = await deliver(http.app.state.rt, UNKNOWN)
    assert done.notice.unmatched and done.notice.match_count == 0
    [none] = await rows(engine, "watch_match", watch_match_of(done.notice.id))
    assert none.target_type == "none" and none.target_id is None
    listed = (await http.get("/api/v1/watch/notices?unmatched=true", headers=headers("p-alex"))).json()["items"]
    assert done.notice.id in [n["id"] for n in listed]
    [alert] = await alerts_for(engine, "p-alex", done.notice.id)
    assert alert.doc["title"].startswith("Unmatched notice stored")


async def test_the_same_notice_twice_is_one_notice_and_one_alert(http, engine):
    rt = http.app.state.rt
    first = await deliver(rt, SUSPENSION)
    again = await deliver(rt, SUSPENSION)
    assert (first.state, again.state) == ("new", "duplicate") and again.notice.id == first.notice.id
    assert len([n for n in await rows(engine, "foreign_notice") if n.doc["title"] == SUSPENSION["title"]]) == 1
    assert await total(engine, "foreign_notice_revision", revision_of(first.notice.id)) == 1
    assert len(await alerts_for(engine, "p-alex", first.notice.id)) == 1
    assert len(await rows(engine, "watch_match", watch_match_of(first.notice.id))) == len(first.new_matches)


def revision_of(notice_id):
    from app.tables import ROW

    return ROW["foreign_notice_revision"].notice_id == notice_id


async def test_a_corrected_notice_is_a_new_revision_and_updates_the_alert(http, engine):
    rt = http.app.state.rt
    first = await deliver(rt, SUSPENSION)
    [alert] = await alerts_for(engine, "p-alex", first.notice.id)
    ack = await http.post(f"/api/v1/notifications/{alert.id}/ack", headers=headers("p-alex"))
    assert ack.json()["state"] == "acknowledged"
    fixed = await deliver(rt, {**SUSPENSION, "text": SUSPENSION["text"] + " The suspension covers batches made before March."})
    assert fixed.state == "revised" and fixed.notice.id == first.notice.id and fixed.notice.latest_revision == 2
    assert await total(engine, "foreign_notice_revision", revision_of(first.notice.id)) == 2
    [first_rev, second_rev] = sorted(await rows(engine, "foreign_notice_revision", revision_of(first.notice.id)), key=lambda r: r.revision)
    assert first_rev.text != second_rev.text and first_rev.content_hash != second_rev.content_hash
    [alert] = await alerts_for(engine, "p-alex", first.notice.id)
    assert alert.doc["title"].startswith("Corrected (revision 2)") and alert.state == "pending" and alert.doc["acknowledged_at"] is None
    # No second match for a target the first revision already matched.
    assert len(await rows(engine, "watch_match", watch_match_of(first.notice.id))) == len(first.new_matches)


async def test_a_notice_cannot_go_below_the_packs_floor_and_an_agent_can_only_raise_it(http, engine):
    rt = http.app.state.rt
    urgent = await deliver(rt, SUSPENSION)  # the pack's floor for a suspension is urgent
    assert urgent.notice.severity == "urgent"
    async with system_tx(rt, TENANT) as ctx:
        lowered = await apply_assessment(ctx, urgent.notice.id, "suspension", "Minor.", "info", [], "run-1")
    assert lowered.severity == "urgent" and lowered.assessment.suggested_severity == "info"
    review = await deliver(rt, LABEL)  # a label change has a floor of review
    assert review.notice.severity == "review"
    [before] = await alerts_for(engine, "p-alex", review.notice.id)
    assert before.severity == "review" and before.ack_due_at is None
    async with system_tx(rt, TENANT) as ctx:
        raised_to = await apply_assessment(ctx, review.notice.id, "label_change", "This reaches patients.", "urgent", [], "run-2")
    assert raised_to.severity == "urgent"
    [after] = await alerts_for(engine, "p-alex", review.notice.id)
    assert after.severity == "urgent" and after.ack_due_at is not None


async def test_a_burst_of_licence_matches_is_one_incident(http, engine):
    done = await deliver(http.app.state.rt, BURST)
    licences = [x for x in done.new_matches if x.target_type == "licence"]
    assert len(licences) >= 3
    incidents = await rows(engine, "watch_incident", incident_of(done.notice.id))
    assert len(incidents) == 1 and incidents[0].doc["match_count"] == len(licences)
    assert incidents[0].doc["site"] == "Example Pharma, Plant 9 (synthetic)"
    listed = (await http.get("/api/v1/watch/incidents", headers=headers("p-alex"))).json()["items"]
    assert incidents[0].id in [i["id"] for i in listed]


def incident_of(notice_id):
    from app.tables import ROW

    return ROW["watch_incident"].notice_id == notice_id

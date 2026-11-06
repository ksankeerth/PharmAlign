"""A notification is one outbox event and one delivery, written together; a worker sends it once, and an urgent one escalates once."""
from app.delivery import deliver_pending
from app.jobs import Worker
from app.pipeline.runner import system_tx
from app.tables import ROW, AuditEventRow, DomainEventRow

from ..conftest import TENANT
from .conftest import headers, rows, total
from sqlalchemy import func, select
from app.db import make_sessions


async def notify(rt, **over):
    args = {"recipient": "p-alex", "event": "foreign_change_matched", "severity": "urgent", "title": "Reference approval suspended", "ack_within_minutes": 60, **over}
    async with system_tx(rt, TENANT) as ctx:
        return ctx.notify(**args)


async def sent_entries(engine, notification_id):
    async with make_sessions(engine)() as db:
        return await db.scalar(select(func.count()).select_from(AuditEventRow).where(AuditEventRow.action == "notification_sent", AuditEventRow.object_id == notification_id))


async def test_an_event_makes_one_delivery_and_a_worker_that_runs_again_sends_nothing_twice(make_app, engine):
    rt = make_app().state.rt
    assert await deliver_pending(rt) == 1  # the one seeded notification that was still pending
    event_row = DomainEventRow
    events_before = await total(engine, "domain_event", event_row.type == "notification")
    deliveries_before = await total(engine, "notification_delivery")
    notification_id = await notify(rt)
    assert await total(engine, "domain_event", event_row.type == "notification") == events_before + 1
    assert await total(engine, "notification_delivery") == deliveries_before + 1
    [pending] = await rows(engine, "notification_delivery", ROW["notification_delivery"].id == notification_id)
    assert pending.state == "pending"

    assert await deliver_pending(rt) == 1
    assert await deliver_pending(rt) == 0
    await Worker(rt).run_due()
    [sent] = await rows(engine, "notification_delivery", ROW["notification_delivery"].id == notification_id)
    assert sent.state == "sent" and await sent_entries(engine, notification_id) == 1


async def test_a_seeded_notification_is_not_delivered_again(make_app, engine):
    rt = make_app().state.rt
    await deliver_pending(rt)  # whatever was pending, now sent
    assert await total(engine, "notification_delivery", ROW["notification_delivery"].id == "nt-1") == 1
    assert await deliver_pending(rt) == 0
    assert await sent_entries(engine, "nt-1") == 0
    [seeded] = await rows(engine, "notification_delivery", ROW["notification_delivery"].id == "nt-1")
    assert seeded.state == "sent"


async def test_acknowledging_stops_the_escalation(make_app, engine, clock):
    from ..conftest import client_for

    app = make_app()
    rt = app.state.rt
    notification_id = await notify(rt)
    await deliver_pending(rt)
    async with client_for(app) as http:
        other = await http.post(f"/api/v1/notifications/{notification_id}/ack", headers=headers("p-chris"))
        assert other.status_code == 404  # not theirs
        mine = await http.post(f"/api/v1/notifications/{notification_id}/ack", headers=headers("p-alex"))
        assert mine.status_code == 200 and mine.json()["state"] == "acknowledged" and mine.json()["acknowledged_at"]
        listed = (await http.get("/api/v1/notifications?state=acknowledged", headers=headers("p-alex"))).json()["items"]
        assert notification_id in [n["id"] for n in listed]
    clock.advance(hours=2)
    await Worker(rt).run_due()
    [after] = await rows(engine, "notification_delivery", ROW["notification_delivery"].id == notification_id)
    assert after.state == "acknowledged" and after.escalation_level == 0


async def test_an_urgent_alert_nobody_acknowledges_escalates_once(make_app, engine, clock):
    rt = make_app().state.rt
    notification_id = await notify(rt)
    await deliver_pending(rt)
    await Worker(rt).run_due()
    clock.advance(minutes=59)
    await Worker(rt).run_due()
    [early] = await rows(engine, "notification_delivery", ROW["notification_delivery"].id == notification_id)
    assert early.state == "sent" and early.escalation_level == 0
    clock.advance(minutes=2)
    await Worker(rt).run_due()
    [late] = await rows(engine, "notification_delivery", ROW["notification_delivery"].id == notification_id)
    assert late.state == "escalated" and late.escalation_level == 1
    clock.advance(hours=5)
    await Worker(rt).run_due()
    from app.delivery import escalate_overdue

    await escalate_overdue(rt)
    [again] = await rows(engine, "notification_delivery", ROW["notification_delivery"].id == notification_id)
    assert again.escalation_level == 1
    async with make_sessions(engine)() as db:
        escalations = await db.scalar(select(func.count()).select_from(AuditEventRow).where(AuditEventRow.action == "notification_escalated", AuditEventRow.object_id == notification_id))
    assert escalations == 1

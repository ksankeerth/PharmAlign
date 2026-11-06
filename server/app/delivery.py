"""Sending notifications and escalating the urgent ones nobody acknowledged.

A notification is written with its change as an outbox event and a delivery in state `pending`. The worker sends pending
deliveries, once each: a row is locked and checked in the transaction that marks it sent, so running the worker again finds nothing.
"""
from typing import Optional

from pharmalign_contracts.server.models import Notification
from sqlalchemy import select

from .context import DELIVERIES, Ctx, Runtime, Tenant
from .identity import Actor
from .tables import ROW

BATCH = 100
NOTIFIER = "notifier"
ESCALATOR = "escalation-loop"


def _ctx(rt: Runtime, db, tenant_id: str, who: str) -> Ctx:
    return Ctx(rt, db, Actor(type="system", id=who, tenant_id=tenant_id), Tenant(tenant_id, rt.settings.env))


async def deliver_pending(rt: Runtime) -> int:
    """Marks pending deliveries sent. Returns how many it sent."""
    row = ROW["notification_delivery"]
    async with rt.sessions() as db, db.begin():
        found = (await db.scalars(select(row).where(row.state == "pending").order_by(row.created_at, row.id).limit(BATCH).with_for_update(skip_locked=True))).all()
        contexts: dict[str, Ctx] = {}
        for r in found:
            ctx = contexts.setdefault(r.tenant_id, _ctx(rt, db, r.tenant_id, NOTIFIER))
            sent = Notification.model_validate(r.doc).model_copy(update={"state": "sent"})
            r.doc = sent.model_dump(mode="json")
            r.state = "sent"
            ctx.audit("notification_sent", "notification", r.id, after={"state": "sent", "recipient": r.recipient})
        for ctx in contexts.values():
            await ctx.flush()
    return len(found)


async def escalate_one(ctx: Ctx, notification_id: str) -> bool:
    """Escalates one notification if it is still waiting for an acknowledgement. Does nothing a second time."""
    n = await DELIVERIES.get(ctx, notification_id, for_update=True)
    if n is None or n.state not in ("pending", "sent"):
        return False
    done = await DELIVERIES.save(ctx, n.model_copy(update={"state": "escalated", "escalation_level": n.escalation_level + 1}))
    ctx.audit("notification_escalated", "notification", n.id, before={"state": n.state}, after={"escalation_level": done.escalation_level}, actor_type="system", actor_id=ESCALATOR)
    return True


async def escalate_overdue(rt: Runtime) -> int:
    """The sweep behind the scheduled jobs: it also catches urgent notifications that were loaded with no job."""
    row = ROW["notification_delivery"]
    now = rt.clock.now()
    async with rt.sessions() as db, db.begin():
        due = (await db.execute(
            select(row.id, row.tenant_id).where(row.state.in_(("pending", "sent")), row.ack_due_at.is_not(None), row.ack_due_at <= now).order_by(row.ack_due_at, row.id).limit(BATCH)
        )).all()
    count = 0
    for notification_id, tenant_id in due:
        async with rt.sessions() as db, db.begin():
            ctx = _ctx(rt, db, tenant_id, ESCALATOR)
            count += await escalate_one(ctx, notification_id)
            await ctx.flush()
    return count


async def notifications_tick(rt: Runtime) -> int:
    return await deliver_pending(rt) + await escalate_overdue(rt)

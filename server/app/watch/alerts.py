"""The alert for a notice: one delivery per person, made in the notice's own transaction with a name that never changes.
A correction or a higher severity updates that delivery. Nothing here calls an agent."""
from datetime import timedelta
from typing import Iterable, Optional

from pharmalign_contracts.server.models import Notification
from sqlalchemy import select

from ..context import DELIVERIES, ESCALATE_JOB, NOTIFICATIONS, Ctx
from ..tables import ROW, PersonRow, RoleGrantRow
from .logic import raised

EVENT = "foreign_change_matched"
ACK_MINUTES = 60  # an urgent alert must be acknowledged within this time


def alert_id(notice_id: str, person_id: str) -> str:
    return f"nt-{notice_id}-{person_id}"


async def watch_officers(ctx: Ctx) -> list[str]:
    """Case officers who watch foreign changes: a watch officer or case manager grant."""
    stmt = (
        select(PersonRow.id, PersonRow.seq).join(RoleGrantRow, RoleGrantRow.person_id == PersonRow.id)
        .where(PersonRow.tenant_id == ctx.tenant.id, PersonRow.view == "case_officer", RoleGrantRow.role.in_(("watch_officer", "case_manager")))
    )
    return [person_id for person_id, _ in sorted(set((await ctx.db.execute(stmt)).all()), key=lambda r: r[1])]


def _ack_due(ctx: Ctx, severity: str):
    return ctx.now() + timedelta(minutes=ACK_MINUTES) if severity == "urgent" else None


def send(ctx: Ctx, notice_id: str, people: Iterable[str], severity: str, title: str, body: str, link: str) -> None:
    """The first alert for a notice."""
    for person_id in dict.fromkeys(people):
        ctx.notify(person_id, EVENT, severity, title, body, link, ack_within_minutes=ACK_MINUTES if severity == "urgent" else None, id=alert_id(notice_id, person_id))


async def update(ctx: Ctx, notice_id: str, people: Iterable[str], severity: str, title: str, body: str, link: str, options) -> None:
    """A corrected notice: the same alert, with new words, shown as unread again. A severity never goes down."""
    for person_id in dict.fromkeys(people):
        n = await DELIVERIES.get(ctx, alert_id(notice_id, person_id), for_update=True)
        if n is None:
            send(ctx, notice_id, [person_id], severity, title, body, link)
            continue
        level = raised(options, n.severity, severity)
        due = n.ack_due_at if level == n.severity and n.ack_due_at else _ack_due(ctx, level)
        again = n.model_copy(update={"title": title, "body": body, "link": link, "severity": level, "state": "pending", "acknowledged_at": None, "ack_due_at": due, "escalation_level": 0})
        await DELIVERIES.save(ctx, again)
        NOTIFICATIONS.add(ctx, again, id=ctx.id("ev"), recipient=await _recipient(ctx, n.id))
        if due:
            ctx.schedule(ESCALATE_JOB, {"id": n.id}, due)


async def _recipient(ctx: Ctx, notification_id: str) -> Optional[str]:
    return await ctx.db.scalar(select(ROW["notification_delivery"].recipient).where(ROW["notification_delivery"].id == notification_id))


async def raise_severity(ctx: Ctx, notice_id: str, severity: str, options) -> None:
    """An explanation rated the notice higher. Every alert for it follows; none goes down."""
    row = ROW["notification_delivery"]
    for n in await DELIVERIES.list(ctx, row.id.like(f"nt-{notice_id}-%")):
        level = raised(options, n.severity, severity)
        if level == n.severity:
            continue
        due = n.ack_due_at or _ack_due(ctx, level)
        await DELIVERIES.save(ctx, n.model_copy(update={"severity": level, "ack_due_at": due}))
        if due and not n.ack_due_at:
            ctx.schedule(ESCALATE_JOB, {"id": n.id}, due)

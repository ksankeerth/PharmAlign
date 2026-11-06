"""Notifications: my own, newest first. Acknowledging stops the escalation; an urgent one not acknowledged in time escalates."""
from ..context import DELIVERIES, ESCALATE_JOB
from ..delivery import escalate_one
from ..jobs import task
from ..ops import op
from ..paging import paginate
from ..problems import not_found
from ..tables import ROW
from .support import with_limit


@op("GET", "/api/v1/notifications")
async def list_notifications(req, ctx):
    row = ROW["notification_delivery"]
    mine = await DELIVERIES.list(ctx, row.recipient == ctx.person_id, *([row.state == req.query.state] if req.query.state else []))
    return paginate(sorted(mine, key=lambda n: n.created_at, reverse=True), with_limit(req.query, 100))


@op("POST", "/api/v1/notifications/{notification_id}/ack")
async def acknowledge(req, ctx):
    row = ROW["notification_delivery"]
    found = await DELIVERIES.list(ctx, row.id == req.path.notification_id, row.recipient == ctx.person_id)
    if not found:
        not_found("Notification", req.path.notification_id)
    n = await DELIVERIES.require(ctx, req.path.notification_id, for_update=True)
    if n.state != "acknowledged":
        n = await DELIVERIES.save(ctx, n.model_copy(update={"state": "acknowledged", "acknowledged_at": ctx.now()}))
        ctx.audit("notification_acknowledged", "notification", n.id, before={"state": found[0].state}, after={"state": "acknowledged"})
    return n


@task(ESCALATE_JOB)
async def escalate(payload: dict, ctx) -> None:
    """Escalates an urgent notification that was not acknowledged in time."""
    await escalate_one(ctx, payload["id"])

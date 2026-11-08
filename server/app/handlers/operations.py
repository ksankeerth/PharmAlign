"""Health and scheduled work."""
from pharmalign_contracts.common import CONTRACT_VERSION
from pharmalign_contracts.server import models as m
from pharmalign_contracts.server.models import ServerHealthDetails, TickResult
from sqlalchemy import text

from .. import jobs
from ..context import Ctx
from ..ops import op
from ..pipeline.health import agent_health, agents_are_live
from ..stores import STORES
from ..tables import ROW

VERSION = "0.1.0"


@op("GET", "/health")
async def health(req, ctx):
    return {"status": "ok"}


@op("GET", "/health/details")
async def health_details(req, ctx: Ctx):
    try:
        await ctx.db.execute(text("SELECT 1"))
        database = "ok"
    except Exception:
        database = "down"
    agents = await agent_health(ctx) if database == "ok" else []
    worker = ctx.rt.worker
    ingest_worker = "ok" if worker is not None and not worker.done() else "down"
    healthy = ingest_worker == "ok" and all(a.status in ("ok", "off") for a in agents)  # an agent that is off by configuration is not a fault
    return ServerHealthDetails(
        status="down" if database == "down" else "ok" if healthy else "degraded",
        version=VERSION, contract_version=CONTRACT_VERSION, database=database, agent_mode="http" if agents_are_live(ctx.rt) else "replay", agents=agents,
        scheduler_last_tick_at=ctx.rt.last_tick_at, escalation_loop_last_run_at=ctx.rt.last_escalations_at, ingest_worker=ingest_worker,
    )


@op("POST", "/internal/ticks")
async def tick(req, ctx: Ctx):
    started = ctx.now()
    return TickResult(started_at=started, done=await jobs.run_tick(ctx.rt, req.body.tasks))


@op("GET", "/api/v1/admin/agents/health")
async def agents_health(req, ctx):
    return {"items": await agent_health(ctx), "next_cursor": None}


@op("GET", "/api/v1/admin/agent-runs")
async def agent_runs(req, ctx):
    q = req.query
    row = ROW["agent_run"]
    where = [c for c in (row.agent == q.agent if q.agent else None, row.status == q.status if q.status else None, row.case_id == q.case_id if q.case_id else None) if c is not None]
    return await STORES["agent_run"].page(ctx, q, *where, order_by=(row.started_at.desc().nullslast(), row.id))


@op("GET", "/api/v1/admin/usage")
async def usage(req, ctx):
    reports = await STORES["usage_report"].list(ctx, order_by=ROW["usage_report"].period_to.desc())
    if reports:
        return reports[0]
    now = ctx.now()
    return m.UsageReport(period_from=now, period_to=now, agents=[])

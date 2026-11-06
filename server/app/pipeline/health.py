"""How each agent is right now. With agent services, each one is asked, and its run counts come from this workspace's runs.
Without them the server replays recordings, so the only figures are the stored ones, and a new country has none."""
import asyncio
from datetime import timedelta

from pharmalign_contracts.server import models as m
from sqlalchemy import func, select

from ..stores import STORES
from ..tables import ROW


def agents_are_live(rt) -> bool:
    return hasattr(rt.agents, "alive")


async def agent_health(ctx) -> list[m.AgentHealthView]:
    if not agents_are_live(ctx.rt):
        return await STORES["agent_health"].list(ctx)
    client = ctx.rt.agents
    names = sorted(client.urls)
    alive = await asyncio.gather(*(client.alive(name) for name in names))
    row = ROW["agent_run"]
    hour_ago = ctx.now() - timedelta(hours=1)
    rows = await ctx.db.execute(
        select(row.agent, func.max(row.started_at).filter(row.status == "completed"), func.count().filter(row.status == "failed", row.started_at >= hour_ago))
        .where(row.tenant_id == ctx.tenant.id).group_by(row.agent)
    )
    runs = {agent: (last_success, failures) for agent, last_success, failures in rows}
    return [
        m.AgentHealthView(agent=name, status="ok" if up else "down", last_success_at=runs.get(name, (None, 0))[0], failures_last_hour=runs.get(name, (None, 0))[1])
        for name, up in zip(names, alive)
    ]

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


def _off(view: m.AgentHealthView) -> m.AgentHealthView:
    return view.model_copy(update={"status": "off", "models_available": 0, "models_total": 0, "failures_last_hour": 0})


async def agent_health(ctx) -> list[m.AgentHealthView]:
    """An agent switched off by configuration shows as off. It is not asked, and it is never counted as down."""
    off = ctx.rt.settings.off_agents
    if not agents_are_live(ctx.rt):
        return [_off(v) if v.agent in off else v for v in await STORES["agent_health"].list(ctx)]
    client = ctx.rt.agents
    names = sorted(client.urls)
    asked = [name for name in names if name not in off]  # an off agent is not asked
    answers = dict(zip(asked, await asyncio.gather(*(client.alive(name) for name in asked))))
    row = ROW["agent_run"]
    hour_ago = ctx.now() - timedelta(hours=1)
    rows = await ctx.db.execute(
        select(row.agent, func.max(row.started_at).filter(row.status == "completed"), func.count().filter(row.status == "failed", row.started_at >= hour_ago))
        .where(row.tenant_id == ctx.tenant.id).group_by(row.agent)
    )
    runs = {agent: (last_success, failures) for agent, last_success, failures in rows}
    return [
        m.AgentHealthView(agent=name, status="off" if name in off else "ok" if answers[name] else "down", last_success_at=runs.get(name, (None, 0))[0],
                          failures_last_hour=0 if name in off else runs.get(name, (None, 0))[1])
        for name in names
    ]

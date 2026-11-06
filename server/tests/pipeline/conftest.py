"""The runner's tests share one small seeded case and a replay client."""
import pytest_asyncio

from . import world
from ..conftest import START


@pytest_asyncio.fixture
async def rt(make_app, clock, engine):
    from app.db import make_sessions

    async with make_sessions(engine)() as db, db.begin():
        await world.load_case(db, START)
    app = make_app()
    runtime = app.state.rt
    runtime.agents = world.replay_client(clock)
    return runtime


@pytest_asyncio.fixture
def agents(rt):
    return rt.agents

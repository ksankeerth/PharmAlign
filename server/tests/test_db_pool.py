"""A small Cloud SQL instance allows about 25 connections, so the server can be told how many to keep."""
import pytest

from app.config import Settings
from app.db import make_engine, pool_arguments
from app.main import create_app
from app.mcp.server import agent_pool_share

URL = "postgresql+asyncpg://u:p@localhost:5544/none"


def test_without_a_limit_the_engine_keeps_sqlalchemys_defaults():
    pool = make_engine(URL).pool
    assert (pool.size(), pool._max_overflow) == (5, 10)


def test_with_a_limit_the_pool_never_goes_past_it():
    pool = make_engine(URL, 5).pool
    assert (pool.size(), pool._max_overflow) == (5, 0)
    assert pool_arguments(None) == {} and pool_arguments(3) == {"pool_size": 3, "max_overflow": 0}


def test_the_tool_servers_share_is_half_and_at_least_two():
    assert [agent_pool_share(n) for n in (None, 2, 3, 5, 10)] == [None, 2, 2, 2, 5]


def test_the_setting_reaches_both_engines():
    app = create_app(settings=Settings(env="simulation", db_pool_max=6, worker_enabled=False, database_url=URL))
    assert (app.state.rt.engine.pool.size(), app.state.rt.engine.pool._max_overflow) == (6, 0)
    assert (app.state.tools.agent_engine.pool.size(), app.state.tools.agent_engine.pool._max_overflow) == (3, 0)


def test_a_limit_of_one_is_refused():
    with pytest.raises(ValueError):
        Settings(db_pool_max=1)


async def test_every_connection_plans_each_statement_for_its_own_values(database):
    from sqlalchemy import text

    from .conftest import TEST_URL

    engine = make_engine(TEST_URL, 2)
    async with engine.connect() as db:
        assert (await db.execute(text("SHOW plan_cache_mode"))).scalar_one() == "force_custom_plan"
    await engine.dispose()

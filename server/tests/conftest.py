"""Tests run against the real Postgres from compose, in their own database."""
import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
import httpx
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.clock import FixedClock
from app.config import Settings
from app.context import Ctx, Runtime, Tenant
from app.db import make_sessions
from app.identity import Actor
from app.main import create_app
from app.pipeline.client import agent_urls
from app.seed import seed_demo_people, truncate_all

# Tests use the deterministic embedder, whether or not the bge-m3 extra is installed.
os.environ.setdefault("PHARMALIGN_EMBEDDER", "hash")
TEST_URL = os.environ.get("PHARMALIGN_TEST_DATABASE_URL", "postgresql+asyncpg://pharmalign:pharmalign@localhost:5544/pharmalign_test")
SERVICE_TOKEN = "test-service-token"
START = datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc)
TENANT = "ws-ae"


def as_person(person_id: str) -> dict:
    return {"Authorization": f"Bearer {person_id}"}


SERVICE = {"Authorization": f"Bearer {SERVICE_TOKEN}"}


class LiveAgents:
    """Stands in for the agent client when the agents run: it knows their names and answers whether each one is up."""

    def __init__(self, down: tuple[str, ...] = ()):
        self.urls = {name: f"http://{name}.test" for name in agent_urls(Settings())}
        self.down = set(down)

    async def alive(self, agent: str) -> bool:
        return agent not in self.down


def client_for(app) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


class FixedIdentity:
    """A provider that isn't demo, signed in as whoever the test sets."""

    is_demo = False

    def __init__(self, view: str = "platform_admin"):
        self.view = view

    async def authenticate(self, db, request, now):
        return Actor(type="person", id="p-fixed", tenant_id=TENANT, display_name="Fixed", view=self.view)

    def session_cookies(self, person_id):
        return {}


def system_ctx(rt: Runtime, db) -> Ctx:
    return Ctx(rt, db, Actor(type="system", id="test", tenant_id=TENANT), Tenant(TENANT, rt.settings.env))


@pytest_asyncio.fixture(scope="session")
async def database():
    url = make_url(TEST_URL)
    assert "_test" in url.database, "tests drop this database"
    admin = await asyncpg.connect(user=url.username, password=url.password, host=url.host, port=url.port, database="postgres")
    await admin.execute(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)')
    await admin.execute(f'CREATE DATABASE "{url.database}"')
    await admin.close()
    config = Config(str(Path(__file__).parent.parent / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", TEST_URL)
    await asyncio.to_thread(command.upgrade, config, "head")
    await asyncio.to_thread(command.downgrade, config, "base")
    await asyncio.to_thread(command.upgrade, config, "head")


@pytest_asyncio.fixture(scope="session")
async def engine(database):
    engine = create_async_engine(TEST_URL)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def clean(engine, request):
    if request.node.get_closest_marker("seeded"):
        return  # the module loads the full seed once
    async with make_sessions(engine)() as db, db.begin():
        await truncate_all(db)
        await seed_demo_people(db, TENANT, START)


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(START)


@pytest_asyncio.fixture
async def make_app(engine, clock):
    apps = []

    def make(env: str = "simulation", settings: dict | None = None, **kwargs):
        config = Settings(env=env, database_url=TEST_URL, service_token=SERVICE_TOKEN, worker_enabled=False, **(settings or {}))
        app = create_app(settings=config, clock=kwargs.pop("clock", clock), **kwargs)
        apps.append(app)
        return app

    yield make
    for app in apps:
        await app.state.rt.engine.dispose()


@pytest.fixture
def count(engine):
    async def _count(table) -> int:
        async with make_sessions(engine)() as db:
            return await db.scalar(select(func.count()).select_from(table))

    return _count

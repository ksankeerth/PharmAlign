"""The tool server, with the demo seed loaded once per module, and an MCP client that talks to it in process."""
import json
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Optional

import httpx
import pytest_asyncio
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pharmalign_contracts.server import models as m

from app.config import Settings
from app.context import Ctx, Tenant
from app.db import make_sessions
from app.identity import Actor
from app.knowledge.embedding import get_embedder
from app.knowledge.indexing import embed_missing
from app.mcp.token import RunScope, sign
from app.pipeline.replay import load_recorded_dossier
from app.seed import load_files, truncate_all
from app.stores import STORES

from ..conftest import START, TENANT

CASE, OTHER_CASE, SNAPSHOT = "CASE-1042", "CASE-1043", "snap-ae-14"
SA_TENANT, SA_CASE, SA_SNAPSHOT = "ws-sa", "CASE-2001", "snap-sa-4"
EMBEDDER = get_embedder(Settings(env="simulation"))


@pytest_asyncio.fixture(scope="module")
async def seeded(engine):
    async with make_sessions(engine)() as db, db.begin():
        await truncate_all(db)
        await load_files(db, START)
        await load_recorded_dossier(db, TENANT)
        await embed_missing(db, EMBEDDER)


@pytest_asyncio.fixture
async def app(make_app, seeded):
    app = make_app()
    yield app
    await app.state.tools.aclose()


def system(db, tenant: str = TENANT) -> Ctx:
    return Ctx(None, db, Actor(type="system", id="test", tenant_id=tenant), Tenant(tenant, "simulation"))


async def add_run(engine, agent: str, case: str = CASE, tenant: str = TENANT, status: str = "running", is_current: bool = True) -> str:
    run_id = f"test-{agent}-{uuid.uuid4().hex[:10]}"
    async with make_sessions(engine)() as db, db.begin():
        STORES["agent_run"].add(system(db, tenant), m.AgentRunView(run_id=run_id, agent=agent, case_id=case, status=status, is_current=is_current,
                                                                   generation=1, attempt=1, started_at=START))
    return run_id


async def add_session(engine, case: Optional[str] = CASE) -> str:
    session_id = f"as-test-{uuid.uuid4().hex[:10]}"
    async with make_sessions(engine)() as db, db.begin():
        STORES["assistant_session"].add(system(db), m.AssistantSession(id=session_id, case_id=case, created_at=START), person_id="p-alex", agent_session_id="agent-session")
    return session_id


def token(app, run_id: str, agent: str, case=CASE, tenant=TENANT, snapshot=SNAPSHOT, kind="run", calls=50, expires_in=timedelta(hours=1)) -> str:
    exp = int((START + expires_in).timestamp())
    return sign(app.state.rt.settings, RunScope(kind, tenant, case, agent, run_id, snapshot, calls, exp))


async def run_token(app, engine, agent: str, **kwargs) -> str:
    case, tenant = kwargs.get("case", CASE), kwargs.get("tenant", TENANT)
    return token(app, await add_run(engine, agent, case=case, tenant=tenant), agent, **kwargs)


async def session_token(app, engine, case=CASE, **kwargs) -> str:
    session_id = await add_session(engine, case)
    return token(app, session_id, "knowledge-assistant", case=case, kind="session", **kwargs)


def http(app, token: str = "") -> httpx.AsyncClient:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers=headers, timeout=30)


@asynccontextmanager
async def connect(app, token: str):
    """What an ADK agent does: streamable HTTP to /mcp with the run token as a bearer header."""
    async with app.state.tools.serving(), http(app, token) as client, streamable_http_client("http://test/mcp", http_client=client) as (read, write, _), ClientSession(read, write) as session:
        await session.initialize()
        yield session


class Result:
    def __init__(self, raw):
        self.error = raw.isError
        self.body = json.loads(raw.content[0].text)

    def __getitem__(self, key):
        return self.body[key]


async def call(session: ClientSession, tool: str, **arguments) -> Result:
    return Result(await session.call_tool(tool, arguments))

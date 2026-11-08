"""Builds the app. Run with: uvicorn --factory app.main:create_app"""
import asyncio
import logging
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Optional

from fastapi import FastAPI

from . import handlers  # noqa: F401  binds the handlers
from .api import install_error_handlers, install_routes
from .clock import Clock, SystemClock
from .config import Settings
from .connector import make_connector
from .context import Runtime
from .db import make_engine, make_sessions
from .fetcher import make_fetcher
from .identity import DemoIdentity, IdentityProvider
from .knowledge.embedding import EmbedderUnavailable, get_embedder
from .jobs import TASKS, Worker
from .mcp.server import mount as mount_tools
from .mock_regulator.app import create_mock_app
from .ops import HANDLERS
from .pipeline import runner as pipeline_runner
from .pipeline.assistant import make_assistant_client
from .pipeline.client import make_agent_client
from .seed import seed_demo_people, set_resetting


log = logging.getLogger(__name__)
_uvicorn_log = logging.getLogger("uvicorn.error")  # shown by uvicorn at its default level


class UnsafeConfiguration(RuntimeError):
    """The server refuses to start like this."""


def _check_mock_regulator(settings: Settings) -> bool:
    """The mock regulator is mounted in simulation only. Asking for it anywhere else stops the server."""
    wanted = settings.mock_regulator if settings.mock_regulator is not None else settings.env == "simulation"
    if wanted and settings.env != "simulation":
        raise UnsafeConfiguration(f"The mock regulator needs PHARMALIGN_ENV=simulation. It is {settings.env}.")
    return wanted


def _check_tool_server(settings: Settings) -> bool:
    """The agent tool server needs a real token secret and agent login outside simulation."""
    if settings.mcp_enabled and settings.env != "simulation" and not (settings.run_token_secret and settings.agent_database_url):
        raise UnsafeConfiguration("The agent tool server needs PHARMALIGN_RUN_TOKEN_SECRET and PHARMALIGN_AGENT_DATABASE_URL outside simulation.")
    return bool(settings.mcp_enabled)


def _check_identity(settings: Settings, identity: IdentityProvider) -> None:
    if identity.is_demo and settings.env != "simulation":
        raise UnsafeConfiguration(f"Demo identity needs PHARMALIGN_ENV=simulation. It is {settings.env}.")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    rt: Runtime = app.state.rt
    if rt.identity.is_demo:
        async with rt.sessions() as db, db.begin():
            await seed_demo_people(db, rt.settings.default_tenant, rt.clock.now())
    try:
        _uvicorn_log.info("embedder: %s", get_embedder(rt.settings).name)
    except EmbedderUnavailable as e:
        _uvicorn_log.warning("embedder: none (%s)", e)
    if rt.settings.env == "simulation":
        try:
            await set_resetting(rt.sessions, False)  # a reset that was running when the last process stopped is over
        except Exception:
            log.exception("could not clear the demo reset flag")
    stop = asyncio.Event()
    if rt.settings.worker_enabled:
        await pipeline_runner.pick_up_unfinished(rt)  # a restart picks up every unfinished run
    loop = rt.worker = asyncio.create_task(Worker(rt).run_forever(stop)) if rt.settings.worker_enabled else None
    tools = getattr(app.state, "tools", None)
    async with AsyncExitStack() as stack:
        if tools:
            await stack.enter_async_context(tools.serving())
        yield
    stop.set()
    for background in list(rt.background):
        background.cancel()
    await asyncio.gather(*rt.background, return_exceptions=True)
    if loop:
        await loop
    for client in (rt.agents, rt.assistant, rt.connector, tools):
        if hasattr(client, "aclose"):
            await client.aclose()
    await rt.engine.dispose()


def create_app(settings: Optional[Settings] = None, identity: Optional[IdentityProvider] = None, clock: Optional[Clock] = None,
               handlers: Optional[dict] = None, tasks: Optional[dict] = None, agents=None, assistant=None, fetcher=None, connector=None) -> FastAPI:
    """`handlers`, `tasks`, `agents`, `assistant`, `fetcher` and `connector` add to or replace the bound ones, for tests."""
    settings = settings or Settings()
    clock = clock or SystemClock()
    identity = identity or DemoIdentity(settings.default_person)
    _check_identity(settings, identity)
    mock_regulator = _check_mock_regulator(settings)
    tool_server = _check_tool_server(settings)

    engine = make_engine(settings.database_url, settings.db_pool_max)
    sessions = make_sessions(engine)
    app = FastAPI(title="PharmAlign Server API", lifespan=_lifespan)
    app.state.rt = Runtime(
        settings=settings, clock=clock, engine=engine, sessions=sessions, identity=identity,
        handlers={**HANDLERS, **(handlers or {})}, tasks={**TASKS, **(tasks or {})},
        agents=agents or make_agent_client(settings, clock), assistant=assistant or make_assistant_client(settings, sessions),
        fetcher=fetcher or make_fetcher(settings), connector=connector or make_connector(settings),
    )
    install_routes(app)
    install_error_handlers(app)
    if mock_regulator:
        app.mount("/mock", create_mock_app(app.state.rt))
    app.state.tools = mount_tools(app, app.state.rt) if tool_server else None
    return app

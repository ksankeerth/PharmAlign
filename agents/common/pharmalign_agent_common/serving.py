"""ADK's own app (dev UI, sessions, /health) with an agent's contract routes in front."""
import os

from fastapi import APIRouter, FastAPI

from .routes import RunFn, attach_first, build_router
from .sessions import SessionSpec, build_session_router
from .settings import Settings
from .spec import AgentSpec


def _adk_base(name: str, agents_dir: str, settings: Settings) -> FastAPI:
    from google.adk.cli.fast_api import get_fast_api_app

    origins = os.getenv("ALLOW_ORIGINS")
    app = get_fast_api_app(
        agents_dir=agents_dir,
        web=True,
        allow_origins=origins.split(",") if origins else None,
        use_local_storage=False,
        otel_to_cloud=settings.otel_to_cloud,
    )
    app.title = name
    app.description = f"The {name} agent: contract routes, plus ADK's own routes"
    return app


def adk_app(spec: AgentSpec, run: RunFn, agents_dir: str, *, settings: Settings | None = None) -> FastAPI:
    """`agents_dir` is the project folder holding `app/`. Sessions are in memory, like runs."""
    settings = settings or Settings.from_env()
    app = _adk_base(spec.name, agents_dir, settings)
    attach_first(app, build_router(spec, settings, run, health=False))  # ADK already serves /health
    return app


def adk_session_app(spec: SessionSpec, agents_dir: str, *, settings: Settings | None = None) -> FastAPI:
    """Like `adk_app`, for a session agent: /sessions and /sessions/{id}/turns in front, one /health."""
    settings = settings or Settings.from_env()
    app = _adk_base(spec.name, agents_dir, settings)
    router: APIRouter = build_session_router(spec, settings, health=False)
    attach_first(app, router)
    return app

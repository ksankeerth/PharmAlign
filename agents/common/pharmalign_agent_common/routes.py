"""The contract routes every run agent serves, built from an AgentSpec.

POST {path}              start a run: 202, or 200 if the same run id and body already finished
GET {path}/{run_id}      status, and the result once finished
DELETE {path}/{run_id}   cancel
GET /health                no header needed; skipped when the host app already serves it

Runs live in memory. After a restart a poll returns 404, which the server treats as a lost run.
Errors are application/problem+json.
"""
import asyncio
import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from pharmalign_contracts.common import (
    CONTRACT_HEADER,
    CONTRACT_VERSION,
    RUN_ID_PATTERN,
    AgentInfo,
    Health,
    Problem,
    RunError,
)
from pydantic import ValidationError

from .errors import BudgetExceeded, OutputInvalid, OutputTruncated
from . import scripted
from .runner import RunOutcome, run_agent
from .settings import Settings
from .spec import AgentSpec

RUN_TTL = timedelta(hours=24)
RunFn = Callable[[Any], Awaitable[RunOutcome]]


@dataclass
class _Run:
    body_hash: str
    run: Any
    created: datetime
    task: asyncio.Task | None = None


def _now() -> datetime:
    return datetime.now(UTC)


def _json(model, status: int = 200) -> JSONResponse:
    return JSONResponse(model.model_dump(mode="json"), status_code=status, headers={CONTRACT_HEADER: CONTRACT_VERSION})


def _problem(status: int, code: str, title: str, detail: str | None = None, run_id: str | None = None) -> JSONResponse:
    problem = Problem(title=title, status=status, code=code, detail=detail[:500] if detail else None, run_id=run_id)
    return JSONResponse(
        problem.model_dump(mode="json", exclude_none=True),
        status_code=status,
        media_type="application/problem+json",
        headers={CONTRACT_HEADER: CONTRACT_VERSION},
    )


def build_router(spec: AgentSpec, settings: Settings, run: RunFn | None = None, health: bool = True) -> APIRouter:
    """The router for one agent. `run` defaults to the shared ADK runner.

    Pass `health=False` when the host app already serves `/health`, so there is exactly one."""
    run = run or (lambda request: run_agent(spec, request, settings=settings))
    router = APIRouter(tags=["contract"])
    runs: dict[str, _Run] = {}
    agent_info = AgentInfo(name=spec.name, version=spec.version(), prompt_hash=spec.prompt_hash)

    def refuse(request: Request) -> JSONResponse | None:
        """Checks the contract header."""
        sent = request.headers.get(CONTRACT_HEADER)
        if not sent:
            return _problem(422, "contract_header_missing", f"The {CONTRACT_HEADER} header is required.")
        if sent.split(".")[0] != CONTRACT_VERSION.split(".")[0]:
            return _problem(422, "contract_version_unsupported", f"This agent speaks contract {CONTRACT_VERSION}, not {sent}.")
        return None

    def bad_run_id(run_id: str) -> JSONResponse | None:
        if not re.fullmatch(RUN_ID_PATTERN, run_id):
            return _problem(422, "invalid_run_id", "The run_id breaks the contract's pattern.", run_id=run_id)
        return None

    def forget_expired() -> None:
        cutoff = _now() - RUN_TTL
        for run_id in [r for r, held in runs.items() if held.created < cutoff]:
            del runs[run_id]

    def settle(held: _Run, **fields) -> None:
        """Ends a run. Ignored if the run already ended, so a late finish can't undo a cancel."""
        if held.run.status not in ("queued", "running"):
            return
        held.run = spec.run_model.model_validate({**held.run.model_dump(), "finished_at": _now(), **fields})

    def fail(held: _Run, code: str, message: str, retryable: bool, finish_reason: str = "error") -> None:
        error = RunError(code=code, message=message[:500] or code, retryable=retryable)
        settle(held, status="failed", finish_reason=finish_reason, error=error)

    async def execute(held: _Run, req) -> None:
        held.run = held.run.model_copy(update={"status": "running", "started_at": _now()})
        deadline = asyncio.timeout(req.budget.deadline_seconds)
        try:
            async with deadline:
                outcome = await run(req)
            result = spec.result_model.model_validate(outcome.result.model_dump())
        except asyncio.CancelledError:
            settle(held, status="cancelled", finish_reason="cancelled")
            raise
        except BudgetExceeded:
            settle(held, status="stopped_early", finish_reason="budget")
        except OutputTruncated as exc:
            fail(held, "output_truncated", str(exc), True, finish_reason="length")
        except OutputInvalid as exc:
            fail(held, "invalid_output", str(exc), True)
        except ValidationError as exc:
            fail(held, "invalid_output", str(exc), True)
        except TimeoutError as exc:
            if deadline.expired():
                settle(held, status="stopped_early", finish_reason="deadline")
            else:
                fail(held, "internal_error", f"TimeoutError: {exc}", True)
        except Exception as exc:  # an honest failure, never a half result
            fail(held, "internal_error", f"{type(exc).__name__}: {exc}", True)
        else:
            settle(
                held, status="completed", finish_reason="stop", result=result, usage=outcome.usage,
                model_calls=outcome.model_calls, tool_errors=outcome.tool_errors,
            )

    @router.post(spec.path, summary="Start a run")
    async def start(request: Request) -> JSONResponse:
        if refused := refuse(request):
            return refused
        try:
            body = await request.json()
        except ValueError:
            return _problem(422, "invalid_json", "The body isn't valid JSON.")
        try:
            req = spec.request_model.model_validate(body)
        except ValidationError as exc:
            return _problem(422, "contract_violation", "The request breaks the contract.", str(exc))

        if reason := scripted.refuse_live(settings.model_mode, req.mode):
            return _problem(422, "scripted_not_for_live", "Scripted mode refuses live requests.", reason, run_id=req.run_id)
        forget_expired()
        body_hash = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        if held := runs.get(req.run_id):
            if held.body_hash != body_hash:
                return _problem(409, "run_id_reused", "This run_id already exists with a different body.", run_id=req.run_id)
            finished = held.run.status not in ("queued", "running")
            return _json(held.run, 200 if finished else 202)

        held = _Run(
            body_hash=body_hash,
            run=spec.run_model(run_id=req.run_id, status="queued", agent=agent_info),
            created=_now(),
        )
        runs[req.run_id] = held
        held.task = asyncio.create_task(execute(held, req))
        return _json(held.run, 202)

    @router.get(spec.path + "/{run_id}", summary="Run status, and the result once finished")
    async def poll(run_id: str, request: Request) -> JSONResponse:
        if refused := refuse(request) or bad_run_id(run_id):
            return refused
        forget_expired()
        if not (held := runs.get(run_id)):
            return _problem(404, "run_not_found", "No run with this id, or it has expired.", run_id=run_id)
        return _json(held.run)

    @router.delete(spec.path + "/{run_id}", summary="Cancel a run")
    async def cancel(run_id: str, request: Request) -> JSONResponse:
        if refused := refuse(request) or bad_run_id(run_id):
            return refused
        if not (held := runs.get(run_id)):
            return _problem(404, "run_not_found", "No run with this id, or it has expired.", run_id=run_id)
        settle(held, status="cancelled", finish_reason="cancelled")
        if held.task and not held.task.done():
            held.task.cancel()
            try:
                await held.task
            except asyncio.CancelledError:
                pass
        return _json(held.run)

    if health:

        @router.get("/health", summary="Liveness")
        async def health_route() -> JSONResponse:
            return _json(Health(status="ok"))

    return router


def attach_first(app: FastAPI, router: APIRouter) -> None:
    """Adds the router ahead of the app's own routes, so the contract's routes answer first."""
    before = len(app.router.routes)
    app.include_router(router)
    added = app.router.routes[before:]
    del app.router.routes[before:]
    app.router.routes[:0] = added


def create_app(spec: AgentSpec, settings: Settings | None = None, run: RunFn | None = None) -> FastAPI:
    """A plain FastAPI app with only the contract routes."""
    settings = settings or Settings.from_env()
    app = FastAPI(title=spec.name)
    app.include_router(build_router(spec, settings, run))
    return app

"""The contract routes from `openapi.yaml`: start, poll and cancel a run, and health.

This file is plumbing and is finished. It checks the token, the contract header and the request,
keeps runs in memory, and calls `app.reader.read_dossier` in the background. The agent's own work
lives in `app/reader.py`, `app/agent.py` and `app/tools.py`.

Errors are `application/problem+json`, built here so ADK's own routes keep their usual errors.
"""
import asyncio
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib.metadata import PackageNotFoundError, version

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app import reader
from app.contract.models import (
    CONTRACT_HEADER,
    CONTRACT_VERSION,
    RUN_ID_PATTERN,
    AgentInfo,
    DossierReadRequest,
    DossierReadResult,
    DossierReadRun,
    Health,
    HealthDetails,
    ModelHealth,
    Problem,
    RunError,
)

AGENT_NAME = "dossier-reader"
CONTRACT_TOOLS = ("list_documents", "read_page", "read_table", "search_dossier", "verify_quote", "lookup_term")
RUN_TTL = timedelta(hours=24)
# Only the PharmAlign server calls an agent. Locally, Postman sends this token.
SERVICE_TOKEN = os.getenv("AGENT_SERVICE_TOKEN", "local-dev-token")

router = APIRouter(tags=["contract"])


@dataclass
class _Run:
    body_hash: str
    run: DossierReadRun
    created: datetime
    task: asyncio.Task | None = None


_runs: dict[str, _Run] = {}


def _now() -> datetime:
    return datetime.now(UTC)


def _agent_version() -> str:
    try:
        return version("dossier-reader")
    except PackageNotFoundError:
        return "0.0.0"


def _agent_info() -> AgentInfo:
    from app.agent import root_agent

    instruction = root_agent.instruction if isinstance(root_agent.instruction, str) else ""
    prompt_hash = hashlib.sha256(instruction.encode()).hexdigest()
    return AgentInfo(name=AGENT_NAME, version=_agent_version(), prompt_hash=f"sha256:{prompt_hash}")


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


def _refuse(request: Request) -> JSONResponse | None:
    """Checks the token and the contract header that every route needs."""
    auth = request.headers.get("authorization", "")
    if auth != f"Bearer {SERVICE_TOKEN}":
        return _problem(401, "unauthorized", "Missing or wrong service token.")
    sent = request.headers.get(CONTRACT_HEADER)
    if not sent:
        return _problem(422, "contract_header_missing", f"The {CONTRACT_HEADER} header is required.")
    if sent.split(".")[0] != CONTRACT_VERSION.split(".")[0]:
        return _problem(422, "contract_version_unsupported", f"This agent speaks contract {CONTRACT_VERSION}, not {sent}.")
    return None


def _bad_run_id(run_id: str) -> JSONResponse | None:
    if not re.fullmatch(RUN_ID_PATTERN, run_id):
        return _problem(422, "invalid_run_id", "The run_id breaks the contract's pattern.", run_id=run_id)
    return None


def _forget_expired() -> None:
    cutoff = _now() - RUN_TTL
    for run_id in [r for r, held in _runs.items() if held.created < cutoff]:
        del _runs[run_id]


def _finish(held: _Run, **fields) -> None:
    held.run = DossierReadRun.model_validate({**held.run.model_dump(), "finished_at": _now(), **fields})


def check_result(req: DossierReadRequest, result: DossierReadResult) -> None:
    """The rules the server enforces on every result, checked here first so a bad result fails loudly."""
    asked = {f.key for f in req.fact_keys}
    doc_ids = {d.doc_id for d in req.documents}
    for fact in result.facts:
        if fact.key not in asked:
            raise ValueError(f"fact '{fact.key}' wasn't asked for")
        for ev in fact.evidence:
            if ev.anchor.split(":")[0] not in doc_ids:
                raise ValueError(f"anchor '{ev.anchor}' points at a document that isn't in this run")
    for entry in result.document_map:
        if entry.doc_id not in doc_ids:
            raise ValueError(f"document_map names '{entry.doc_id}', which isn't in this run")
        if entry.module is not None and entry.module not in req.modules:
            raise ValueError(f"module '{entry.module}' isn't one of the given modules")
    for issue in result.issues:
        if issue.doc_id not in doc_ids:
            raise ValueError(f"issue names '{issue.doc_id}', which isn't in this run")


async def _execute(held: _Run, req: DossierReadRequest) -> None:
    held.run = held.run.model_copy(update={"status": "running", "started_at": _now()})
    try:
        outcome = await asyncio.wait_for(reader.read_dossier(req), timeout=req.budget.deadline_seconds)
        result = DossierReadResult.model_validate(outcome.result.model_dump())
        check_result(req, result)
    except asyncio.CancelledError:
        _finish(held, status="cancelled", finish_reason="cancelled")
        raise
    except TimeoutError:
        _finish(held, status="stopped_early", finish_reason="deadline")
    except reader.BudgetExceeded:
        _finish(held, status="stopped_early", finish_reason="budget")
    except NotImplementedError as exc:
        error = RunError(code="not_implemented", message=str(exc)[:500] or "Not built yet.", retryable=False)
        _finish(held, status="failed", finish_reason="error", error=error)
    except (ValidationError, ValueError) as exc:
        error = RunError(code="invalid_output", message=str(exc)[:500], retryable=True)
        _finish(held, status="failed", finish_reason="error", error=error)
    except Exception as exc:  # an honest failure, never a half result
        error = RunError(code="internal_error", message=f"{type(exc).__name__}: {exc}"[:500], retryable=True)
        _finish(held, status="failed", finish_reason="error", error=error)
    else:
        _finish(
            held, status="completed", finish_reason="stop", result=result, usage=outcome.usage,
            model_calls=outcome.model_calls, tool_errors=outcome.tool_errors,
        )


@router.post("/dossier-reads", summary="Start a run")
async def start(request: Request) -> JSONResponse:
    if refused := _refuse(request):
        return refused
    try:
        body = await request.json()
    except ValueError:
        return _problem(422, "invalid_json", "The body isn't valid JSON.")
    try:
        req = DossierReadRequest.model_validate(body)
    except ValidationError as exc:
        return _problem(422, "contract_violation", "The request breaks the contract.", str(exc))

    _forget_expired()
    body_hash = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    if held := _runs.get(req.run_id):
        if held.body_hash != body_hash:
            return _problem(409, "run_id_reused", "This run_id already exists with a different body.", run_id=req.run_id)
        return _json(held.run, 200)

    held = _Run(body_hash=body_hash, run=DossierReadRun(run_id=req.run_id, status="queued", agent=_agent_info()), created=_now())
    _runs[req.run_id] = held
    held.task = asyncio.create_task(_execute(held, req))
    return _json(held.run, 202)


@router.get("/dossier-reads/{run_id}", summary="Run status, and the result once finished")
async def poll(run_id: str, request: Request) -> JSONResponse:
    if refused := _refuse(request) or _bad_run_id(run_id):
        return refused
    _forget_expired()
    if not (held := _runs.get(run_id)):
        return _problem(404, "run_not_found", "No run with this id, or it has expired.", run_id=run_id)
    return _json(held.run)


@router.delete("/dossier-reads/{run_id}", summary="Cancel a run")
async def cancel(run_id: str, request: Request) -> JSONResponse:
    if refused := _refuse(request) or _bad_run_id(run_id):
        return refused
    if not (held := _runs.get(run_id)):
        return _problem(404, "run_not_found", "No run with this id, or it has expired.", run_id=run_id)
    if held.task and not held.task.done():
        held.task.cancel()
        try:
            await held.task
        except asyncio.CancelledError:
            pass
    if held.run.status in ("queued", "running"):  # cancelled before the task started
        _finish(held, status="cancelled", finish_reason="cancelled")
    return _json(held.run)


@router.get("/health", summary="Liveness")
async def health(request: Request) -> JSONResponse:
    if refused := _refuse(request):
        return refused
    return _json(Health(status="ok"))


@router.get("/health/details", summary="Models and tools. Holds no document content.")
async def health_details(request: Request) -> JSONResponse:
    if refused := _refuse(request):
        return refused
    from app.agent import MODEL, root_agent

    wired = [getattr(t, "__name__", getattr(t, "name", "")) for t in root_agent.tools]
    missing = [t for t in CONTRACT_TOOLS if t not in wired]
    details = HealthDetails(
        status="degraded" if missing else "ok",
        agent=AGENT_NAME,
        version=_agent_version(),
        contract_version=CONTRACT_VERSION,
        models=[ModelHealth(role="reader", model=MODEL, available=True)],
        tools=[t for t in wired if re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", t)],
    )
    return _json(details)


def attach_contract_routes(app: FastAPI) -> None:
    """Adds the contract routes ahead of ADK's own, so the contract's /health answers first."""
    before = len(app.router.routes)
    app.include_router(router)
    added = app.router.routes[before:]
    del app.router.routes[before:]
    app.router.routes[:0] = added

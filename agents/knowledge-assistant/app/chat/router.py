"""The session routes for the pipeline. Knowledge-only sessions run the pipeline; case sessions keep today's ADK agent.

POST /sessions              open a session: 201 {agent_session_id}
POST /sessions/{id}/turns   one turn, streamed as server-sent events
GET /health                 only when `health` is True

Rules:
- A session with a case_id, and any turn for an id this router does not hold, go to the common session router
  (today's agent). It answers 404 session_not_found for ids nobody holds.
- The classifier and answer chains are built on the first knowledge turn, so the app starts without credentials.
- Same limits as the common router: SESSION_TTL, MAX_SESSIONS, one turn at a time per session, the turn deadline.
"""
import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from google.adk.models.base_llm import BaseLlm
from google.adk.tools.mcp_tool import McpToolset
from pharmalign_agent_common import SessionSpec, Settings, ToolLog, build_session_router
from pharmalign_agent_common import scripted
from pharmalign_agent_common.routes import _json, _problem
from pharmalign_agent_common.sessions import MAX_SESSIONS, SESSION_TTL, TOO_SLOW, _sse, keepalive
from pharmalign_contracts.agents.knowledge_assistant import (
    ErrorEvent,
    SessionCreate,
    SessionCreated,
    TurnRequest,
    UsageEvent,
)
from pharmalign_contracts.common import CONTRACT_HEADER, CONTRACT_VERSION, Health

from app.chat.models import ModelChain, chain_from_env
from app.chat.pipeline import PipelineSession, run_knowledge_turn
from app.chat.retrieve import CallTool, mcp_call_tool, session_toolset

CallToolFactory = Callable[[McpToolset], CallTool]


def _now() -> datetime:
    return datetime.now(UTC)


PIPELINE_MCP_TIMEOUT = 30.0  # a live search takes 6-9 s on the eval server, and two run together


def pipeline_settings(settings: Settings) -> Settings:
    """The settings for the pipeline's own toolset: a longer MCP timeout than common's 10 s. Case sessions keep theirs."""
    return replace(settings, mcp_timeout_seconds=float(os.getenv("PHARMALIGN_KA_MCP_TIMEOUT") or PIPELINE_MCP_TIMEOUT))


def _endpoint(router: APIRouter, path: str, method: str):
    for route in router.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", ()):
            return route.endpoint  # type: ignore[attr-defined]
    raise RuntimeError(f"the common session router has no {method} {path}")


def build_pipeline_router(
    spec: SessionSpec,
    settings: Settings,
    *,
    classifier: ModelChain | None = None,
    answerer: ModelChain | None = None,
    case_model: BaseLlm | None = None,
    call_tool_factory: CallToolFactory | None = None,
    turn_log_path: str | None = None,
    health: bool = True,
) -> APIRouter:
    router = APIRouter(tags=["contract"])
    sessions: dict[str, PipelineSession] = {}
    closing: set[asyncio.Task] = set()
    chains: dict[str, ModelChain] = {}
    make_call_tool = call_tool_factory or mcp_call_tool

    case_router = build_session_router(spec, settings, health=False, model=case_model)
    case_open = _endpoint(case_router, spec.path, "POST")
    case_turn = _endpoint(case_router, spec.path + "/{session_id}/turns", "POST")

    def chain(role: str) -> ModelChain:
        given = classifier if role == "classifier" else answerer
        if given is not None:
            return given
        if role not in chains:
            chains[role] = chain_from_env(role)  # type: ignore[arg-type]
        return chains[role]

    def refuse(request: Request) -> JSONResponse | None:
        sent = request.headers.get(CONTRACT_HEADER)
        if not sent:
            return _problem(422, "contract_header_missing", f"The {CONTRACT_HEADER} header is required.")
        if sent.split(".")[0] != CONTRACT_VERSION.split(".")[0]:
            return _problem(422, "contract_version_unsupported", f"This agent speaks contract {CONTRACT_VERSION}, not {sent}.")
        return None

    def drop(session_id: str) -> None:
        held = sessions.pop(session_id, None)
        if held and held.toolset is not None:
            task = asyncio.get_running_loop().create_task(held.toolset.close())
            closing.add(task)
            task.add_done_callback(closing.discard)

    def forget_old() -> None:
        cutoff = _now() - SESSION_TTL
        for session_id in [i for i, s in sessions.items() if s.created < cutoff]:
            drop(session_id)
        while len(sessions) >= MAX_SESSIONS:
            drop(next(iter(sessions)))

    @router.post(spec.path, summary="Open a session", status_code=201)
    async def open_session(request: Request) -> JSONResponse:
        if refused := refuse(request):
            return refused
        try:
            create = SessionCreate.model_validate(await request.json())
        except ValueError as exc:  # bad JSON, or a ValidationError
            return _problem(422, "contract_violation", "The request breaks the contract.", str(exc))
        if create.case_id:
            return await case_open(request)  # a case session keeps today's agent
        if not settings.mcp_url:
            return _problem(503, "tools_not_configured", "PHARMALIGN_MCP_URL is not set.")
        if reason := scripted.refuse_live(settings.model_mode, create.mode):
            return _problem(422, "scripted_not_for_live", "Scripted mode refuses live requests.", reason)
        forget_old()
        session_id = f"asa-{uuid.uuid4().hex[:12]}"
        sessions[session_id] = PipelineSession(
            session_id, create, ToolLog(), [], session_toolset(pipeline_settings(settings), create.run_token), _now()
        )
        return _json(SessionCreated(agent_session_id=session_id), 201)

    @router.post(spec.path + "/{session_id}/turns", summary="One turn, streamed")
    async def turn(session_id: str, request: Request):
        if refused := refuse(request):
            return refused
        forget_old()
        held = sessions.get(session_id)
        if held is None:
            return await case_turn(session_id, request)  # a case session, or 404 session_not_found
        try:
            turn_request = TurnRequest.model_validate(await request.json())
        except ValueError as exc:
            return _problem(422, "contract_violation", "The request breaks the contract.", str(exc))

        async def work() -> tuple[list[dict], object, ErrorEvent | None]:
            out: list[dict] = []
            usage = None
            error: ErrorEvent | None = None
            try:
                async with asyncio.timeout(spec.turn_seconds):
                    out, usage, error = await run_knowledge_turn(
                        held, turn_request, chain("classifier"), chain("answer"),
                        make_call_tool(held.toolset), turn_log_path,
                    )
            except asyncio.CancelledError:
                raise
            except TimeoutError:
                error = ErrorEvent(code="deadline_exceeded", message=TOO_SLOW, retryable=True)
            except Exception as exc:  # an honest error event, never half an answer
                error = ErrorEvent(code="internal_error", message=f"{type(exc).__name__}: {exc}"[:500], retryable=True)
            return out, usage, error

        async def events() -> AsyncIterator[str]:
            async with held.lock:  # one turn at a time per session
                task = asyncio.ensure_future(work())
                async for beat in keepalive(task, spec.keepalive_seconds):
                    yield beat
                out, usage, error = task.result()
                for event in out:
                    yield _sse(event)
                if usage:
                    yield _sse(UsageEvent(usage=usage).model_dump(mode="json"))
                if error:
                    yield _sse(error.model_dump(mode="json"))

        return StreamingResponse(
            events(), media_type="text/event-stream",
            headers={CONTRACT_HEADER: CONTRACT_VERSION, "Cache-Control": "no-cache"},
        )

    if health:

        @router.get("/health", summary="Liveness")
        async def health_route() -> JSONResponse:
            return _json(Health(status="ok"))

    return router

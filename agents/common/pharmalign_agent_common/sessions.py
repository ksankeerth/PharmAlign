"""A session agent: chat sessions with streamed turns (the Knowledge Assistant).

POST /sessions              open a session: 201 {agent_session_id}
POST /sessions/{id}/turns   one turn, streamed as server-sent events: `data: {event json}`
GET /health                 no header needed; skipped when the host app already serves it

Rules this file keeps:
- Sessions live in memory. After a restart a turn returns 404: the caller starts a new session.
- The session's run token goes out as a bearer header on every tool call of that session, and
  nowhere else. A knowledge-only session (no case_id) gets no case tools.
- The model answers each turn with one JSON object holding a list of events. Only the event types
  in `model_events` are allowed. The answer is checked by the agent's rules first, then streamed:
  nothing leaves before it passes. One retry with the reason, then an error event.
- The tool log lasts for the session, so a citation may rest on a tool result from an earlier turn.
"""
import asyncio
import hashlib
import json
import re
import uuid
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from pharmalign_contracts.agents.knowledge_assistant import (
    EVENT_ADAPTER,
    ErrorEvent,
    SessionCreate,
    SessionCreated,
    TURN_MAX_MODEL_CALLS,
    TURN_MAX_TOOL_CALLS,
    TURN_SECONDS,
    TurnRequest,
    UsageEvent,
)
from pharmalign_contracts.common import CONTRACT_HEADER, CONTRACT_VERSION, Health
from pydantic import BaseModel, ValidationError

from .errors import BudgetExceeded, OutputInvalid, OutputTruncated
from .evidence import ToolLog
from .routes import _json, _problem
from . import scripted
from .runner import _ask, _callbacks, _FENCE, RunTracker, build_model, toolset_for
from .settings import Settings

SESSION_TTL = timedelta(hours=24)
MAX_SESSIONS = 500
CHUNK_WORDS = 8  # words per text_delta


@dataclass
class Turn:
    """What a rule needs to know about this turn."""

    session: SessionCreate
    request: TurnRequest
    calls_before: int  # tool calls logged before this turn began

    @property
    def case_session(self) -> bool:
        return self.session.case_id is not None


@dataclass(frozen=True)
class SessionSpec:
    name: str  # such as "knowledge-assistant"
    adk_name: str
    instruction: str
    default_model: str
    answer_model: type[BaseModel]  # {"events": [...]}: what the model answers each turn
    model_events: frozenset[str]  # the only event types the model may emit
    knowledge_tools: tuple[str, ...]
    case_tools: tuple[str, ...]
    build_message: Callable[[SessionCreate, TurnRequest], str]
    check_turn: Callable[[Turn, Any, ToolLog], None]  # raise ValueError to reject
    variants: Mapping[str, str] = field(default_factory=dict)  # "knowledge" and "case", added after the instruction
    max_model_calls: int = TURN_MAX_MODEL_CALLS  # per turn, as the contract sets it
    max_tool_calls: int = TURN_MAX_TOOL_CALLS
    turn_seconds: int = TURN_SECONDS

    @property
    def path(self) -> str:
        return "/sessions"

    @property
    def prompt_hash(self) -> str:
        text = self.instruction + "".join(f"\n\n[{k}]\n{v}" for k, v in sorted(self.variants.items()))
        return "sha256:" + hashlib.sha256(text.encode()).hexdigest()

    def tools_for(self, session: SessionCreate) -> tuple[str, ...]:
        return self.knowledge_tools + (self.case_tools if session.case_id else ())

    def instruction_for(self, session: SessionCreate) -> str:
        extra = self.variants.get("case" if session.case_id else "knowledge")
        return self.instruction + ("\n\n" + extra if extra else "")

    def version(self) -> str:
        try:
            return version(self.name)
        except PackageNotFoundError:
            return "0.0.0"


def build_session_agent(
    spec: SessionSpec,
    settings: Settings,
    session: SessionCreate,
    *,
    model: BaseLlm | None = None,
    tracker: RunTracker | None = None,
) -> LlmAgent:
    """The ADK agent for one session: its instruction, its tools and its token."""
    if model is None and settings.model_mode == "scripted":
        model = scripted.model_for_session(spec.name)
    model = model or build_model(spec, settings)  # type: ignore[arg-type]
    text = spec.instruction_for(session)
    callbacks = _callbacks(tracker, getattr(model, "model", "")) if tracker is not None else {}
    return LlmAgent(
        name=spec.adk_name,
        model=model,
        instruction=lambda _: text,  # a function, so ADK doesn't template the text
        output_schema=spec.answer_model,
        tools=[toolset_for(spec.tools_for(session), settings, session.run_token)] if settings.mcp_url else [],
        **callbacks,
    )


def parse_answer(spec: SessionSpec, turn: Turn, text: str, log: ToolLog) -> Any:
    """Checks the model's answer: JSON, only allowed event types, the schema, then the agent's rules."""
    text = text.strip()
    if fenced := _FENCE.match(text):
        text = fenced.group(1)
    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise OutputInvalid("the answer isn't one JSON object") from exc
    events = raw.get("events") if isinstance(raw, dict) else None
    if isinstance(events, list):
        bad = sorted({str(e.get("type")) if isinstance(e, dict) else "?" for e in events
                      if not isinstance(e, dict) or e.get("type") not in spec.model_events})
        if bad:
            raise OutputInvalid(
                f"event type {', '.join(bad)} is outside the allowed set. Use only: {', '.join(sorted(spec.model_events))}"
            )
    try:
        answer = spec.answer_model.model_validate(raw)
        spec.check_turn(turn, answer, log)
    except (ValidationError, ValueError) as exc:
        raise OutputInvalid(str(exc)) from exc
    return answer


def _deltas(text: str) -> list[dict]:
    words = re.findall(r"\S+\s*", text)
    return [{"type": "text_delta", "text": "".join(words[i:i + CHUNK_WORDS])} for i in range(0, len(words), CHUNK_WORDS)]


def stream_events(answer: Any) -> list[dict]:
    """The events to send for a checked answer: each message is preceded by its text in pieces."""
    out: list[dict] = []
    for event in answer.events:
        data = event.model_dump(mode="json")
        if data["type"] == "message":
            out.extend(_deltas(data["text"]))
        out.append(data)
    return out


@dataclass
class _Session:
    id: str
    create: SessionCreate
    runner: Runner
    tracker: RunTracker
    created: datetime
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def _now() -> datetime:
    return datetime.now(UTC)


def _new_turn(spec: SessionSpec, tracker: RunTracker) -> int:
    """Resets the per-turn counters. The tool log stays. Returns how many tool calls it already holds."""
    tracker.max_model_calls, tracker.max_tool_calls = spec.max_model_calls, spec.max_tool_calls
    tracker.model_calls, tracker.tool_errors = [], []
    tracker.tool_calls = tracker.started = 0
    tracker.truncated, tracker.exceeded = False, None
    return len(tracker.log.calls)


async def run_turn(spec: SessionSpec, held: _Session, request: TurnRequest) -> Any:
    """One turn. Returns the checked answer. Raises BudgetExceeded, OutputInvalid or OutputTruncated."""
    tracker = held.tracker
    turn = Turn(held.create, request, _new_turn(spec, tracker))
    text = await _ask(held.runner, held.id, spec.build_message(held.create, request), tracker)
    try:
        return parse_answer(spec, turn, text, tracker.log)
    except OutputInvalid as first:
        retry = f"Your answer was rejected: {first}\nReply again with only the corrected JSON for the result schema."
        text = await _ask(held.runner, held.id, retry, tracker)
        return parse_answer(spec, turn, text, tracker.log)  # a second failure ends the turn


def _sse(event: dict) -> str:
    return f"data: {json.dumps(EVENT_ADAPTER.validate_python(event).model_dump(mode='json'), ensure_ascii=False)}\n\n"


def _failure(exc: BaseException, tracker: RunTracker) -> ErrorEvent:
    if isinstance(exc, BudgetExceeded) or tracker.exceeded:
        return ErrorEvent(code="budget_exceeded", message=tracker.exceeded or str(exc) or "budget used", retryable=True)
    if isinstance(exc, OutputTruncated):
        return ErrorEvent(code="output_truncated", message=str(exc), retryable=True)
    if isinstance(exc, OutputInvalid):
        return ErrorEvent(code="invalid_output", message=str(exc)[:500], retryable=True)
    return ErrorEvent(code="internal_error", message=f"{type(exc).__name__}: {exc}"[:500], retryable=True)


def build_session_router(
    spec: SessionSpec, settings: Settings, *, health: bool = True, model: BaseLlm | None = None
) -> APIRouter:
    """The session routes. `model` is for tests. Pass `health=False` when the host app serves `/health`."""
    router = APIRouter(tags=["contract"])
    sessions: dict[str, _Session] = {}
    closing: set[asyncio.Task] = set()

    def refuse(request: Request) -> JSONResponse | None:
        sent = request.headers.get(CONTRACT_HEADER)
        if not sent:
            return _problem(422, "contract_header_missing", f"The {CONTRACT_HEADER} header is required.")
        if sent.split(".")[0] != CONTRACT_VERSION.split(".")[0]:
            return _problem(422, "contract_version_unsupported", f"This agent speaks contract {CONTRACT_VERSION}, not {sent}.")
        return None

    def drop(session_id: str) -> None:
        held = sessions.pop(session_id, None)
        if held:
            task = asyncio.get_running_loop().create_task(held.runner.close())
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
        if not settings.mcp_url:
            return _problem(503, "tools_not_configured", "PHARMALIGN_MCP_URL is not set.")
        if reason := scripted.refuse_live(settings.model_mode, create.mode):
            return _problem(422, "scripted_not_for_live", "Scripted mode refuses live requests.", reason)
        forget_old()
        tracker = RunTracker(spec.max_model_calls, spec.max_tool_calls)
        agent = build_session_agent(spec, settings, create, model=model, tracker=tracker)
        runner = Runner(app_name=spec.adk_name, agent=agent, session_service=InMemorySessionService(), auto_create_session=True)
        session_id = f"asa-{uuid.uuid4().hex[:12]}"
        sessions[session_id] = _Session(session_id, create, runner, tracker, _now())
        return _json(SessionCreated(agent_session_id=session_id), 201)

    @router.post(spec.path + "/{session_id}/turns", summary="One turn, streamed")
    async def turn(session_id: str, request: Request):
        if refused := refuse(request):
            return refused
        forget_old()
        held = sessions.get(session_id)
        if held is None:
            return _problem(404, "session_not_found", "No session with this id. Start a new one.")
        try:
            turn_request = TurnRequest.model_validate(await request.json())
        except ValueError as exc:
            return _problem(422, "contract_violation", "The request breaks the contract.", str(exc))

        async def events() -> AsyncIterator[str]:
            async with held.lock:  # one turn at a time per session
                error: ErrorEvent | None = None
                out: list[dict] = []
                try:
                    async with asyncio.timeout(spec.turn_seconds):
                        out = stream_events(await run_turn(spec, held, turn_request))
                except asyncio.CancelledError:
                    raise
                except TimeoutError:
                    error = ErrorEvent(code="deadline_exceeded", message=f"the turn took more than {spec.turn_seconds} seconds", retryable=True)
                except Exception as exc:  # an honest error event, never half an answer
                    error = _failure(exc, held.tracker)
                for event in out:
                    yield _sse(event)
                usage = held.tracker.usage()
                if usage.model_calls or usage.tool_calls:
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

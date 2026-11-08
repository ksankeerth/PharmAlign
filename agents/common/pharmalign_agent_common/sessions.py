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
import time
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
from google.adk.planners import BuiltInPlanner
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
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
    # Opt-in controls for an agent whose model works in a loop of tool calls (all off by default, so other agents run as before):
    keepalive_seconds: float | None = None  # a comment line every this many seconds while a turn works, so the caller's idle limit is not hit
    thinking_level: str | None = None  # "minimal", "low", "medium" or "high": the model's thinking in each round (a long default makes rounds slow)
    soft_seconds: float | None = None  # this long into a turn, every model call is told to answer now with what it has
    model_call_seconds: float | None = None  # one model call that takes longer is cancelled and tried once more (a hung call must not use up the turn)
    max_output_tokens: int | None = None  # a cap on one model call's output (thinking included), so a call that runs on without end is cut short
    soft_rounds: int | None = None  # from this many model calls into a turn, every model call is told to answer now
    repeat_answer: str | None = None  # a tool call the same as an earlier one in the turn is not sent again: the model gets this text

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
    if spec.model_call_seconds:
        model = TimedModel(model=model.model, inner=model, seconds=spec.model_call_seconds)
    text = spec.instruction_for(session)
    callbacks = _tuned(spec, _callbacks(tracker, getattr(model, "model", "")), tracker) if tracker is not None else {}
    extra: dict[str, Any] = {}
    if spec.thinking_level:
        level = types.ThinkingLevel(spec.thinking_level.upper())
        extra["planner"] = BuiltInPlanner(thinking_config=types.ThinkingConfig(thinking_level=level))
    if spec.max_output_tokens:
        extra["generate_content_config"] = types.GenerateContentConfig(max_output_tokens=spec.max_output_tokens)
    return LlmAgent(
        name=spec.adk_name,
        model=model,
        instruction=lambda _: text,  # a function, so ADK doesn't template the text
        output_schema=spec.answer_model,
        tools=[toolset_for(spec.tools_for(session), settings, session.run_token)] if settings.mcp_url else [],
        **callbacks,
        **extra,
    )


class TimedModel(BaseLlm):
    """Wraps a model so that one call that hangs is cancelled after `seconds` and tried once more, then fails with a TimeoutError."""

    inner: BaseLlm
    seconds: float
    attempts: int = 2

    async def generate_content_async(self, llm_request, stream: bool = False):
        for _ in range(self.attempts):
            try:
                async with asyncio.timeout(self.seconds):
                    responses = [r async for r in self.inner.generate_content_async(llm_request, stream=stream)]
            except TimeoutError:
                continue
            for response in responses:
                yield response
            return
        raise TimeoutError(f"the model did not answer within {self.seconds:g} seconds, {self.attempts} times")


ANSWER_NOW = (
    "Time for this answer is nearly up. Call no more tools. Answer now, with the result JSON, from what the tools have already "
    "returned. If that is not enough, say so with a no_source or not_available event instead of guessing."
)


def _tuned(spec: SessionSpec, callbacks: dict[str, Any], tracker: RunTracker) -> dict[str, Any]:
    """Adds the spec's opt-in controls to the common callbacks: the answer-now nudge and the repeated-call guard."""
    if spec.soft_seconds is None and spec.soft_rounds is None and spec.repeat_answer is None:
        return callbacks
    before_model, before_tool = callbacks["before_model_callback"], callbacks["before_tool_callback"]

    def tuned_before_model(callback_context, llm_request):
        out = before_model(callback_context, llm_request)
        started = getattr(tracker, "turn_started", None)
        slow = spec.soft_seconds is not None and started is not None and time.monotonic() - started > spec.soft_seconds
        many = spec.soft_rounds is not None and tracker.started > spec.soft_rounds
        if slow or many:
            llm_request.contents.append(types.Content(role="user", parts=[types.Part(text=ANSWER_NOW)]))
        return out

    def tuned_before_tool(tool, args, tool_context):
        if spec.repeat_answer is not None and tool.name != "set_model_response":
            seen = getattr(tracker, "turn_calls", None)
            if seen is not None:
                key = (tool.name, json.dumps(args, sort_keys=True, default=str))
                if key in seen:
                    return {"repeated_call": True, "message": spec.repeat_answer}  # a returned dict replaces the call: nothing is sent
                seen.add(key)
        return before_tool(tool, args, tool_context)

    return {**callbacks, "before_model_callback": tuned_before_model, "before_tool_callback": tuned_before_tool}


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
    tracker.turn_started, tracker.turn_calls = time.monotonic(), set()  # for the opt-in controls of SessionSpec
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


TOO_SLOW = "The assistant took too long to answer. Please try again."
KEEPALIVE_LINE = ": keepalive\n\n"  # an SSE comment: a reader that takes only `data:` lines skips it, and it still resets an idle timer


async def keepalive(task: "asyncio.Future", every: float | None) -> AsyncIterator[str]:
    """Waits for `task`, yielding a comment line every `every` seconds meanwhile (none when `every` is None).

    If the caller goes away first (the generator is closed), the task is cancelled, so no work goes on for nobody."""
    try:
        while not task.done():
            await asyncio.wait({task}, timeout=every)
            if every is not None and not task.done():
                yield KEEPALIVE_LINE
    finally:
        if not task.done():
            task.cancel()


def _sse(event: dict) -> str:
    return f"data: {json.dumps(EVENT_ADAPTER.validate_python(event).model_dump(mode='json'), ensure_ascii=False)}\n\n"


def _failure(exc: BaseException, tracker: RunTracker) -> ErrorEvent:
    if isinstance(exc, BudgetExceeded) or tracker.exceeded:
        return ErrorEvent(code="budget_exceeded", message=tracker.exceeded or str(exc) or "budget used", retryable=True)
    if isinstance(exc, TimeoutError) or "TimeoutError" in str(exc):  # ADK re-raises a model's timeout as a RuntimeError with its text
        return ErrorEvent(code="deadline_exceeded", message=TOO_SLOW, retryable=True)
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

        async def work() -> tuple[list[dict], ErrorEvent | None]:
            error: ErrorEvent | None = None
            out: list[dict] = []
            try:
                async with asyncio.timeout(spec.turn_seconds):
                    out = stream_events(await run_turn(spec, held, turn_request))
            except asyncio.CancelledError:
                raise
            except TimeoutError:
                error = ErrorEvent(code="deadline_exceeded", message=TOO_SLOW, retryable=True)
            except Exception as exc:  # an honest error event, never half an answer
                error = _failure(exc, held.tracker)
            return out, error

        async def events() -> AsyncIterator[str]:
            async with held.lock:  # one turn at a time per session
                task = asyncio.ensure_future(work())
                async for beat in keepalive(task, spec.keepalive_seconds):
                    yield beat
                out, error = task.result()
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

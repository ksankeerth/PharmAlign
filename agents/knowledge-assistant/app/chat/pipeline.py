"""One knowledge turn, step by step: gate, classify, route, retrieve, answer, check.

    message -> 0 gate (no model) -> 1 classify -> 2 route -> a fixed reply, OR
               3 retrieve (search_kb per part) -> nothing found: no_source (no answer model)
                                               -> 4 answer (one model call, no tools) -> 5 check (app/rules.py)

Rules:
- A fixed reply never goes through the answer model or the answer check.
- Nothing is streamed before the answer passes the check. One retry with the reason, then an error event.
- When the answer model says no_source, code builds the event from the searches that really ran. The model cannot invent searches.
- One JSON line per turn goes to the turn log when a path is set. It never holds the run token.
"""
import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from google.adk.tools.mcp_tool import McpToolset
from pharmalign_agent_common import ToolLog, Turn
from pharmalign_agent_common.sessions import stream_events
from pharmalign_contracts.agents.knowledge_assistant import ErrorEvent, SessionCreate, TurnRequest
from pharmalign_contracts.common import Usage

from app import replies
from app.answer import TurnAnswer
from app.chat import answer as answering
from app.chat import quotes
from app.chat.classify import classify
from app.chat.gate import needs_english
from app.chat.labels import Route
from app.chat.models import AllModelsBusy, ModelCall, ModelChain, ModelFailed
from app.chat.retrieve import CallTool, Retrieved, retrieve
from app.chat.route import route as decide
from app.rules import check_turn

log = logging.getLogger(__name__)

HISTORY_KEPT = 20
RETRIES = 1


@dataclass
class PipelineSession:
    id: str
    create: SessionCreate
    log: ToolLog
    history: list[tuple[str, str]]  # (user message, reply text), for the classifier
    toolset: McpToolset | None
    created: datetime
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def _reply_text(ta: TurnAnswer) -> str:
    event = ta.events[0]
    if event.type == "message":
        return event.text
    return getattr(event, "detail", None) or ""


def _usage(calls: list[ModelCall], searches: int) -> Usage | None:
    if not calls and not searches:
        return None
    return Usage(
        model_calls=len(calls), tool_calls=searches,
        input_tokens=sum(c.input_tokens for c in calls),
        output_tokens=sum(c.output_tokens + c.thought_tokens for c in calls),
    )


def _write_log(path: str | Path, record: dict) -> None:
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as error:
        log.warning("turn log not written: %s", error)


def _error(code: str, message: str) -> ErrorEvent:
    return ErrorEvent(code=code, message=message[:500] or code, retryable=True)


async def run_knowledge_turn(
    held: PipelineSession,
    request: TurnRequest,
    classifier: ModelChain,
    answerer: ModelChain,
    call_tool: CallTool,
    turn_log_path: str | Path | None = None,
) -> tuple[list[dict], Usage | None, ErrorEvent | None]:
    """Runs one turn. Returns the events to stream, the usage, and the error (if the turn failed).

    The events are empty when there is an error: nothing leaves before the answer is checked."""
    started = time.monotonic()
    models: list[ModelCall] = []
    retrieved = Retrieved()
    rec: dict[str, Any] = {
        "agent_session_id": held.id, "message": request.message, "gate": False, "kind": None, "follow_up": None,
        "parts": [], "history_wanted": False, "english": None, "fail_open": False, "route": "error",
        "searches": [], "retries": 0, "check_error": None, "citations": [], "answer_model": None,
    }
    last_turn = held.history[-1] if held.history else None
    try:
        events, error = await _run(held, request, classifier, answerer, call_tool, models, retrieved, rec)
        return events, _usage(models, len(retrieved.searches)), error
    finally:
        if not held.history or held.history[-1] is last_turn:  # the turn failed: the next message still has context
            held.history.append((request.message, ""))
            del held.history[:-HISTORY_KEPT]
        if turn_log_path:
            rec["searches"] = [
                {k: s[k] for k in ("query", "purpose", "scope", "hits", "documents")} for s in retrieved.searches
            ]
            rec["passages"] = [  # what the answer model was shown, cut short: for finding out why an answer missed
                {"anchor": q["anchor"], "document_id": q.get("document_id"), "jurisdiction": q.get("jurisdiction"), "text": (q.get("text") or "")[:240]}
                for q in retrieved.passages
            ]
            rec["models"] = [c.as_log() for c in models]
            rec["ms"] = round((time.monotonic() - started) * 1000)
            _write_log(turn_log_path, rec)


async def _run(
    held: PipelineSession, request: TurnRequest, classifier: ModelChain, answerer: ModelChain, call_tool: CallTool,
    models: list[ModelCall], retrieved: Retrieved, rec: dict[str, Any],
) -> tuple[list[dict], ErrorEvent | None]:
    has_history = bool(held.history)

    # 0 the language gate: no model call
    if needs_english(request.message):
        rec.update(gate=True, route="language")
        return _fixed(held, request, Route(kind="fixed", reply="language", has_history=has_history)), None

    # 1 classify (fails open, so it never raises) and 2 route
    labels, calls = await classify(request.message, held.history, classifier)
    models.extend(calls)
    rec.update(
        kind=labels.kind, follow_up=labels.follow_up, parts=list(labels.parts), history_wanted=labels.history_wanted,
        english=labels.english, fail_open=labels.fail_open,
    )
    route = decide(labels, has_history=has_history)
    if route.kind == "fixed":
        rec["route"] = route.reply
        return _fixed(held, request, route), None

    # 3 retrieve
    calls_before = len(held.log.calls)
    found = await retrieve(route.parts, route.history_wanted, call_tool, held.log, request.message)
    retrieved.passages, retrieved.searches = found.passages, found.searches
    if retrieved.all_failed:
        return [], _error("tool_error", "The library search failed. Try again.")
    if retrieved.nothing_found:
        rec["route"] = "no_source"
        ta = TurnAnswer(events=[answering.no_source_event(retrieved, replies.SUGGESTIONS)])
        return _done(held, request, ta), None

    # 4 answer and 5 check
    reason: str | None = None
    ta: TurnAnswer | None = None
    for attempt in range(RETRIES + 1):
        rec["retries"] = attempt
        try:
            out, result = await answering.answer(route.parts, retrieved, route.history_wanted, answerer, reason, calls=models)
        except AllModelsBusy:
            return [], _error("model_unavailable", "The answer model is busy. Try again in a minute.")
        except ModelFailed as failure:
            return [], _error("internal_error", str(failure))
        except ValueError as bad:  # the JSON did not fit the schema
            reason = "the answer was not one JSON object in the required shape: " + (str(bad).splitlines() or [""])[0]
            rec["check_error"] = reason
            continue
        rec["answer_model"] = result.model
        if answering.is_no_source(out):
            ta = TurnAnswer(events=[answering.no_source_event(retrieved, out.suggestions)])
            rec["route"] = "no_source"
            break
        try:
            candidate = answering.to_turn_answer(out, retrieved, route.preface)
            check_turn(Turn(held.create, request, calls_before), candidate, held.log)
            # the server checks each quote against its whole document: look at the rest of it, and widen a quote that repeats
            chunks, checks = await quotes.document_chunks(candidate, retrieved.passages, call_tool, held.log)
            retrieved.searches.extend(checks)
            candidate, changes = quotes.repair(candidate, chunks, request.message)
            if changes:
                rec["quotes_repaired"] = rec.get("quotes_repaired", []) + changes
                check_turn(Turn(held.create, request, calls_before), candidate, held.log)  # still word for word from a tool's text
            if problem := answering.citation_problems(candidate, request.message, chunks):
                raise ValueError(problem)
        except ValueError as bad:
            reason = str(bad)
            rec["check_error"] = reason
            continue
        ta = candidate
        rec["route"] = "answer"
        rec["citations"] = [{"anchor": c.anchor, "quote": c.quote} for c in ta.events[0].citations]
        break
    if ta is None:
        return [], _error("invalid_output", reason or "the answer was refused")
    return _done(held, request, ta), None


def _fixed(held: PipelineSession, request: TurnRequest, route: Route) -> list[dict]:
    return _done(held, request, TurnAnswer(events=[replies.fixed_event(route)]))


def _done(held: PipelineSession, request: TurnRequest, ta: TurnAnswer) -> list[dict]:
    """The events for a checked answer, and the turn goes into the session's history."""
    held.history.append((request.message, _reply_text(ta)))
    del held.history[:-HISTORY_KEPT]
    return stream_events(ta)

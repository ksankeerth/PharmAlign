"""One knowledge turn, step by step: gate, classify, route, retrieve, answer, check.

    message -> 0-2 understand() (gate, suggestion pick, classify, route) -> a fixed reply, OR
               3 retrieve (search_kb per part) -> nothing found: no_source (no answer model)
                                               -> 4 answer (one model call, no tools) -> 5 check (app/rules.py)

Rules:
- A fixed reply never goes through the answer model or the answer check.
- Nothing is streamed before the answer passes the check. One retry with the reason, then an error event.
- When the answer model says no_source on the home sources, the whole library is searched once and the model answers again
  (a fresh retry budget). Only a second no_source, or none when nothing new came back, ends the turn that way.
- When the answer model says no_source, code builds the event from the searches that really ran. The model cannot invent searches.
- A turn has a time budget (TURN_BUDGET_SECONDS). Every answer call ends by the deadline; a retry or the widen step that
  cannot fit in the time left is skipped, and the turn log says which one in `budget_cut`.
- One JSON line per turn goes to the turn log when a path is set. It never holds the run token.
"""
import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from google.adk.tools.mcp_tool import McpToolset
from pharmalign_agent_common import ToolLog, Turn
from pharmalign_agent_common.sessions import stream_events
from pharmalign_contracts.agents.knowledge_assistant import ErrorEvent, SessionCreate, TurnRequest
from pharmalign_contracts.common import Usage

from app import replies
from app.answer import TurnAnswer
from app.chat import answer as answering
from app.chat import quotes, topics
from app.chat.labels import NOT_AVAILABLE_REPLIES, HistoryEntry, Route
from app.chat.models import AllModelsBusy, ModelCall, ModelChain, ModelFailed, env_seconds
from app.chat.retrieve import CallTool, Retrieved, can_widen, is_status_question, local_first_on, retrieve, widen
from app.chat.understand import understand
from app.rules import check_turn

log = logging.getLogger(__name__)

HISTORY_KEPT = 20
RETRIES = 1
TURN_BUDGET_SECONDS = env_seconds("PHARMALIGN_KA_TURN_BUDGET", 50.0)  # the server gives up on a turn at 60 s
RETRY_MIN_SECONDS = 8.0  # a check-failure retry needs at least this much of the budget left
WIDEN_MIN_SECONDS = 20.0  # the widen step (a whole-library search and a second answer) needs at least this much


@dataclass
class PipelineSession:
    id: str
    create: SessionCreate
    log: ToolLog
    history: list[HistoryEntry]  # what each turn said and was, for the classifier
    toolset: McpToolset | None
    created: datetime
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def _suggestions(ta: TurnAnswer) -> tuple[str, ...]:
    return tuple(getattr(ta.events[0], "suggestions", None) or ())


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
        "searches": [], "widened": False, "retries": 0, "check_error": None, "citations": [], "answer_model": None,
        "how": None, "found": None, "style": "none", "sources_named": [], "search_all": False, "budget_cut": None,
    }
    deadline = started + TURN_BUDGET_SECONDS
    last_turn = held.history[-1] if held.history else None
    try:
        events, error = await _run(held, request, classifier, answerer, call_tool, models, retrieved, rec, deadline)
        return events, _usage(models, len(retrieved.searches)), error
    finally:
        if not held.history or held.history[-1] is last_turn:  # the turn failed: the next message still has context
            held.history.append(HistoryEntry(request.message, "", "error"))
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
    models: list[ModelCall], retrieved: Retrieved, rec: dict[str, Any], deadline: float,
) -> tuple[list[dict], ErrorEvent | None]:
    # 0 gate, suggestion pick, 1 classify (fails open, so it never raises), 2 route
    understood = await understand(request.message, held.history, classifier)
    models.extend(understood.calls)
    labels, route = understood.labels, understood.route
    rec.update(how=understood.how, style=route.style, sources_named=list(route.sources_named), search_all=route.search_all)
    if understood.how == "gate":
        rec.update(gate=True, route="language")
        return _fixed(held, request, route), None
    rec.update(
        kind=labels.kind, follow_up=labels.follow_up, parts=list(labels.parts), history_wanted=labels.history_wanted,
        english=labels.english, fail_open=labels.fail_open,
    )
    if route.kind == "fixed":
        rec["route"] = route.reply
        return _fixed(held, request, route), None

    # 3 retrieve
    calls_before = len(held.log.calls)
    # a picked suggestion has no words of its own worth searching ("yes")
    own_words = None if understood.how == "suggestion_pick" else request.message
    found = await retrieve(route.parts, route.history_wanted, call_tool, held.log, own_words, route.search_all)
    retrieved.passages, retrieved.searches = found.passages, found.searches
    if retrieved.all_failed:
        return [], _error("tool_error", "The library search failed. Try again.")
    if retrieved.nothing_found:
        rec["route"] = "no_source"
        ta = TurnAnswer(events=[answering.no_source_event(retrieved, topics.suggestions_for(route.parts) or replies.SUGGESTIONS)])
        return _done(held, request, ta, "no_source"), None

    # 4 answer and 5 check; when the model finds no answer in the home sources, widen to the whole library once
    first = await _attempt(held, request, route, answerer, call_tool, models, retrieved, rec, calls_before, deadline)
    last = first
    wants_widen = first.status == "no_source" and not route.search_all and local_first_on() and can_widen(retrieved)
    if wants_widen and deadline - time.monotonic() < WIDEN_MIN_SECONDS:
        rec["budget_cut"] = "widen"
        wants_widen = False
    if wants_widen:
        purpose = "context" if route.history_wanted else "requirement"
        seen = len(retrieved.passages)
        ran = await widen(retrieved, call_tool, held.log, purpose)
        if ran:
            rec["widened"] = True
        if ran and len(retrieved.passages) > seen:
            last = await _attempt(held, request, route, answerer, call_tool, models, retrieved, rec, calls_before, deadline)
    if last.error:
        return [], last.error
    if last.status == "no_source":
        rec["route"] = "no_source"
        ta = TurnAnswer(events=[answering.no_source_event(retrieved, last.suggestions or topics.suggestions_for(route.parts) or replies.SUGGESTIONS)])
        return _done(held, request, ta, "no_source"), None
    if last.ta is None:
        return [], _error("invalid_output", last.reason or "the answer was refused")
    rec["route"] = "answer"
    rec["citations"] = [{"anchor": c.anchor, "quote": c.quote} for c in last.ta.events[0].citations]
    return _done(held, request, last.ta, "answer"), None


@dataclass
class Attempt:
    """How one round of answer calls ended: a checked answer (`ta`), the model's no_source (`suggestions`), a failure
    (`reason`: every call was refused by the checks), or an error event."""

    status: Literal["answer", "no_source", "failed", "error"]
    ta: TurnAnswer | None = None
    suggestions: list[str] = field(default_factory=list)
    reason: str | None = None
    error: ErrorEvent | None = None


async def _attempt(
    held: PipelineSession, request: TurnRequest, route: Route, answerer: ModelChain, call_tool: CallTool,
    models: list[ModelCall], retrieved: Retrieved, rec: dict[str, Any], calls_before: int,
    deadline: float,
) -> Attempt:
    """Up to RETRIES + 1 answer calls on the passages found so far, each checked; a refused answer is retried with the reason."""
    reason: str | None = None
    history = route.history_wanted or is_status_question(route.parts)  # a status question may cite the old law next to its status
    for attempt in range(RETRIES + 1):
        if attempt and deadline - time.monotonic() < RETRY_MIN_SECONDS:
            rec["budget_cut"] = "retry"
            break
        rec["retries"] = attempt
        try:
            out, result = await answering.answer(route.parts, retrieved, route.history_wanted, answerer, reason, calls=models, style=route.style, deadline=deadline)
        except AllModelsBusy:
            return Attempt("error", error=_error("model_unavailable", "The answer model is busy. Try again in a minute."))
        except ModelFailed as failure:
            return Attempt("error", error=_error("internal_error", str(failure)))
        except ValueError as bad:  # the JSON did not fit the schema
            reason = "the answer was not one JSON object in the required shape: " + (str(bad).splitlines() or [""])[0]
            rec["check_error"] = reason
            continue
        rec["answer_model"] = result.model
        rec["found"] = out.found[:300]  # what the model said answers the question, for finding out why an answer missed
        if answering.is_no_source(out):
            named = answering.named_passages(out.found, retrieved)
            if named and attempt < RETRIES:  # it named passages that answer, then refused: ask once which of the two it means
                reason = (
                    f"you named {', '.join(named[:3])} as stating the answer but set no_source. If they state it, set answer to "
                    "message and cite them. If they do not, set found to none"
                )
                rec["check_error"] = reason
                continue
            return Attempt("no_source", suggestions=list(out.suggestions))
        try:
            candidate = answering.to_turn_answer(out, retrieved, route.preface, topics.suggestions_for(route.parts), hard=attempt == RETRIES)
            check_turn(Turn(held.create, request, calls_before), candidate, held.log, history=history)
            # the server checks each quote against its whole document: look at the rest of it, and widen a quote that repeats
            chunks, checks = await quotes.document_chunks(candidate, retrieved.passages, call_tool, held.log)
            retrieved.searches.extend(checks)
            candidate, changes = quotes.repair(candidate, chunks, request.message)
            if changes:
                rec["quotes_repaired"] = rec.get("quotes_repaired", []) + changes
                check_turn(Turn(held.create, request, calls_before), candidate, held.log, history=history)  # still word for word from a tool's text
            if problem := answering.citation_problems(candidate, request.message, chunks):
                raise ValueError(problem)
        except ValueError as bad:
            reason = str(bad)
            rec["check_error"] = reason
            continue
        return Attempt("answer", ta=candidate)
    return Attempt("failed", reason=reason)


def _fixed(held: PipelineSession, request: TurnRequest, route: Route) -> list[dict]:
    reply = route.reply or ""
    kind = "refused" if reply in NOT_AVAILABLE_REPLIES else reply  # greeting, clarify, split, language, distress, decision
    return _done(held, request, TurnAnswer(events=[replies.fixed_event(route)]), kind)


def _done(held: PipelineSession, request: TurnRequest, ta: TurnAnswer, kind: str) -> list[dict]:
    """The events for a checked answer, and the turn goes into the session's history."""
    held.history.append(HistoryEntry(request.message, _reply_text(ta), kind, _suggestions(ta)))
    del held.history[:-HISTORY_KEPT]
    return stream_events(ta)

"""What is specific to the Knowledge Assistant: the first message of a turn and the rules an answer must keep
before any of it is streamed."""
import json
import re
from typing import Any

from pharmalign_agent_common import ToolLog, Turn, check_evidence
from pharmalign_contracts.agents.knowledge_assistant import Message, NoSource, SessionCreate, TurnRequest

from app.answer import TurnAnswer

# A question about earlier law or earlier versions. Only then may superseded law be cited.
HISTORY = re.compile(
    r"\b(histor\w*|previous\w*|prior|earlier|former\w*|old|older|past|superseded|repealed|replaced|revoked|amended|"
    r"used to|was in force|were in force|before|version\w*)\b",
    re.IGNORECASE,
)
SEARCHES = ("search_kb", "lookup_term")


def build_message(session: SessionCreate, request: TurnRequest) -> str:
    """One turn's input for the model. Tools supply all law and case text."""
    task: dict[str, Any] = {
        "question": request.message,
        "focus": request.focus.model_dump(mode="json", exclude_none=True) if request.focus else None,
        "language": session.language,
        "as_of": session.as_of.isoformat(),
    }
    if session.case_id:
        task["case_id"] = session.case_id
    return json.dumps(task, ensure_ascii=False)


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from _walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from _walk(v)


def legal_status_of(log: ToolLog, anchor: str) -> str | None:
    """The legal status a tool returned for this anchor, if it returned one."""
    for call in log.calls:
        if call.failed:
            continue
        for row in _walk(call.payload):
            if row.get("anchor") == anchor and isinstance(row.get("legal_status"), str):
                return row["legal_status"]
    return None


def saved_trace_ids(log: ToolLog) -> set[str]:
    return {
        str(c.payload["trace_id"]) for c in log.calls
        if c.tool == "get_run_trace" and not c.failed and isinstance(c.payload, dict) and c.payload.get("saved") and c.payload.get("trace_id")
    }


def check_turn(turn: Turn, answer: TurnAnswer, log: ToolLog, *, history: bool | None = None) -> None:
    """Mirrors the contract and the server's checks. Raises ValueError.

    `history` is the classifier's word that the person asks about earlier law. It counts together with the old-law
    words in the raw message; None (the ADK path) leaves only the words."""
    check_evidence(answer, log)  # every anchor came from a tool in this session, every quote is in its text
    history = bool(history) or bool(HISTORY.search(turn.request.message))
    traces = saved_trace_ids(log)
    for event in answer.events:
        if event.type == "message":
            _check_message(event, history, traces, log)
        elif event.type == "no_source":
            _check_no_source(turn, event, log)
        elif event.type == "draft_comment" and not turn.case_session:
            raise ValueError("a draft comment needs a case. This session has none: answer not_available with reason out_of_scope")


def _check_message(m: Message, history: bool, traces: set[str], log: ToolLog) -> None:
    if not m.citations:
        raise ValueError(
            "a message needs at least one citation from a tool result. When no source answers, "
            "use a no_source event (or not_available if the question is outside what you do)"
        )
    for ev in m.citations:
        status = legal_status_of(log, ev.anchor)
        if status in (None, "in_force"):
            continue
        label = status.replace("_", " ")
        if not history:
            raise ValueError(
                f"'{ev.anchor}' is {label} law. Cite only law in force, unless the person asks about earlier law or history"
            )
        if label not in m.text.casefold():
            raise ValueError(f"'{ev.anchor}' is {label} law. Say so in the text, next to what it says")
    unknown = [t for t in m.trace_ids if t not in traces]
    if unknown:
        raise ValueError(f"trace_id '{unknown[0]}' wasn't returned by get_run_trace in this session")


def _check_no_source(turn: Turn, event: NoSource, log: ToolLog) -> None:
    empty = log.empty_searches(SEARCHES, since=turn.calls_before)
    if not empty:
        raise ValueError("no_source needs a search_kb or lookup_term in this turn that found nothing. Search first")
    if len(event.searches_tried) < len(empty):
        raise ValueError(f"list every search that found nothing: {len(empty)} in this turn, {len(event.searches_tried)} listed")

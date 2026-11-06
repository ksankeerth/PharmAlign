"""Contract for the Knowledge Assistant: a chat agent, with sessions and streamed turns.

It answers from the knowledge base for anyone. When a session is opened on a case, it also reads that case."""
from typing import Annotated, Literal, Optional, Union

from pydantic import AwareDatetime, Field, TypeAdapter

from ..common import (
    Evidence,
    ExecutionProfile,
    Identifier,
    Language,
    ShortText,
    Slug,
    Strict,
    Usage,
    Versions,
)

# What a knowledge-only session may call: nothing here needs a case.
KNOWLEDGE_TOOLS = ("search_kb", "get_doc_status", "read_clause", "lookup_term", "read_notice")
# What a case session may also call.
CASE_TOOLS = (
    "get_facts", "get_findings", "get_conflicts", "get_previous_run", "get_check", "compare_pack",
    "read_page", "read_reference", "get_run_trace", "draft_comment",
)
TOOLS = KNOWLEDGE_TOOLS + CASE_TOOLS

# Routes the agent serves. A turn streams as server-sent events with one `data: {json}` line per event, a blank line after each.
SESSION_ROUTE = "/sessions"
TURN_ROUTE = "/sessions/{agent_session_id}/turns"
EVENT_LINE_PREFIX = "data: "
# What one turn may use. Past any of these, the agent ends the turn with a retryable error event.
TURN_MAX_MODEL_CALLS = 12
TURN_MAX_TOOL_CALLS = 20
TURN_SECONDS = 90


class SessionCreate(Strict):
    """Scoped by the caller to one person. With a `case_id` it is also scoped to that case. Without one, it is a knowledge-only session
    with no case tools."""

    run_token: Annotated[str, Field(min_length=8)]
    tenant: Slug
    mode: Literal["live", "simulation"]
    execution_profile: ExecutionProfile
    as_of: AwareDatetime
    versions: Versions
    case_id: Optional[Identifier] = None
    user_id: Identifier
    view: Slug
    language: Language = "en"


class SessionCreated(Strict):
    agent_session_id: Identifier


class TurnFocus(Strict):
    check_id: Optional[Identifier] = None
    finding_id: Optional[Identifier] = None


class TurnRequest(Strict):
    message: Annotated[str, Field(min_length=1, max_length=4000)]
    focus: Optional[TurnFocus] = None


class TextDelta(Strict):
    type: Literal["text_delta"] = "text_delta"
    text: str


class Message(Strict):
    """`trace_ids` lists saved traces this answer read. The caller labels the answer from them: saved, or a new reading.
    Each citation is an anchor a tool returned and a quote from it. The caller checks the quote and adds the title and legal status itself."""

    type: Literal["message"] = "message"
    text: Annotated[str, Field(min_length=1)]
    trace_ids: list[Identifier] = Field(default_factory=list)
    citations: list[Evidence] = Field(default_factory=list)
    suggestions: Annotated[list[ShortText], Field(max_length=3)] = Field(
        default_factory=list, description="Follow-up questions the person may ask next. Questions only."
    )


class DraftComment(Strict):
    """A draft only. It is posted when a person confirms it."""

    type: Literal["draft_comment"] = "draft_comment"
    check_id: Optional[Identifier] = None
    text: Annotated[str, Field(min_length=1, max_length=4000)]


class NotAvailable(Strict):
    """Used when something wasn't checked, wasn't saved or is outside what the agent does. The agent says so and doesn't guess."""

    type: Literal["not_available"] = "not_available"
    reason: Literal["not_checked", "not_saved", "out_of_scope"]
    detail: Optional[str] = None


class SearchStep(Strict):
    """One search the agent made before saying it found no source."""

    search: ShortText
    result: ShortText
    candidates: Optional[Annotated[int, Field(ge=0)]] = None


class NoSource(Strict):
    """No source in the knowledge base answers the question. The agent lists what it searched and doesn't answer from memory."""

    type: Literal["no_source"] = "no_source"
    detail: Optional[ShortText] = None
    searches_tried: Annotated[list[SearchStep], Field(min_length=1)]
    suggestions: Annotated[list[ShortText], Field(max_length=3)] = Field(default_factory=list)


class UsageEvent(Strict):
    type: Literal["usage"] = "usage"
    usage: Usage


class ErrorEvent(Strict):
    type: Literal["error"] = "error"
    code: Slug
    message: str
    retryable: bool


AssistantEvent = Annotated[
    Union[TextDelta, Message, DraftComment, NotAvailable, NoSource, UsageEvent, ErrorEvent],
    Field(discriminator="type"),
]
EVENT_ADAPTER = TypeAdapter(AssistantEvent)

SESSION_EXAMPLE = {
    "run_token": "example-signed-token",
    "tenant": "example-country",
    "mode": "simulation",
    "execution_profile": {"model_set": "default", "gate": "on", "data_class": "synthetic"},
    "as_of": "2026-11-01T10:00:00Z",
    "versions": {"pack": "pack-3", "kb_snapshot": "snap-14"},
    "case_id": "case-1042",
    "user_id": "demo-case-officer",
    "view": "case_officer",
    "language": "en",
}
TURN_EXAMPLE = {"message": "Why did this check need a person?", "focus": {"check_id": "QUAL-07"}}
KNOWLEDGE_SESSION_EXAMPLE = {k: v for k, v in SESSION_EXAMPLE.items() if k != "case_id"}
EVENT_EXAMPLES = (
    {"type": "text_delta", "text": "This check can't pass alone."},
    {"type": "message", "text": "This check can't pass alone, so a person confirms it.", "trace_ids": ["trace-221"],
     "citations": [{"anchor": "E-S25:v3:c0412", "quote": "30 C / 65% RH"}], "suggestions": ["What does the guideline say about storage?"]},
    {"type": "draft_comment", "check_id": "QUAL-07", "text": "Please confirm the study covers 12 months."},
    {"type": "not_available", "reason": "not_saved", "detail": "No reasoning was saved for this check."},
    {"type": "no_source", "detail": "Nothing in force answers this.", "searches_tried": [
        {"search": "meaning and words, in force only", "candidates": 412, "result": "no passage was similar enough"},
        {"search": "same question, any legal status", "candidates": 640, "result": "nothing similar either"}],
     "suggestions": ["What does the stability guideline require?"]},
    {"type": "usage", "usage": {"model_calls": 2, "tool_calls": 3, "input_tokens": 4200, "output_tokens": 180}},
    {"type": "error", "code": "model_unavailable", "message": "No approved model is available.", "retryable": True},
)

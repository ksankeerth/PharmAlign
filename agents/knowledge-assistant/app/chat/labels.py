"""What the classifier says about one message, and what the router decides. Shared by classify.py, route.py and pipeline.py."""
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Kind = Literal["question", "own_data", "decision_request", "off_topic", "unsafe", "distress", "small_talk", "vague"]
KINDS: tuple[str, ...] = Kind.__args__  # type: ignore[attr-defined]

# The names of the fixed replies in app/replies.py.
Reply = Literal["greeting", "clarify", "split", "language", "distress", "decision", "off_topic", "unsafe", "own_data"]
NOT_AVAILABLE_REPLIES = frozenset({"off_topic", "unsafe", "own_data"})  # sent as not_available, reason out_of_scope
MAX_PARTS = 3  # more parts than this gets the "split" reply

Style = Literal["none", "shorter", "simpler", "more_detail", "bullets"]
STYLES: tuple[str, ...] = Style.__args__  # type: ignore[attr-defined]
MAX_SOURCES = 5

# What kind of reply a chat turn got, as the classifier is told about it.
EntryKind = Literal["answer", "no_source", "clarify", "split", "greeting", "decision", "language", "distress", "refused", "error"]
ENTRY_KINDS: tuple[str, ...] = EntryKind.__args__  # type: ignore[attr-defined]


@dataclass(frozen=True)
class HistoryEntry:
    """One finished turn of the chat: what the person said, what we replied, what kind of reply it was, and the
    follow-up questions we showed with it."""

    user: str
    reply: str
    kind: str  # one of ENTRY_KINDS
    suggestions: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "HistoryEntry":
        """From a dict with keys user, reply, kind, suggestions (the shape of eval/conversation.jsonl)."""
        return cls(
            user=str(data["user"]), reply=str(data.get("reply") or ""), kind=str(data.get("kind") or "answer"),
            suggestions=tuple(str(s) for s in data.get("suggestions") or ()),
        )


class ClassifierOutput(BaseModel):
    """The model's answer, used as response_schema. It labels and rewrites, never answers."""

    model_config = ConfigDict(extra="forbid")

    kind: Kind
    follow_up: bool = Field(description="True when the message only makes sense with the earlier turns of this chat.")
    parts: list[str] = Field(
        max_length=5,
        description="Each separate question in the message, rewritten in English so it makes sense without the chat. "
        "Empty when the message holds no question.",
    )
    history_wanted: bool = Field(description="True when the person asks about earlier, old, superseded or repealed law.")
    english: bool = Field(description="True when the message is written in English.")
    style: Style = Field(
        default="none",
        description="How the person wants the answer written: shorter, simpler, more_detail or bullets. "
        "none unless they ask.",
    )
    sources_named: list[str] = Field(
        default_factory=list,
        max_length=MAX_SOURCES,
        description="Sources the person names to look in, such as WHO, ICH, GCC, EU, FDA. Empty unless named.",
    )


@dataclass(frozen=True)
class Labels:
    """The classifier's result after code has checked it. `parts` is never empty for kind question."""

    kind: Kind
    follow_up: bool
    parts: tuple[str, ...]
    history_wanted: bool
    english: bool
    style: Style = "none"
    sources_named: tuple[str, ...] = ()
    fail_open: bool = False  # True when the model failed and code used the safe default
    model: str | None = None  # the model that answered, None on fail-open

    @classmethod
    def fail_open_for(cls, message: str) -> "Labels":
        """A wrong label must never block a real question."""
        return cls(kind="question", follow_up=False, parts=(message,), history_wanted=False, english=True, fail_open=True)


@dataclass(frozen=True)
class Route:
    """What to do with a message.

    - kind "fixed": send the fixed reply `reply`, with no search and no model call.
    - kind "answer": search each of `parts`, then answer. `preface` names a fixed sentence the answer starts with
      ("decision" for a decision request that also holds a question).
    """

    kind: Literal["fixed", "answer"]
    reply: Reply | None = None
    parts: tuple[str, ...] = ()
    history_wanted: bool = False
    preface: Literal["decision"] | None = None
    suggestions: tuple[str, ...] = field(default_factory=tuple)  # for "split": the first 3 rewritten parts
    has_history: bool = False  # the session already has turns ("greeting" then thanks instead of introducing itself)
    style: Style = "none"  # how the person wants the answer written
    sources_named: tuple[str, ...] = ()  # sources the person named to look in
    search_all: bool = False  # search the whole library at once, not the home country first

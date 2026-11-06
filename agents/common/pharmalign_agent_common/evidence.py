"""What a run's tools returned, and the evidence rules every agent shares.

The runner records every tool response of a run in a ToolLog. Rules use it to refuse an anchor
no tool returned, or a quote that isn't in the text the tool returned at that anchor.
Text matching follows the server's citation check: NFKC, case folded, punctuation ignored.
"""
import json
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from pharmalign_contracts.common import Evidence, FactValue
from pydantic import BaseModel

KB_TOOLS = frozenset({"search_kb", "read_clause", "lookup_term"})
NOT_SOURCES = frozenset({"verify_quote", "compute", "set_model_response"})  # they echo or compute, never cite

_TOKEN = re.compile(r"(?<![\w.,])-?\d+(?:[.,]\d+)*|\d+(?:[.,]\d+)*|%|[^\W\d_]+")
_THOUSANDS = re.compile(r"-?\d{1,3}(?:,\d{3})+")
_DASHES = str.maketrans({c: "-" for c in "‐‑‒–—―−"})


def _clean(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Cf").translate(_DASHES).casefold()
    return "".join(str(unicodedata.decimal(c)) if c.isdecimal() else c for c in text)


def _tokens(text: str) -> list[str]:
    return [t.replace(",", "") if _THOUSANDS.fullmatch(t) else t for t in _TOKEN.findall(_clean(text))]


def normalise(text: str) -> str:
    return " ".join(_tokens(text))


def quote_in(quote: str, text: str) -> bool:
    needle = normalise(quote)
    return bool(needle) and f" {needle} " in f" {normalise(text)} "


def numbers_in(text: str) -> list[float]:
    found = []
    for token in _tokens(text):
        if token[-1].isdigit():
            try:
                found.append(float(token.replace(",", ".")))
            except ValueError:
                pass
    return found


def unquoted_numbers(text: str, quotes: Iterable[str], also: Iterable[str] = ()) -> list[float]:
    """Numbers in `text` that no quote (and no text in `also`) contains."""
    known = {n for q in [*quotes, *also] for n in numbers_in(q)}
    return [n for n in numbers_in(text) if n not in known]


def value_in_quotes(value: str | None, number: float | None, quotes: Iterable[str]) -> bool:
    """A found value must be what its own quote says (the server's rule)."""
    quotes = list(quotes)
    if number is not None:
        return any(number in numbers_in(q) for q in quotes)
    return any(quote_in(value or "", q) for q in quotes)


def fact_in_quotes(fact: FactValue) -> bool:
    return fact.status != "found" or value_in_quotes(fact.value, fact.number, [e.quote for e in fact.evidence])


def evidence_in(value: Any) -> list[Evidence]:
    """Every Evidence inside a result."""
    if isinstance(value, Evidence):
        return [value]
    if isinstance(value, BaseModel):
        return [e for name in type(value).model_fields for e in evidence_in(getattr(value, name))]
    if isinstance(value, list | tuple):
        return [e for item in value for e in evidence_in(item)]
    return []


@dataclass
class Source:
    tools: set[str] = field(default_factory=set)
    texts: list[str] = field(default_factory=list)
    document: str = ""


@dataclass
class ToolCallRecord:
    tool: str
    args: dict
    payload: Any
    failed: bool


def _payload(response: Any) -> tuple[Any, bool]:
    """The tool's own value from ADK's dump of an MCP result, and whether it failed."""
    if not isinstance(response, dict) or "content" not in response:
        return response, False
    failed = bool(response.get("isError"))
    if isinstance(response.get("structuredContent"), dict):
        return response["structuredContent"], failed
    text = "".join(c.get("text", "") for c in response.get("content") or [] if isinstance(c, dict))
    try:
        return json.loads(text), failed
    except ValueError:
        return text, failed


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


@dataclass
class ToolLog:
    """One run's tool results: the anchors returned, the text at each, and verified quotes."""

    sources: dict[str, Source] = field(default_factory=dict)
    verified: set[tuple[str, str]] = field(default_factory=set)
    calls: list[ToolCallRecord] = field(default_factory=list)

    def record(self, tool: str, args: dict, response: Any) -> None:
        payload, failed = _payload(response)
        self.calls.append(ToolCallRecord(tool, dict(args or {}), payload, failed))
        if failed:
            return
        if tool == "verify_quote":
            if isinstance(payload, dict) and payload.get("verified") is True:
                self.verified.add((str(args.get("anchor", "")), normalise(str(args.get("quote", "")))))
            return
        if tool not in NOT_SOURCES:
            self._collect(tool, payload)

    def _collect(self, tool: str, value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                self._collect(tool, item)
            return
        if not isinstance(value, dict):
            return
        anchor = value.get("anchor") or value.get("text_anchor")
        if isinstance(anchor, str) and anchor:
            source = self.sources.setdefault(anchor, Source())
            source.tools.add(tool)
            source.texts.append(" \n ".join(s for k, v in value.items() if k not in ("anchor", "text_anchor") for s in _strings(v)))
            source.document = source.document or str(value.get("document_id") or value.get("doc_id") or anchor.split(":")[0])
        for v in value.values():
            if isinstance(v, dict | list):
                self._collect(tool, v)

    def returned(self, anchor: str) -> bool:
        return anchor in self.sources

    def from_tools(self, anchor: str, tools: Iterable[str]) -> bool:
        return anchor in self.sources and bool(self.sources[anchor].tools & set(tools))

    def document_of(self, anchor: str) -> str:
        source = self.sources.get(anchor)
        return source.document if source else anchor.split(":")[0]

    def is_verified(self, ev: Evidence) -> bool:
        """verify_quote said this quote is at this anchor."""
        return (ev.anchor, normalise(ev.quote)) in self.verified

    def confirms(self, ev: Evidence) -> bool:
        """The quote is in the text a tool returned at its anchor, or verify_quote confirmed it."""
        if self.is_verified(ev):
            return True
        source = self.sources.get(ev.anchor)
        return bool(source) and any(quote_in(ev.quote, t) for t in source.texts)

    def looked(self) -> bool:
        """At least one tool call that reads or searches."""
        return any(c.tool not in NOT_SOURCES for c in self.calls)

    def texts(self) -> list[str]:
        """Every string in every successful tool result."""
        return [s for c in self.calls if not c.failed for s in _strings(c.payload)]

    def empty_searches(
        self, tools: Iterable[str] = ("search_kb", "search_dossier", "lookup_term"), since: int = 0
    ) -> list[str]:
        """Searches that found nothing, as plain descriptions. `since` skips the first calls (a session's earlier turns)."""
        found = []
        for c in self.calls[since:]:
            if c.tool not in set(tools) or c.failed or not isinstance(c.payload, dict):
                continue
            if c.payload.get("no_source_found") or c.payload.get("no_match"):
                found.append(f"{c.tool}: {c.args.get('query') or c.args.get('term') or ''}".strip())
        return found


def check_evidence(result: BaseModel, log: ToolLog) -> None:
    """Every anchor came from a tool in this run, and every quote is in what that tool returned."""
    for ev in evidence_in(result):
        if not log.returned(ev.anchor):
            raise ValueError(
                f"anchor '{ev.anchor}' wasn't returned by any tool in this run. Cite only anchors a tool returned."
            )
        if not log.confirms(ev):
            raise ValueError(
                f"the quote at '{ev.anchor}' isn't in the text the tool returned there. "
                "Copy it word for word, or check it with verify_quote."
            )

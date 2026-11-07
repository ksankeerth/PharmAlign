"""Quotes the server will keep. Runs after check_turn passes, before anything is streamed.

When the server relays a message it checks every quote against EVERY chunk of the cited document version, not only the
cited chunk. A quote whose words also appear in two other chunks of that version, or in one other chunk when it is shorter
than 12 words, is dropped as unverified. A service page that repeats its fee table (card, FAQ, summary) makes any
short quote of a fee repeat, however exact.

So, for each cited document version:
1. One search_kb inside that document, with the quote as the query, finds the other chunks that hold the same words.
   (verify_quote would be exact, but it is not one of this agent's contract tools.) Together with the passages already
   retrieved, that is the agent's view of the document.
2. A quote that repeats is widened word by word inside its own chunk, or moved to another chunk that holds it, until
   it is at least 12 words and appears nowhere else. It stays a word-for-word copy of a chunk a tool returned, so
   check_turn still holds. No model call.
3. When no such quote exists, answer.citation_problems says why, and the model is asked again.
"""
import asyncio
import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from pharmalign_agent_common import ToolLog
from pharmalign_agent_common.evidence import _payload, normalise, quote_in
from pharmalign_contracts.agents.knowledge_assistant import Message

from app.answer import TurnAnswer

log = logging.getLogger(__name__)

CallTool = Callable[[str, dict], Awaitable[dict]]
MIN_WORDS = 8  # the server's shortest quote that can stand alone
ALONE_WORDS = 12  # a quote this long may repeat once in its document
MAX_WORDS = 60  # a widened quote stops here
CHECK_TOP_K = 10


def version_of(anchor: str) -> str:
    """'B-01:v2:c0005' -> 'B-01:v2'. The server compares a quote with the chunks of the same document version."""
    return anchor.rsplit(":", 1)[0] if anchor.count(":") >= 2 else anchor


def _words(text: str) -> int:
    return len(normalise(text).split())


def elsewhere(quote: str, anchor: str, chunks: dict[str, str]) -> int:
    """Other chunks of the same document version whose text holds the quote."""
    version = version_of(anchor)
    return sum(1 for other, text in chunks.items() if other != anchor and version_of(other) == version and quote_in(quote, text))


def points_here(quote: str, anchor: str, chunks: dict[str, str], question: str) -> bool:
    """The server's relay rule, as far as the agent can see the document."""
    words = _words(quote)
    if words < MIN_WORDS or quote_in(quote, question):
        return False
    seen = elsewhere(quote, anchor, chunks)
    return seen == 0 or (seen == 1 and words >= ALONE_WORDS)


def _tokens(text: str) -> tuple[list[re.Match], list[tuple[str, int]]]:
    """The raw words of a text, and its normalised tokens, each with the index of the raw word it came from."""
    words = list(re.finditer(r"\S+", text))
    flat = [(token, i) for i, w in enumerate(words) for token in normalise(w.group()).split()]
    return words, flat


def _locate(quote: str, text: str) -> tuple[list[re.Match], int, int] | None:
    """The raw words of `text` and the first and last word index where the quote's tokens sit, or None."""
    wanted = normalise(quote).split()
    words, flat = _tokens(text)
    if not wanted:
        return None
    tokens = [t for t, _ in flat]
    for start in range(len(tokens) - len(wanted) + 1):
        if tokens[start:start + len(wanted)] == wanted:
            return words, flat[start][1], flat[start + len(wanted) - 1][1]
    return None


def _ends_here(text: str, words: list[re.Match], i: int) -> bool:
    """Word i ends a sentence or a line: a person can read the quote as whole lines."""
    if i >= len(words) - 1 or words[i].group()[-1] in ".?!":  # not ":" -- "Application: AED 100" is one line
        return True
    return "\n" in text[words[i].end():words[i + 1].start()]


def _whole_lines(text: str, words: list[re.Match], first: int, last: int) -> tuple[int, int]:
    """The span pushed out to the start of its sentence or line and the end of its sentence or line."""
    while first > 0 and not _ends_here(text, words, first - 1):
        first -= 1
    while not _ends_here(text, words, last):
        last += 1
    return first, last


def widen(quote: str, anchor: str, chunks: dict[str, str], question: str) -> str | None:
    """The shortest widening of the quote inside chunk `anchor` that points only here, as whole lines when that stays
    under MAX_WORDS, or None. A longer copy of a quote found nowhere else is found nowhere else, so rounding out is safe."""
    text = chunks.get(anchor)
    found = _locate(quote, text) if text else None
    if not found:
        return None
    words, first, last = found

    def span(a: int, b: int) -> str:
        return text[words[a].start():words[b].end()]

    def rounded(a: int, b: int) -> str:
        whole = span(*_whole_lines(text, words, a, b))
        return whole if _words(whole) <= MAX_WORDS else span(a, b)

    if points_here(span(first, last), anchor, chunks, question):
        return rounded(first, last)
    for _ in range(len(words)):
        grew = False
        for step in ("right", "left"):
            if step == "right" and last + 1 < len(words):
                last, grew = last + 1, True
            elif step == "left" and first > 0:
                first, grew = first - 1, True
            else:
                continue
            candidate = span(first, last)
            if _words(candidate) > MAX_WORDS:
                return None
            if points_here(candidate, anchor, chunks, question):
                return rounded(first, last)
        if not grew:
            return None
    return None


def repair(ta: TurnAnswer, chunks: dict[str, str], question: str) -> tuple[TurnAnswer, list[dict]]:
    """Each quote that would be dropped as repeated, widened or moved so that it points to one chunk. Returns the answer and what changed."""
    changes: list[dict] = []
    events = []
    for event in ta.events:
        if event.type != "message":
            events.append(event)
            continue
        citations = []
        for ev in event.citations:
            if points_here(ev.quote, ev.anchor, chunks, question) or ev.anchor not in chunks:
                citations.append(ev)
                continue
            # the cited chunk first, then any other chunk of the same version that holds the quote
            homes = [ev.anchor] + sorted(a for a, t in chunks.items() if a != ev.anchor and version_of(a) == version_of(ev.anchor) and quote_in(ev.quote, t))
            fixed = next(((home, q) for home in homes if (q := widen(ev.quote, home, chunks, question))), None)
            if fixed is None:
                citations.append(ev)  # citation_problems says why, and the model is asked again
                continue
            home, quote = fixed
            changes.append({"from": {"anchor": ev.anchor, "quote": ev.quote}, "to": {"anchor": home, "quote": quote}})
            citations.append(ev.model_copy(update={"anchor": home, "quote": quote}))
        events.append(Message.model_validate({**event.model_dump(), "citations": [c.model_dump() for c in citations]}))
    return TurnAnswer(events=events), changes


async def document_chunks(ta: TurnAnswer, passages: Sequence[dict], call_tool: CallTool, tool_log: ToolLog) -> tuple[dict[str, str], list[dict]]:
    """Every chunk the agent can see of each cited document version: the passages, plus one search inside each document per quote.

    Returns anchor -> text, and a row per check search for the turn log. A failed search leaves the passages alone."""
    chunks = {p["anchor"]: p.get("text") or "" for p in passages if p.get("anchor")}
    documents = {p["anchor"]: p.get("document_id") for p in passages if p.get("anchor")}
    asks: dict[tuple[str, str], str] = {}
    for event in ta.events:
        if event.type != "message":
            continue
        for ev in event.citations:
            document = documents.get(ev.anchor) or ev.anchor.split(":")[0]
            asks.setdefault((document, normalise(ev.quote)), ev.quote)

    async def ask(document: str, quote: str) -> dict:
        args = {"query": quote[:1000], "purpose": "context", "top_k": CHECK_TOP_K, "document_ids": [document]}
        row: dict[str, Any] = {"query": quote[:200], "purpose": "context", "scope": "quote_check", "hits": 0, "documents": [document], "failed": False}
        try:
            dumped = await call_tool("search_kb", args)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            log.warning("quote check search failed: %s: %s", type(error).__name__, error)
            row["failed"] = True
            return row
        tool_log.record("search_kb", args, dumped)
        payload, failed = _payload(dumped)
        hits = payload.get("hits") if isinstance(payload, dict) and not failed else None
        rows = [h for h in hits if isinstance(h, dict) and h.get("anchor") and isinstance(h.get("text"), str)] if isinstance(hits, list) else []
        row["failed"] = failed
        row["hits"] = len(rows)
        for hit in rows:
            chunks.setdefault(hit["anchor"], hit["text"])
        return row

    checks = await asyncio.gather(*(ask(document, quote) for (document, _), quote in asks.items()))
    return chunks, list(checks)

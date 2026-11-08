"""Step 3 of the pipeline: one search_kb per question part, plus one for the person's own message when it differs
from every part, all at once, over the session's own MCP connection.

Rules:
- The session's toolset holds the run token. It goes out as a bearer header on these calls and nowhere else.
- Every result is recorded in the session's ToolLog in the shape ADK's after_tool callback records, so the answer
  rules (app/rules.py) work unchanged.
- A search that fails counts as a search with no hits, marked failed. When every search failed, the caller ends the
  turn with an error, not a no_source.
- Home country first: each query is searched among the documents of the workspace's own country (PHARMALIGN_KA_HOME_COUNTRIES,
  default AE, the code the library stores for the UAE, its emirates included), and only when that finds nothing, or the server
  refuses the filter, among the whole library. The library also holds EU, WHO, GCC and other documents, and they can outrank
  the UAE law that answers the question.
- Home passages that are only near the topic do not answer: when the answer model says no_source, widen() runs the same
  queries over the whole library once, and the model answers again on the larger set.
  PHARMALIGN_KA_LOCAL_FIRST=off searches the whole library at once, as before.
"""
import asyncio
import logging
import os
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from google.adk.tools.mcp_tool import McpToolset
from pharmalign_agent_common import Settings, ToolLog
from pharmalign_agent_common.evidence import _payload
from pharmalign_agent_common.runner import toolset_for

try:  # the dump ADK itself records, with the key names the ToolLog reads
    from google.adk.tools.mcp_tool.mcp_tool import _dump_mcp_model
except ImportError:  # pragma: no cover
    def _dump_mcp_model(model: Any) -> dict:
        return model.model_dump(mode="json", exclude_none=True)

log = logging.getLogger(__name__)

TOOL = "search_kb"
TOP_K_ENV = "PHARMALIGN_KA_TOP_K"
DEFAULT_TOP_K = 10
DEFAULT_HOME_COUNTRIES = "AE"
HOME_COUNTRIES_ENV = "PHARMALIGN_KA_HOME_COUNTRIES"
LOCAL_FIRST_ENV = "PHARMALIGN_KA_LOCAL_FIRST"
CallTool = Callable[[str, dict], Awaitable[dict]]
PASSAGE_FIELDS = ("anchor", "document_id", "title", "legal_status", "text", "article", "jurisdiction", "issuer")


@dataclass
class Retrieved:
    passages: list[dict] = field(default_factory=list)  # de-duplicated by anchor, then by text; part searches first, then the message search
    searches: list[dict] = field(default_factory=list)  # query, purpose, hits, documents, searched, failed

    @property
    def nothing_found(self) -> bool:
        return all(s["hits"] == 0 for s in self.searches)

    @property
    def all_failed(self) -> bool:
        return bool(self.searches) and all(s["failed"] for s in self.searches)


def top_k() -> int:
    """Hits asked of each search: PHARMALIGN_KA_TOP_K, default 10."""
    try:
        value = int(os.environ.get(TOP_K_ENV) or DEFAULT_TOP_K)
    except ValueError:
        return DEFAULT_TOP_K
    return value if value > 0 else DEFAULT_TOP_K


def home_countries(env: Mapping[str, str] | None = None) -> frozenset[str]:
    """The country codes whose documents are the workspace's own law, casefolded. PHARMALIGN_KA_HOME_COUNTRIES, comma-separated."""
    env = os.environ if env is None else env
    raw = env.get(HOME_COUNTRIES_ENV) or DEFAULT_HOME_COUNTRIES
    return frozenset(j.strip().casefold() for j in raw.split(",") if j.strip())


def local_first_on(env: Mapping[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    return (env.get(LOCAL_FIRST_ENV) or "on").strip().lower() != "off"


# A question about whether a law or rule still applies, or what replaced it. The answer is in a different law (the one that
# repeals or replaces it), which a search for the old law's own words ranks below the old law itself.
STATUS_QUESTION = re.compile(
    r"\bin[- ]force\b|\brepeal\w*|\bsupersed\w*|\breplac\w*|\brevok\w*|\bno longer (valid|applies|apply|applicable|effective)\b|"
    r"\bstill (valid|applies|apply|applicable|effective|current|good)\b|\bcurrently (valid|applicable|effective)\b",
    re.IGNORECASE,
)
STATUS_SUFFIX = " repealed or replaced by a new law"


def is_status_question(parts: Sequence[str]) -> bool:
    return any(STATUS_QUESTION.search(p) for p in parts)


def _same_words(text: str) -> str:
    """Casefolded, spaces collapsed, trailing punctuation dropped: two texts that agree here are one query."""
    return re.sub(r"[\s.,;:!?\u061f\u2026]+$", "", " ".join(text.casefold().split()))


def session_toolset(settings: Settings, run_token: str) -> McpToolset:
    """The one toolset of a session: search_kb only, token fixed."""
    return toolset_for((TOOL,), settings, run_token)


def mcp_call_tool(toolset: McpToolset) -> CallTool:
    """A `call_tool` that runs a tool through the session's toolset, with the session's bearer header."""
    headers = toolset.header_provider(None)  # ignores its argument

    async def call_tool(name: str, args: dict) -> dict:
        result = await toolset._execute_with_session(
            lambda session: session.call_tool(name, arguments=args), f"{name} failed", headers=headers
        )
        return _dump_mcp_model(result)

    return call_tool


def _hits(payload: Any) -> list[dict]:
    rows = payload.get("hits") if isinstance(payload, dict) else None
    return [r for r in rows if isinstance(r, dict) and r.get("anchor") and isinstance(r.get("text"), str)] if isinstance(rows, list) else []


async def _search(
    part: str, purpose: str, call_tool: CallTool, tool_log: ToolLog, countries: Sequence[str] | None = None
) -> tuple[dict, list[dict]]:
    args: dict[str, Any] = {"query": part, "purpose": purpose, "top_k": top_k()}
    if countries:
        args["countries"] = list(countries)
    row = {"query": part, "purpose": purpose, "scope": "local" if countries else "all", "hits": 0, "documents": [],
           "searched": None, "failed": False}
    try:
        dumped = await call_tool(TOOL, args)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        log.warning("search_kb failed: %s: %s", type(error).__name__, error)
        row["failed"] = True
        return row, []
    tool_log.record(TOOL, args, dumped)
    payload, failed = _payload(dumped)
    if failed:
        row["failed"] = True
        return row, []
    hits = _hits(payload)
    row["hits"] = len(hits)
    row["documents"] = list(dict.fromkeys(str(h["document_id"]) for h in hits if h.get("document_id")))
    searched = payload.get("searched") if isinstance(payload, dict) else None
    row["searched"] = searched if isinstance(searched, int) and not isinstance(searched, bool) else None
    return row, [{k: h.get(k) for k in PASSAGE_FIELDS} for h in hits]


async def _search_local_first(
    query: str, purpose: str, call_tool: CallTool, tool_log: ToolLog, local: Sequence[str]
) -> list[tuple[dict, list[dict]]]:
    """The home country first. The whole library only when that found nothing, or failed (an older server may refuse the filter)."""
    first = await _search(query, purpose, call_tool, tool_log, local)
    if first[0]["hits"] or not local:
        return [first]
    return [first, await _search(query, purpose, call_tool, tool_log)]


async def retrieve(
    parts: Sequence[str], history_wanted: bool, call_tool: CallTool, tool_log: ToolLog, message: str | None = None,
    search_all: bool = False,
) -> Retrieved:
    """One search_kb per part and one for the original message (when it differs from every part), run together.

    Results come back in part order, then the message. A passage found twice is kept once.
    With `search_all` every query goes to the whole library, with no country filter.

    A status question ("is X still in force?", "what replaced X?") gets three searches per part: what is in force now, the
    law with any status, and the part with "repealed or replaced" added. The old law's own text outranks the law that
    repeals it, so one search of either kind can miss the answer."""
    purpose = "context" if history_wanted else "requirement"
    queries = list(parts)
    if message and message.strip() and all(_same_words(message) != _same_words(p) for p in parts):
        queries.append(message)
    specs: list[tuple[str, str]] = [(q, purpose) for q in queries]
    if is_status_question(parts):
        specs += [(q, "requirement") for q in parts] + [(q, "context") for q in parts]
        specs += [(q + STATUS_SUFFIX, "requirement") for q in parts]
        specs = list(dict.fromkeys(specs))
        specs = sorted(specs, key=lambda spec: spec[1] != "requirement" and spec[0] in parts)  # in force first, old law after
    local = sorted(home_countries()) if local_first_on() and not search_all else []
    results = await asyncio.gather(*(_search_local_first(q, p, call_tool, tool_log, local) for q, p in specs))
    out = Retrieved()
    anchors: set[str] = set()
    texts: set[str] = set()
    for row, passages in (done for per_query in results for done in per_query):
        out.searches.append(row)
        for passage in passages:
            if passage["anchor"] in anchors or passage["text"] in texts:
                continue
            anchors.add(passage["anchor"])
            texts.add(passage["text"])
            out.passages.append(passage)
    return out


def _local_only_queries(retrieved: Retrieved) -> list[tuple[str, str]]:
    """The (query, purpose) pairs whose real searches were all home-country ones, in order. A quote check is not a search for an answer."""
    scopes: dict[tuple[str, str], set[str]] = {}
    for row in retrieved.searches:
        if row.get("scope") != "quote_check":
            scopes.setdefault((row["query"], row["purpose"]), set()).add(row.get("scope") or "all")
    return [key for key, found in scopes.items() if found == {"local"}]


def can_widen(retrieved: Retrieved) -> bool:
    return bool(_local_only_queries(retrieved))


async def widen(retrieved: Retrieved, call_tool: CallTool, tool_log: ToolLog, purpose: str) -> int:
    """Searches the whole library for every query that was only searched at home, together, and adds what they find.

    Each query keeps the purpose it was first searched with; `purpose` is for a row that has none.
    Rows and passages are appended to `retrieved`: passages follow the same de-duplication as retrieve() (by anchor,
    then by identical text) and the ones already there keep their place. Returns how many searches ran."""
    pairs = _local_only_queries(retrieved)
    results = await asyncio.gather(*(_search(q, p or purpose, call_tool, tool_log) for q, p in pairs))
    anchors = {p["anchor"] for p in retrieved.passages}
    texts = {p["text"] for p in retrieved.passages}
    for row, passages in results:
        retrieved.searches.append(row)
        for passage in passages:
            if passage["anchor"] in anchors or passage["text"] in texts:
                continue
            anchors.add(passage["anchor"])
            texts.add(passage["text"])
            retrieved.passages.append(passage)
    return len(results)


def searches_tried(retrieved: Retrieved) -> list[dict]:
    """The SearchStep dicts for the searches that really ran. A failed search is not a search that found nothing."""
    steps = []
    for s in retrieved.searches:
        if s["failed"] or s.get("scope") == "quote_check":  # a quote check looks for repeats, it doesn't look for an answer
            continue
        result = "nothing similar enough" if s["hits"] == 0 else f"{s['hits']} passages, none answers the question"
        steps.append({
            "search": f"{TOOL} ({s['purpose']}{', home country' if s.get('scope') == 'local' else ''}): {s['query']}"[:500],
            "result": result[:500],
            "candidates": s["searched"] if isinstance(s["searched"], int) else None,
        })
    return steps

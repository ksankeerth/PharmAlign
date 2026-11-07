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
    parts: Sequence[str], history_wanted: bool, call_tool: CallTool, tool_log: ToolLog, message: str | None = None
) -> Retrieved:
    """One search_kb per part and one for the original message (when it differs from every part), run together.

    Results come back in part order, then the message. A passage found twice is kept once."""
    purpose = "context" if history_wanted else "requirement"
    queries = list(parts)
    if message and message.strip() and all(_same_words(message) != _same_words(p) for p in parts):
        queries.append(message)
    local = sorted(home_countries()) if local_first_on() else []
    results = await asyncio.gather(*(_search_local_first(q, purpose, call_tool, tool_log, local) for q in queries))
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

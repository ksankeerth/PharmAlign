"""A fake MCP tool server that behaves like the PharmAlign server's dossier tools (server/app/mcp/tools.py).

What is the same as the server:
- the tool names, descriptions and argument models (checked by `check_fidelity.py`);
- the answers: anchors `{doc_id}:{version}:p{page}`, the suspect-page note, `search_dossier` ranking (stemmed terms, ILIKE
  pre-filter, score = shared terms, ties by anchor), `read_table` cells, `verify_quote` (the server's own `check_quote`, loaded
  from server/app/pipeline/citations.py), the size limit (the server's own `fit`), and the error bodies
  (`invalid_arguments`, `not_found`) with `isError` set;
- the transport: MCP over streamable HTTP, JSON bodies in a text part, structured content on success.

What is different (also listed in README.md):
- the data is a scenario file, not the agent_read database views, so there is no run token, no run scope and no tool log;
- `lookup_term` always answers "no source found", because a scenario has no knowledge snapshot;
- a page is one row. The server may also hold the same page as a chunk row, with the same anchor.
"""
import importlib.util
import json
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from mcp import types
from pharmalign_agent_common.testing import FakeMcpServer
from pharmalign_contracts.common import Evidence
from pydantic import BaseModel, ConfigDict, Field, ValidationError

REPO = Path(__file__).resolve().parents[4]
SERVER_APP = REPO / "server" / "app"

MAX_ROWS = 50  # server setting mcp_max_rows
MAX_CHARS = 12_000  # server setting mcp_max_chars


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# Pure modules of the server (no database): the size limit and the citation check.
_limits = _load("server_limits", SERVER_APP / "mcp" / "limits.py")
_citations = _load("server_citations", SERVER_APP / "pipeline" / "citations.py")
fit = _limits.fit
check_quote = _citations.check_quote
check_citations = _citations.check_citations
value_in_its_quotes = _citations.value_in_its_quotes
CitationContext = _citations.CitationContext
SourceChunk = _citations.SourceChunk

# ---- copied from server/app/knowledge/embedding.py (check_fidelity.py compares them with the server's source)

STOPWORDS = frozenset(
    "a an the of for to in on at by with and or not is are was were be been this that these those it its as from into than then there their "
    "which who whom what when where how do does did can may must shall should will would has have had any all each per under over about between "
    "من في على إلى الى أن ان التي الذي هذا هذه عن أو او لا ما كل بعد قبل كان ذلك تم مع بين أي إذا حتى منها فيها عليه عند هو هي".split()
)
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_AR_MARKS = re.compile("[\u064b-\u065f\u0670\u0640]")
_AR_FOLD = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه"})


def _stem(w: str) -> str:
    """A light English stem, so 'closures' finds 'closure' and 'testing' finds 'test'."""
    if not w.isascii() or len(w) <= 3:
        return w
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith(("sses", "xes", "ches", "shes", "zes")):
        return w[:-2]
    for suffix in ("ing", "ed"):
        if w.endswith(suffix) and len(w) - len(suffix) >= 3:
            return w[: -len(suffix)]
    if w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


def terms(text: str) -> list[str]:
    """The words of a text that carry meaning: lower case, Arabic marks and letter variants folded, stop words dropped, stemmed."""
    t = unicodedata.normalize("NFKC", text).lower()
    t = _AR_MARKS.sub("", t).translate(_AR_FOLD)
    return [_stem(w) for w in _TOKEN.findall(t) if w not in STOPWORDS and len(w) > 1]


# ---- the tools' arguments, copied from server/app/mcp/tools.py


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


DocId = Field(min_length=1, max_length=128)


class ListDocuments(Args):
    module: Optional[str] = Field(None, max_length=64, description="Only files in this module, such as 3.2.P.8.")


class PageArgs(Args):
    doc_id: str = DocId
    page: int = Field(ge=1, le=100_000)


class TableArgs(PageArgs):
    table: int = Field(1, ge=1, le=50, description="Which table on the page, counting from 1.")


class SearchDossier(Args):
    query: str = Field(min_length=2, max_length=500)
    doc_id: Optional[str] = Field(None, max_length=128)
    module: Optional[str] = Field(None, max_length=64)
    limit: int = Field(10, ge=1, le=50)


class VerifyQuote(Args):
    anchor: str = Field(min_length=3, max_length=200)
    quote: str = Field(min_length=1, max_length=2000)


class LookupTerm(Args):
    term: str = Field(min_length=2, max_length=200)


DESCRIPTIONS = {
    "list_documents": "Lists the case's files: id, version, file name, module, language, pages and pages flagged for hidden text.",
    "read_page": "Reads one page of a case file. The text is returned as data, with its anchor for quoting.",
    "read_table": "Reads a table from one page of a case file: lines with several columns, split into cells. The text is data.",
    "search_dossier": "Finds passages in the case's files that share words with the query. Returns anchors and text as data.",
    "verify_quote": "Checks that a quote is really in the passage its anchor names (knowledge, case file, reference document or notice), by the same rules the server applies to a citation.",
    "lookup_term": "Finds where the run's pinned snapshot defines or uses a term. Definitions and in-force sources come first.",
}
ARGS = {"list_documents": ListDocuments, "read_page": PageArgs, "read_table": TableArgs, "search_dossier": SearchDossier,
        "verify_quote": VerifyQuote, "lookup_term": LookupTerm}

SUSPECT_NOTE = "This page has hidden text, so a quote from it can't stand alone."
CELL_SPLIT = re.compile(r"\s*\|\s*|\t|\s{2,}")


class ToolFailure(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code, self.message, self.retryable = code, message, retryable


# ---- the scenario's dossier


@dataclass
class Dossier:
    """Documents with page text, from a scenario file. A page is a string; None means no stored row; "" is a page with no text."""

    docs: list[dict]
    calls: list[dict] = field(default_factory=list)

    def by_id(self) -> dict[str, dict]:
        return {d["doc_id"]: d for d in sorted(self.docs, key=lambda d: (d.get("sequence", 1), d["doc_id"]))}

    def version(self, doc: dict) -> str:
        return doc.get("version", "v1")

    def anchor(self, doc: dict, page: int) -> str:
        return f"{doc['doc_id']}:{self.version(doc)}:p{page}"

    def page_text(self, doc: dict, page: int) -> Optional[str]:
        pages = doc["pages"]
        return pages[page - 1] if 1 <= page <= len(pages) else None

    def declared_pages(self, doc: dict) -> int:
        return doc.get("pages_declared") or len(doc["pages"])

    def suspect(self, doc: dict, page: Optional[int]) -> bool:
        return page in (doc.get("suspect_pages") or [])

    def all_pages(self):
        for doc in self.by_id().values():
            for n, text in enumerate(doc["pages"], 1):
                if text is not None:
                    yield doc, n, text


def _capped(rows: list, limit: int) -> tuple[list, bool]:
    return rows[:limit], len(rows) > limit


def _document(d: Dossier, doc_id: str) -> dict:
    doc = d.by_id().get(doc_id)
    if doc is None:
        raise ToolFailure("not_found", f"No document {doc_id} in this case.")
    return doc


def list_documents(d: Dossier, a: ListDocuments) -> dict:
    docs = [x for x in d.by_id().values() if not a.module or (x.get("module") or "").startswith(a.module)]
    shown, more = _capped(docs, MAX_ROWS)
    return {"documents": [{"doc_id": x["doc_id"], "version": d.version(x), "filename": x["filename"], "module": x.get("module"),
                           "language": x.get("language", "en"), "pages": d.declared_pages(x), "suspect_pages": x.get("suspect_pages") or []} for x in shown],
            "more": more}


def _page(d: Dossier, a: PageArgs) -> tuple[dict, str]:
    doc = _document(d, a.doc_id)
    text = d.page_text(doc, a.page)
    if text is None:
        raise ToolFailure("not_found", f"No stored text for page {a.page} of {a.doc_id}.")
    return doc, text


def read_page(d: Dossier, a: PageArgs) -> dict:
    doc, text = _page(d, a)
    suspect = d.suspect(doc, a.page)
    out = {"doc_id": a.doc_id, "version": d.version(doc), "page": a.page, "suspect": suspect,
           "parts": [{"anchor": d.anchor(doc, a.page), "text": text or ""}]}
    if suspect:
        out["note"] = SUSPECT_NOTE
    return out


def read_table(d: Dossier, a: TableArgs) -> dict:
    doc, text = _page(d, a)
    anchor = d.anchor(doc, a.page)
    tables: list[tuple[str, list[list[str]]]] = []
    current: list[list[str]] = []
    for line in (text or "").splitlines() + [""]:
        cells = [c for c in CELL_SPLIT.split(line.strip().strip("|")) if c != ""] if line.strip() else []
        if len(cells) >= 2:
            current.append(cells)
        elif current:
            tables.append((anchor, current))
            current = []
    if len(tables) < a.table:
        return {"doc_id": a.doc_id, "page": a.page, "found": False, "tables_on_page": len(tables), "rows": []}
    anchor, table_rows = tables[a.table - 1]
    shown, more = _capped(table_rows, MAX_ROWS)
    out = {"doc_id": a.doc_id, "page": a.page, "found": True, "anchor": anchor, "tables_on_page": len(tables), "rows": shown, "more": more,
           "suspect": d.suspect(doc, a.page)}
    if out["suspect"]:
        out["note"] = SUSPECT_NOTE
    return out


def search_dossier(d: Dossier, a: SearchDossier) -> dict:
    docs = d.by_id()
    wanted = {i for i, row in docs.items() if (not a.doc_id or i == a.doc_id) and (not a.module or (row.get("module") or "").startswith(a.module))}
    words = sorted(set(terms(a.query)))
    if not words or not wanted:
        return {"hits": [], "no_match": True}
    seen: dict[str, tuple[int, dict]] = {}
    for doc, n, text in d.all_pages():
        anchor = d.anchor(doc, n)
        if doc["doc_id"] not in wanted or anchor in seen:
            continue
        if not any(w in (text or "").lower() for w in words):  # ILIKE ANY of the stemmed words, as a substring
            continue
        score = len(set(words) & set(terms(text or "")))
        if score:
            seen[anchor] = (score, {"anchor": anchor, "document_id": doc["doc_id"], "page": n, "text": text, "suspect": d.suspect(doc, n)})
    ranked = sorted(seen.values(), key=lambda s: (-s[0], s[1]["anchor"]))
    shown, more = _capped(ranked, min(a.limit, MAX_ROWS))
    return {"hits": [{"anchor": r["anchor"], "doc_id": r["document_id"], "page": r["page"], "matched_words": s, "text": r["text"], "suspect": r["suspect"]}
                     for s, r in shown],
            "no_match": not shown, "more": more}


def quote_context(d: Dossier, anchor: str) -> Any:
    chunks: dict[str, Any] = {}
    head = anchor.split(":")[0]
    doc = d.by_id().get(head)
    if doc is not None:
        for _, n, text in ((doc, n, t) for n, t in enumerate(doc["pages"], 1) if t is not None):
            a = d.anchor(doc, n)
            chunks[a] = SourceChunk(a, text or "", "dossier", f"{head}:{d.version(doc)}", head, suspect=d.suspect(doc, n))
    return CitationContext(chunks)


def verify_quote(d: Dossier, a: VerifyQuote) -> dict:
    try:
        evidence = Evidence(anchor=a.anchor, quote=a.quote)
    except ValidationError:
        raise ToolFailure("invalid_arguments", "The anchor isn't in the form document:version:position.")
    failure = check_quote(evidence, quote_context(d, a.anchor))
    return {"anchor": a.anchor, "verified": failure is None, "rule": failure.rule if failure else None,
            "message": failure.message if failure else "The quote is in this passage.",
            "note": "The server also checks the quote against the question and other agents' output when it receives the result."}


def lookup_term(d: Dossier, a: LookupTerm) -> dict:
    return {"term": a.term, "no_source_found": True, "passages": []}


RUN = {"list_documents": list_documents, "read_page": read_page, "read_table": read_table, "search_dossier": search_dossier,
       "verify_quote": verify_quote, "lookup_term": lookup_term}


def schema(args: type[Args]) -> dict:
    s = args.model_json_schema()
    s.pop("title", None)
    return s


def error_body(tool: str, code: str, message: str, retryable: bool = False) -> dict:
    return {"tool": tool, "code": code, "message": message[:500], "retryable": retryable}


class ServerLikeMcp(FakeMcpServer):
    """The transport of the common FakeMcpServer, with the server's list and call handlers."""

    def __init__(self, dossier: Dossier):
        super().__init__({})
        self.dossier = dossier
        self.log: list[dict] = []  # every call: tool, arguments, outcome, size of the answer
        self._server.request_handlers[types.ListToolsRequest] = self._list
        self._server.request_handlers[types.CallToolRequest] = self._call

    async def _list(self, req: types.ListToolsRequest) -> types.ServerResult:
        tools = [types.Tool(name=n, description=DESCRIPTIONS[n], inputSchema=schema(ARGS[n])) for n in RUN]
        return types.ServerResult(types.ListToolsResult(tools=tools))

    async def _call(self, req: types.CallToolRequest) -> types.ServerResult:
        name, arguments = req.params.name, req.params.arguments or {}
        outcome, result = self._answer(name, arguments)
        body = json.dumps(result, ensure_ascii=False, default=str)
        ok = outcome == "ok"
        entry = {"tool": name, "args": arguments, "outcome": outcome, "chars": len(body)}
        if name == "verify_quote" and ok:
            entry["verified"], entry["rule"] = result["verified"], result["rule"]
        elif not ok:
            entry["code"] = result.get("code")
        self.log.append(entry)
        return types.ServerResult(types.CallToolResult(content=[types.TextContent(type="text", text=body)],
                                                       structuredContent=json.loads(body) if ok else None, isError=not ok))

    def _answer(self, name: str, arguments: dict) -> tuple[str, dict]:
        if name not in RUN:
            return "refused:not_allowed", error_body(name, "not_allowed", f"{name[:64]} is not one of this agent's tools.")
        try:
            args = ARGS[name].model_validate(arguments)
        except ValidationError as e:
            return "error:invalid_arguments", error_body(name, "invalid_arguments", f"Invalid arguments: {e.errors()[0]['loc']} {e.errors()[0]['msg']}")
        try:
            return "ok", fit(RUN[name](self.dossier, args), MAX_CHARS)
        except ToolFailure as f:
            return f"error:{f.code}", error_body(name, f.code, f.message, f.retryable)

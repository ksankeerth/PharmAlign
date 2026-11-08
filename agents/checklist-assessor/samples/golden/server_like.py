"""A fake MCP tool server that behaves like the PharmAlign server's tools for the Checklist Assessor (server/app/mcp/tools.py).

What is the same as the server:
- the tool names, descriptions and argument models (checked by `check_fidelity.py`);
- the answers: dossier anchors `{doc_id}:{version}:p{page}`, law anchors `{doc_id}:{version}:c{position}`, the suspect-page note,
  `search_dossier` ranking (stemmed terms, ILIKE pre-filter, score = shared terms, ties by anchor), `read_table` cells, `read_clause`
  matching and order, `get_doc_status` rows, `lookup_term` order, `get_facts` and `get_site` shapes, `compute` (copied), `verify_quote` (the
  server's own `check_quote`, loaded from server/app/pipeline/citations.py), the size limit (the server's own `fit`), and the error bodies
  (`invalid_arguments`, `not_found`, `outside_check_sources`) with `isError` set;
- the check's reach: a check with a source may read only that document (or the law a person moved it to) and its supporting sources.
  The same rule as `check_documents` in server/app/mcp/server.py;
- the transport: MCP over streamable HTTP, JSON bodies in a text part, structured content on success.

What is different (also listed in README.md):
- the data is a scenario file, not the agent_read database views, so there is no run token, no run scope and no tool log;
- `search_kb` does not use embeddings. A chunk is a hit when it shares at least two meaningful words with the query and the share is at
  least `KB_FLOOR` of the geometric mean of the two word counts. The real search ranks by meaning and is more generous, so a scenario that
  needs "no source found" holds law that shares no words with the question;
- `purpose: requirement` returns in-force chunks only, as the server's `kb_requirement_chunk` view does; `context` returns any status;
- a page is one row. The server may also hold the same page as a chunk row, with the same anchor.
"""
import ast
import importlib.util
import json
import math
import operator
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Literal, Optional

from mcp import types
from pharmalign_agent_common.testing import FakeMcpServer
from pharmalign_contracts.common import Evidence
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

REPO = Path(__file__).resolve().parents[4]
SERVER_APP = REPO / "server" / "app"

MAX_ROWS = 50  # server setting mcp_max_rows
MAX_CHARS = 12_000  # server setting mcp_max_chars
KB_FLOOR = 0.12  # stands in for the embedder's similarity floor


def _load(name: str, path: Path, patch: tuple[str, str] | None = None):
    source = path.read_text()
    if patch:
        source = source.replace(*patch)
    spec = importlib.util.spec_from_loader(name, loader=None)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    exec(compile(source, str(path), "exec"), module.__dict__)  # noqa: S102 - the server's own pure module
    return module


# Pure modules of the server (no database): the size limit, the citation check, and the rule engine for typed checks.
_limits = _load("server_limits", SERVER_APP / "mcp" / "limits.py")
_citations = _load("server_citations", SERVER_APP / "pipeline" / "citations.py")
_typed_rules = _load("server_typed_rules", SERVER_APP / "pipeline" / "typed_rules.py", ("from .citations import normalise", "from server_citations import normalise"))
fit = _limits.fit
check_quote = _citations.check_quote
check_citations = _citations.check_citations
unsupported_numbers = _citations.unsupported_numbers
value_in_its_quotes = _citations.value_in_its_quotes
CitationContext = _citations.CitationContext
SourceChunk = _citations.SourceChunk
evaluate_rule = _typed_rules.evaluate

# ---- copied from server/app/knowledge/embedding.py and search.py (check_fidelity.py compares them with the server's source)

STOPWORDS = frozenset(
    "a an the of for to in on at by with and or not is are was were be been this that these those it its as from into than then there their "
    "which who whom what when where how do does did can may must shall should will would has have had any all each per under over about between "
    "من في على إلى الى أن ان التي الذي هذا هذه عن أو او لا ما كل بعد قبل كان ذلك تم مع بين أي إذا حتى منها فيها عليه عند هو هي".split()
)
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_AR_MARKS = re.compile("[ً-ٰٟـ]")
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


def like_contains(term: str) -> str:
    """An ILIKE pattern that matches `term` anywhere, with the pattern characters in it taken literally. Use with ESCAPE '\\'."""
    return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def clause_matches(clause: str, *fields: Optional[str]) -> bool:
    """A field holds the clause as a whole label: "3.2.6.1" is in "3.2.6.1 General case" but not in "3.2.6.10", "Article 6" not in "Article 60"."""
    want = clause.strip()
    if not want:
        return False
    pattern = re.compile(r"(?<![0-9A-Za-z])" + re.escape(want) + r"(?![0-9])", re.IGNORECASE)
    return any(f is not None and pattern.search(f) is not None for f in fields)


# ---- the tools' arguments, copied from server/app/mcp/tools.py

DocId = Field(min_length=1, max_length=128)


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


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


class SearchKb(Args):
    query: str = Field(min_length=2, max_length=1000)
    purpose: Literal["requirement", "context"] = Field("requirement", description="requirement: in-force sources only. context: any status, labelled.")
    top_k: int = Field(5, ge=1, le=20)
    document_ids: Optional[list[Annotated[str, StringConstraints(min_length=1, max_length=128)]]] = Field(
        None, max_length=20, description="Search only inside these knowledge documents, e.g. the check's source document.")
    countries: Optional[list[Annotated[str, StringConstraints(min_length=2, max_length=8)]]] = Field(
        None, max_length=12, description="Search only documents of these countries, as the codes the library stores, such as AE for the UAE (case ignored). "
        "A document with no country code is left out. Use it to look in the home country first, then search again without it if nothing is found.")


CLAUSE_COLUMNS = "anchor, document_id, document_version_id, title, issuer, legal_status, authority_level, usable_as_requirement, article, section_path, page, language, text"


class ReadClause(Args):
    anchor: Optional[str] = Field(None, max_length=200, description="An anchor a search returned.")
    document_id: Optional[str] = Field(None, max_length=128)
    article: Optional[str] = Field(None, max_length=200, description="With document_id: the article or clause to read.")


class DocStatus(Args):
    document_id: str = DocId


class LookupTerm(Args):
    term: str = Field(min_length=2, max_length=200)


class GetFacts(Args):
    keys: Optional[list[str]] = Field(None, max_length=100, description="Only these fact keys.")


OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.Mod: operator.mod, ast.Pow: operator.pow,
       ast.USub: operator.neg, ast.UAdd: operator.pos}
FUNCS = {"min": min, "max": max, "abs": abs, "round": round}


class ToolFailure(Exception):
    """A clear error for the agent. Never an exception that leaves the tool server."""

    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code, self.message, self.retryable = code, message, retryable


def _eval(node) -> float:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.UnaryOp) and type(node.op) in OPS:
        return OPS[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in OPS:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 10:
            raise ToolFailure("invalid_arguments", "Powers above 10 aren't allowed.")
        return OPS[type(node.op)](left, right)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FUNCS and not node.keywords and node.args:
        return FUNCS[node.func.id](*[_eval(x) for x in node.args])
    raise ToolFailure("invalid_arguments", "Only numbers, + - * / % **, brackets and min, max, abs, round are allowed.")


class Compute(Args):
    expression: str = Field(min_length=1, max_length=200, description="Arithmetic, such as (36 - 24) / 24 * 100.")


DESCRIPTIONS = {
    "read_page": "Reads one page of a case file. The text is returned as data, with its anchor for quoting.",
    "read_table": "Reads a table from one page of a case file: lines with several columns, split into cells. The text is data.",
    "search_dossier": "Finds passages in the case's files that share words with the query. Returns anchors and text as data.",
    "verify_quote": "Checks that a quote is really in the passage its anchor names (knowledge, case file, reference document or notice), by the same rules the server applies to a citation.",
    "search_kb": "Searches the run's pinned knowledge snapshot, optionally only inside given documents or countries. Filters first, then ranks. Says no_source_found when nothing qualifies.",
    "read_clause": "Reads a clause of the run's pinned snapshot by anchor, or by document and article, with its legal status.",
    "get_doc_status": "Says what the run's pinned snapshot holds for a knowledge document: each version's legal status, authority and whether it can be read.",
    "lookup_term": "Finds where the run's pinned snapshot defines or uses a term. Definitions and in-force sources come first.",
    "get_facts": "Returns the facts extracted from the case's files, each with its quotes.",
    "get_site": "Returns the case's manufacturing and other site facts, with quotes, and the applicant on record.",
    "compute": "Does arithmetic exactly, so a comparison never rests on a model's sums.",
}
ARGS = {"read_page": PageArgs, "read_table": TableArgs, "search_dossier": SearchDossier, "verify_quote": VerifyQuote, "search_kb": SearchKb,
        "read_clause": ReadClause, "get_doc_status": DocStatus, "lookup_term": LookupTerm, "get_facts": GetFacts, "get_site": Args, "compute": Compute}

SUSPECT_NOTE = "This page has hidden text, so a quote from it can't stand alone."
CELL_SPLIT = re.compile(r"\s*\|\s*|\t|\s{2,}")


# ---- the scenario's dossier and law


@dataclass
class Dossier:
    """Documents with page text, from a scenario file. A page is a string; None means no stored row; "" is a page with no text."""

    docs: list[dict]

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


@dataclass
class Law:
    """Knowledge documents with versions and clauses, from a scenario file. Every text is invented for the scenario."""

    docs: list[dict]
    chunks: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        for d in self.docs:
            for v in d["versions"]:
                version_id = f"{d['doc_id']}:{v['version']}"
                for position, c in enumerate(v["clauses"], 1):
                    self.chunks.append({
                        "anchor": f"{version_id}:c{position:04d}", "document_id": d["doc_id"], "document_version_id": version_id, "title": d["title"],
                        "issuer": d.get("issuer", "Example Authority"), "country": d.get("country", "AE"), "legal_status": v.get("legal_status", "in_force"),
                        "authority_level": v.get("authority_level", 2), "usable_as_requirement": v.get("legal_status", "in_force") == "in_force",
                        "article": c.get("article"), "section_path": c.get("section_path") or c.get("article"), "page": c.get("page"),
                        "language": c.get("language", d.get("language", "en")), "text": c["text"], "chunk_type": c.get("chunk_type", "clause"),
                        "position": position,
                    })

    def anchor_of(self, doc_id: str, version: str, article: str) -> str:
        return next(c["anchor"] for c in self.chunks if c["document_id"] == doc_id and c["document_version_id"] == f"{doc_id}:{version}" and c["article"] == article)

    def row(self, c: dict, extra: tuple[str, ...] = ()) -> dict:
        return {k: c[k] for k in [x.strip() for x in CLAUSE_COLUMNS.split(",")] + list(extra)}


def _capped(rows: list, limit: int) -> tuple[list, bool]:
    return rows[:limit], len(rows) > limit


@dataclass
class World:
    """One scenario as the tool server sees it."""

    dossier: Dossier
    law: Law
    case_facts: list[dict]
    applicant: Optional[dict]
    documents: Optional[tuple[str, ...]]  # the knowledge documents this check may read; None means the whole snapshot (a check with no source)


def reach(request: dict) -> Optional[tuple[str, ...]]:
    """What `check_documents` in server/app/mcp/server.py returns for a run."""
    check = request["check"]
    main = request.get("moved_source") or check.get("source")
    if not main:
        return None
    return tuple(dict.fromkeys([main["doc_id"], *(s["doc_id"] for s in check.get("supporting_sources") or [])]))


def _in_scope(w: World, doc_ids: list[str]) -> None:
    outside = [d for d in doc_ids if w.documents is not None and d not in w.documents]
    if outside:
        raise ToolFailure("outside_check_sources", f"This check may read only {', '.join(w.documents)}. {outside[0][:64]} is outside it.")


# ---- dossier tools


def _document(w: World, doc_id: str) -> dict:
    doc = w.dossier.by_id().get(doc_id)
    if doc is None:
        raise ToolFailure("not_found", f"No document {doc_id} in this case.")
    return doc


def _page(w: World, a: PageArgs) -> tuple[dict, str]:
    doc = _document(w, a.doc_id)
    text = w.dossier.page_text(doc, a.page)
    if text is None:
        raise ToolFailure("not_found", f"No stored text for page {a.page} of {a.doc_id}.")
    return doc, text


def read_page(w: World, a: PageArgs) -> dict:
    d = w.dossier
    doc, text = _page(w, a)
    suspect = d.suspect(doc, a.page)
    out = {"doc_id": a.doc_id, "version": d.version(doc), "page": a.page, "suspect": suspect,
           "parts": [{"anchor": d.anchor(doc, a.page), "text": text or ""}]}
    if suspect:
        out["note"] = SUSPECT_NOTE
    return out


def read_table(w: World, a: TableArgs) -> dict:
    d = w.dossier
    doc, text = _page(w, a)
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


def search_dossier(w: World, a: SearchDossier) -> dict:
    d = w.dossier
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
        if not any(x in (text or "").lower() for x in words):  # ILIKE ANY of the stemmed words, as a substring
            continue
        score = len(set(words) & set(terms(text or "")))
        if score:
            seen[anchor] = (score, {"anchor": anchor, "document_id": doc["doc_id"], "page": n, "text": text, "suspect": d.suspect(doc, n)})
    ranked = sorted(seen.values(), key=lambda s: (-s[0], s[1]["anchor"]))
    shown, more = _capped(ranked, min(a.limit, MAX_ROWS))
    return {"hits": [{"anchor": r["anchor"], "doc_id": r["document_id"], "page": r["page"], "matched_words": s, "text": r["text"], "suspect": r["suspect"]}
                     for s, r in shown],
            "no_match": not shown, "more": more}


# ---- quotes


def quote_context(w: World, anchor: str) -> Any:
    """What the server's `_quote_context` builds: the case's file, or the whole version of the cited law, with its legal status."""
    d = w.dossier
    chunks: dict[str, Any] = {}
    head = anchor.split(":")[0]
    doc = d.by_id().get(head)
    if doc is not None:
        for n, t in enumerate(doc["pages"], 1):
            if t is not None:
                a = d.anchor(doc, n)
                chunks[a] = SourceChunk(a, t or "", "dossier", f"{head}:{d.version(doc)}", head, suspect=d.suspect(doc, n))
        return CitationContext(chunks)
    cited = next((c for c in w.law.chunks if c["anchor"] == anchor), None)
    if cited is not None:
        for c in w.law.chunks:
            if c["document_version_id"] == cited["document_version_id"]:
                chunks[c["anchor"]] = SourceChunk(c["anchor"], c["text"], "kb", c["document_version_id"], c["document_id"], in_force=c["legal_status"] == "in_force")
    return CitationContext(chunks)


def verify_quote(w: World, a: VerifyQuote) -> dict:
    try:
        evidence = Evidence(anchor=a.anchor, quote=a.quote)
    except ValidationError:
        raise ToolFailure("invalid_arguments", "The anchor isn't in the form document:version:position.")
    failure = check_quote(evidence, quote_context(w, a.anchor))
    return {"anchor": a.anchor, "verified": failure is None, "rule": failure.rule if failure else None,
            "message": failure.message if failure else "The quote is in this passage.",
            "note": "The server also checks the quote against the question and other agents' output when it receives the result."}


# ---- knowledge tools


def _hit(c: dict, score: float) -> dict:
    return {"anchor": c["anchor"], "text": c["text"], "score": round(float(score), 3), "document_id": c["document_id"], "title": c["title"], "issuer": c["issuer"],
            "jurisdiction": c["country"] or "-", "legal_status": c["legal_status"], "authority_level": c["authority_level"], "article": c["article"], "page": c["page"]}


def _similarity(query: str, text: str) -> tuple[float, int]:
    q, t = set(terms(query)), set(terms(text))
    shared = len(q & t)
    return (shared / math.sqrt(len(q) * len(t)) if q and t else 0.0), shared


def search_kb(w: World, a: SearchKb) -> dict:
    docs = a.document_ids
    if w.documents is not None:
        _in_scope(w, docs or [])
        docs = docs or list(w.documents)
    requirement = a.purpose == "requirement"
    places = {c.strip().lower() for c in a.countries if c.strip()} if a.countries else None
    pool = [c for c in w.law.chunks if (not requirement or c["usable_as_requirement"]) and (not docs or c["document_id"] in docs)
            and (not places or (c["country"] or "").lower() in places)]
    scored = sorted(((_similarity(a.query, c["text"] + " " + (c["title"] or "")), c) for c in pool), key=lambda s: (-s[0][0], s[1]["anchor"]))
    hits = [_hit(c, sim) for (sim, shared), c in scored if sim >= KB_FLOOR and shared >= 2][: min(a.top_k, MAX_ROWS)]
    hits.sort(key=lambda h: (-h["score"], h["authority_level"], h["anchor"]))
    tried = [] if hits else [{"search": "meaning and words, inside the run's pinned snapshot", "candidates": len(pool), "floor": KB_FLOOR,
                              "best_score": round(scored[0][0][0], 3) if scored else None}]
    return {"no_source_found": not hits, "hits": hits, "searched": len(pool), "purpose": a.purpose, "searches_tried": tried}


def read_clause(w: World, a: ReadClause) -> dict:
    if a.anchor:
        _in_scope(w, [a.anchor.split(":")[0]])
        rows = [c for c in w.law.chunks if c["anchor"] == a.anchor]
    elif a.document_id and a.article:
        _in_scope(w, [a.document_id])
        pattern = like_contains(a.article).strip("%").lower()
        rows = sorted((c for c in w.law.chunks if c["document_id"] == a.document_id and
                       (pattern in (c["article"] or "").lower() or pattern in (c["section_path"] or "").lower())),
                      key=lambda c: (c["document_version_id"], c["position"]))[:200]
        rows = [r for r in rows if clause_matches(a.article, r["article"], r["section_path"])]
    else:
        raise ToolFailure("invalid_arguments", "Give an anchor, or a document_id with an article.")
    if not rows:
        raise ToolFailure("not_found", "No such clause in this run's knowledge snapshot.")
    shown, more = _capped([w.law.row(c) for c in rows], MAX_ROWS)
    return {"clauses": shown, "more": more}


def get_doc_status(w: World, a: DocStatus) -> dict:
    versions: dict[str, dict] = {}
    for d in w.law.docs:
        if d["doc_id"] != a.document_id:
            continue
        for v in d["versions"]:
            versions[f"{d['doc_id']}:{v['version']}"] = {
                "document_id": d["doc_id"], "document_version_id": f"{d['doc_id']}:{v['version']}", "title": d["title"], "issuer": d.get("issuer", "Example Authority"),
                "country": d.get("country", "AE"), "doc_type": d.get("doc_type", "guideline"), "legal_status": v.get("legal_status", "in_force"),
                "authority_level": v.get("authority_level", 2), "review_status": "approved", "quality_status": "pass",
                "published_on": v.get("published_on", "2025-01-01"), "effective_on": v.get("effective_on", "2025-03-01"), "readable": v.get("readable", True)}
    if not versions:
        raise ToolFailure("not_found", f"{a.document_id} is not in this run's knowledge snapshot.")
    rows = [versions[k] for k in sorted(versions)]
    return {"document_id": a.document_id, "versions": rows, "in_force": any(r["legal_status"] == "in_force" and r["readable"] for r in rows)}


def lookup_term(w: World, a: LookupTerm) -> dict:
    needle = a.term.lower()
    rows = [c for c in w.law.chunks if needle in c["text"].lower() and (w.documents is None or c["document_id"] in w.documents)]
    rows.sort(key=lambda c: (not (c["chunk_type"] == "definition" or "definition" in (c["section_path"] or "").lower() or "definition" in (c["article"] or "").lower()),
                             not c["usable_as_requirement"], c["authority_level"], c["anchor"]))
    shown = [w.law.row(c, ("chunk_type",)) for c in rows[: min(5, MAX_ROWS)]]
    return {"term": a.term, "no_source_found": not shown, "passages": shown}


# ---- the case


def _fact(r: dict) -> dict:
    return {"key": r["key"], "status": r["status"], "value": r["value"], "evidence": r["evidence"] or []}


def get_facts(w: World, a: GetFacts) -> dict:
    facts = [_fact(r) for r in sorted(w.case_facts, key=lambda r: r["key"]) if not a.keys or r["key"] in a.keys]
    shown, more = _capped(facts, MAX_ROWS)
    return {"facts": shown, "more": more}


def get_site(w: World, a: Args) -> dict:
    facts = [_fact(r) for r in sorted(w.case_facts, key=lambda r: r["key"]) if "site" in r["key"]]
    return {"sites": facts, "applicant": w.applicant, "no_site_found": not any(f["status"] == "found" for f in facts)}


def compute(w: World, a: Compute) -> dict:
    try:
        value = _eval(ast.parse(a.expression, mode="eval"))
    except ToolFailure:
        raise
    except (SyntaxError, ZeroDivisionError, OverflowError, TypeError, ValueError) as e:
        raise ToolFailure("invalid_arguments", f"Can't compute that: {e.__class__.__name__}.")
    return {"expression": a.expression, "value": value}


RUN = {"search_kb": search_kb, "read_clause": read_clause, "get_doc_status": get_doc_status, "read_page": read_page, "read_table": read_table,
       "search_dossier": search_dossier, "get_facts": get_facts, "get_site": get_site, "verify_quote": verify_quote, "compute": compute, "lookup_term": lookup_term}


def schema(args: type[Args]) -> dict:
    s = args.model_json_schema()
    s.pop("title", None)
    return s


def error_body(tool: str, code: str, message: str, retryable: bool = False) -> dict:
    return {"tool": tool, "code": code, "message": message[:500], "retryable": retryable}


class ServerLikeMcp(FakeMcpServer):
    """The transport of the common FakeMcpServer, with the server's list and call handlers."""

    def __init__(self, world: World):
        super().__init__({})
        self.world = world
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
        elif name == "search_kb":
            entry["no_source_found"] = result["no_source_found"]
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
            return "ok", fit(RUN[name](self.world, args), MAX_CHARS)
        except ToolFailure as f:
            return f"error:{f.code}", error_body(name, f.code, f.message, f.retryable)

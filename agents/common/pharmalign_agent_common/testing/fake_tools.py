"""Canned case data served by tools that return what the server's MCP tools return.

    case = FakeCase(pages=[Page("d-17", 2, "a shelf life of 36 months")], clauses=[...])
    with FakeMcpServer(case.tools(SPEC.tools)) as mcp: ...

Shapes follow the server's tools: anchors, field names and the no-match forms. A missing page or
clause raises, which the MCP server turns into a tool error, as the server does.
"""
import ast
import operator
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from ..evidence import quote_in

_WORD = re.compile(r"[^\W_]{3,}")
_CELLS = re.compile(r"\s*\|\s*|\t|\s{2,}")


def _words(text: str) -> set[str]:
    return {w.casefold() for w in _WORD.findall(text)}


@dataclass
class Page:
    doc_id: str
    page: int
    text: str
    version: str = "v1"
    filename: str = ""
    module: str | None = None
    language: str = "en"
    suspect: bool = False

    @property
    def anchor(self) -> str:
        return f"{self.doc_id}:{self.version}:p{self.page}"


@dataclass
class Clause:
    anchor: str  # such as "E-S25:v3:c0412"
    text: str
    article: str | None = None
    title: str = "Example guideline"
    issuer: str = "Example Authority"
    legal_status: str = "in_force"
    authority_level: int = 2
    language: str = "en"
    chunk_type: str = "clause"

    @property
    def document_id(self) -> str:
        return self.anchor.split(":")[0]

    @property
    def version_id(self) -> str:
        return ":".join(self.anchor.split(":")[:2])

    def row(self) -> dict:
        return {
            "anchor": self.anchor, "document_id": self.document_id, "document_version_id": self.version_id,
            "title": self.title, "issuer": self.issuer, "legal_status": self.legal_status,
            "authority_level": self.authority_level, "usable_as_requirement": self.legal_status == "in_force",
            "article": self.article, "section_path": self.article, "page": None, "language": self.language, "text": self.text,
        }


@dataclass
class Notice:
    """A foreign regulator's notice, as read_notice returns it."""

    notice_id: str
    text: str
    revision: int = 1
    regulator: str = "Example Regulator"
    title: str = "Example notice"
    published_on: str = "2026-11-02"
    severity: str | None = None
    language: str = "en"

    @property
    def anchor(self) -> str:
        return f"{self.notice_id}:r{self.revision}:c1"

    def row(self, text: bool = True) -> dict:
        row = {"notice_id": self.notice_id, "regulator": self.regulator, "reference": f"ref-{self.notice_id}", "title": self.title,
               "language": self.language, "published_on": self.published_on, "retracted": False, "severity": self.severity,
               "revision": self.revision, "anchor": self.anchor, "is_retraction": False}
        return {**row, "text": self.text} if text else row


_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Mod: operator.mod, ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos}
_FUNCS = {"min": min, "max": max, "abs": abs, "round": round}


def _eval(node) -> float:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS:
        return _FUNCS[node.func.id](*[_eval(a) for a in node.args])
    raise ValueError("Only numbers, + - * / % **, brackets and min, max, abs, round are allowed.")


@dataclass
class FakeCase:
    pages: list[Page] = field(default_factory=list)
    clauses: list[Clause] = field(default_factory=list)
    facts: list[dict] = field(default_factory=list)  # {key, status, value, evidence}
    references: list[dict] = field(default_factory=list)  # read_reference rows
    reference_pages: list[Page] = field(default_factory=list)  # pages of the reference documents, read only with read_reference
    applicant: dict | None = None
    findings: list[dict] = field(default_factory=list)  # get_findings rows: check_id, status, summary, evidence ...
    conflicts: list[dict] = field(default_factory=list)  # {fact, values, check_ids}
    previous: dict | None = None  # {"previous_run": ..., "previous_recommendation": ..., "results": [...]}
    notices: list[Notice] = field(default_factory=list)
    matched_notices: dict[str, list[dict]] = field(default_factory=dict)  # notice_id -> match rows
    relied_on: list[dict] = field(default_factory=list)  # {id, target_type, target_id, regulator, approval_ref, ...}
    licences: list[dict] = field(default_factory=list)  # {licence_id, medicine_name, ...}
    signals: list[dict] = field(default_factory=list)  # {id, kind, detail, count}
    pack_rules: list[dict] = field(default_factory=list)  # {check_id, version, rule}
    traces: dict[str, dict] = field(default_factory=dict)  # check_id -> saved trace

    # ------------------------------------------------------------ dossier

    def _page(self, args: dict) -> Page:
        for p in self.pages:
            if p.doc_id == args.get("doc_id") and p.page == int(args.get("page", 0)):
                return p
        raise ValueError(f"No stored text for page {args.get('page')} of {args.get('doc_id')}.")

    def list_documents(self, args: dict) -> dict:
        docs: dict[str, dict] = {}
        for p in sorted(self.pages, key=lambda p: (p.doc_id, p.page)):
            if args.get("module") and not (p.module or "").startswith(args["module"]):
                continue
            d = docs.setdefault(p.doc_id, {"doc_id": p.doc_id, "version": p.version, "filename": p.filename or f"{p.doc_id}.pdf",
                                           "module": p.module, "language": p.language, "pages": 0, "suspect_pages": []})
            d["pages"] = max(d["pages"], p.page)
            if p.suspect:
                d["suspect_pages"].append(p.page)
        return {"documents": list(docs.values()), "more": False}

    def read_page(self, args: dict) -> dict:
        p = self._page(args)
        out = {"doc_id": p.doc_id, "version": p.version, "page": p.page, "suspect": p.suspect,
               "parts": [{"anchor": p.anchor, "text": p.text}]}
        if p.suspect:
            out["note"] = "This page has hidden text, so a quote from it can't stand alone."
        return out

    def read_table(self, args: dict) -> dict:
        p = self._page(args)
        tables, current = [], []
        for line in p.text.splitlines() + [""]:
            cells = [c for c in _CELLS.split(line.strip().strip("|")) if c] if line.strip() else []
            if len(cells) >= 2:
                current.append(cells)
            elif current:
                tables.append(current)
                current = []
        n = int(args.get("table", 1))
        if len(tables) < n:
            return {"doc_id": p.doc_id, "page": p.page, "found": False, "tables_on_page": len(tables), "rows": []}
        return {"doc_id": p.doc_id, "page": p.page, "found": True, "anchor": p.anchor, "tables_on_page": len(tables),
                "rows": tables[n - 1], "more": False, "suspect": p.suspect}

    def search_dossier(self, args: dict) -> dict:
        words = _words(str(args.get("query", "")))
        hits = []
        for p in self.pages:
            if args.get("doc_id") and p.doc_id != args["doc_id"]:
                continue
            if args.get("module") and not (p.module or "").startswith(args["module"]):
                continue
            score = len(words & _words(p.text))
            if score:
                hits.append((score, p))
        if not hits:
            return {"hits": [], "no_match": True}
        hits.sort(key=lambda h: (-h[0], h[1].anchor))
        shown = hits[: int(args.get("limit", 10))]
        return {"hits": [{"anchor": p.anchor, "doc_id": p.doc_id, "page": p.page, "matched_words": s, "text": p.text,
                          "suspect": p.suspect} for s, p in shown], "no_match": False, "more": len(hits) > len(shown)}

    # ------------------------------------------------------------ quotes

    def _text_at(self, anchor: str) -> str | None:
        for p in self.pages:
            if p.anchor == anchor:
                return p.text
        for c in self.clauses:
            if c.anchor == anchor:
                return c.text
        for p in self.reference_pages:
            if p.anchor == anchor:
                return p.text
        for n in self.notices:
            if n.anchor == anchor:
                return n.text
        return None

    def verify_quote(self, args: dict) -> dict:
        anchor, quote = str(args.get("anchor", "")), str(args.get("quote", ""))
        if len(anchor.split(":")) != 3:
            raise ValueError("The anchor isn't in the form document:version:position.")
        text = self._text_at(anchor)
        ok = text is not None and quote_in(quote, text)
        rule = None if ok else ("unknown_anchor" if text is None else "quote_not_found")
        return {"anchor": anchor, "verified": ok, "rule": rule,
                "message": "The quote is in this passage." if ok else "The quote isn't in this passage.",
                "note": "The server also checks the quote against the question and other agents' output when it receives the result."}

    # ------------------------------------------------------------ knowledge

    def search_kb(self, args: dict) -> dict:
        requirement = args.get("purpose", "requirement") == "requirement"
        pool = [c for c in self.clauses if not requirement or c.legal_status == "in_force"]
        words = _words(str(args.get("query", "")))
        scored = sorted(((len(words & _words(c.text)), c) for c in pool), key=lambda s: (-s[0], s[1].anchor))
        hits = [{"anchor": c.anchor, "text": c.text, "score": round(min(1.0, 0.5 + 0.1 * s), 3), "document_id": c.document_id,
                 "title": c.title, "issuer": c.issuer, "jurisdiction": "example-country", "legal_status": c.legal_status,
                 "authority_level": c.authority_level, "article": c.article, "page": None}
                for s, c in scored if s >= 2][: int(args.get("top_k", 5))]
        tried = [] if hits else [{"search": "meaning and words, inside the filter", "candidates": len(pool), "best_score": None,
                                  "floor": 0.5, "result": "no chunk in the filter was similar enough" if pool else "nothing is in the filter"}]
        return {"no_source_found": not hits, "hits": hits, "searched": len(pool), "purpose": args.get("purpose", "requirement"),
                "searches_tried": tried}

    def read_clause(self, args: dict) -> dict:
        if args.get("anchor"):
            rows = [c.row() for c in self.clauses if c.anchor == args["anchor"]]
        elif args.get("document_id") and args.get("article"):
            rows = [c.row() for c in self.clauses
                    if c.document_id == args["document_id"] and str(args["article"]).casefold() in (c.article or "").casefold()]
        else:
            raise ValueError("Give an anchor, or a document_id with an article.")
        if not rows:
            raise ValueError("No such clause in this run's knowledge snapshot.")
        return {"clauses": rows, "more": False}

    def get_doc_status(self, args: dict) -> dict:
        rows = {c.version_id: c for c in self.clauses if c.document_id == args.get("document_id")}
        if not rows:
            raise ValueError(f"{args.get('document_id')} is not in this run's knowledge snapshot.")
        versions = [{"document_id": c.document_id, "document_version_id": v, "title": c.title, "issuer": c.issuer,
                     "country": "example-country", "doc_type": "guideline", "legal_status": c.legal_status,
                     "authority_level": c.authority_level, "review_status": "approved", "quality_status": "passed",
                     "published_on": "2025-01-01", "effective_on": "2025-03-01", "readable": True} for v, c in sorted(rows.items())]
        return {"document_id": args["document_id"], "versions": versions, "in_force": any(v["legal_status"] == "in_force" for v in versions)}

    def lookup_term(self, args: dict) -> dict:
        term = str(args.get("term", "")).casefold()
        rows = [{**c.row(), "chunk_type": c.chunk_type} for c in self.clauses if term and term in c.text.casefold()][:5]
        return {"term": args.get("term"), "no_source_found": not rows, "passages": rows}

    # ------------------------------------------------------------ the case

    def get_facts(self, args: dict) -> dict:
        keys = args.get("keys")
        facts = [{"key": f["key"], "status": f["status"], "value": f.get("value"), "evidence": f.get("evidence", [])}
                 for f in sorted(self.facts, key=lambda f: f["key"]) if not keys or f["key"] in keys]
        return {"facts": facts, "more": False}

    def get_site(self, args: dict) -> dict:
        sites = [f for f in self.get_facts({})["facts"] if "site" in f["key"]]
        return {"sites": sites, "applicant": self.applicant, "no_site_found": not any(f["status"] == "found" for f in sites)}

    def read_reference(self, args: dict) -> dict:
        rows = []
        for r in self.references:
            docs: dict[str, dict] = {}
            for p in sorted(self.reference_pages, key=lambda p: (p.doc_id, p.page)):
                if p.doc_id in (r.get("document_refs") or []):
                    doc = docs.setdefault(p.doc_id, {"doc_id": p.doc_id, "version": p.version, "pages": []})
                    doc["pages"].append({"page": p.page, "anchor": p.anchor, "text": p.text})
            rows.append({**r, "documents": list(docs.values())})
        return {"references": rows, "none_on_record": not rows, "pages_cut": False}

    def _finding(self, f: dict) -> dict:
        return {"result_id": f.get("result_id", f"r-{f['check_id']}"), "stage": f.get("stage", "s2"), "check_id": f["check_id"],
                "name": f.get("name", f["check_id"]), "question": f.get("question"), "status": f["status"], "blocking": f.get("blocking", False),
                "method": f.get("method", "agent"), "summary": f.get("summary"), "agent_status": f.get("agent_status"),
                "legal_force": f.get("legal_force", "guidance"), "evidence": f.get("evidence", []), "extracted": f.get("extracted"),
                "challenge": f.get("challenge")}

    def get_findings(self, args: dict) -> dict:
        status = args.get("status")
        return {"findings": [self._finding(f) for f in sorted(self.findings, key=lambda f: f["check_id"])
                             if not status or f["status"] in status], "more": False}

    def get_check(self, args: dict) -> dict:
        for f in self.findings:
            if f["check_id"] == args.get("check_id"):
                return {"result_id": self._finding(f)["result_id"], "stage": "s2", "check": self._finding(f)}
        raise ValueError(f"No check {args.get('check_id')} in this case.")

    def get_conflicts(self, args: dict) -> dict:
        return {"conflicts": list(self.conflicts), "more": False}

    def get_previous_run(self, args: dict) -> dict:
        return self.previous or {"previous_run": None, "previous_recommendation": None, "results": []}

    def compare_pack(self, args: dict) -> dict:
        return {"pinned": {"id": "pack-3", "version": 3}, "active": {"id": "pack-3", "version": 3}, "same": True,
                "added": [], "removed": [], "changed": []}

    def get_run_trace(self, args: dict) -> dict:
        check_id = str(args.get("check_id"))
        self.get_check(args)
        if check_id not in self.traces:
            return {"check_id": check_id, "saved": False, "trace": None}
        return {"check_id": check_id, "trace_id": f"trace-{check_id}", "saved": True, "trace": self.traces[check_id]}

    def draft_comment(self, args: dict) -> dict:
        if args.get("check_id"):
            self.get_check(args)
        return {"draft": {"type": "draft_comment", "check_id": args.get("check_id"), "text": args.get("text")}, "posted": False}

    # ------------------------------------------------------------ notices, licences, gaps

    def read_notice(self, args: dict) -> dict:
        if not args.get("notice_id"):
            return {"notices": [{**n.row(), **m} for n in self.notices for m in self.matched_notices.get(n.notice_id, [])], "more": False}
        rows = [n.row() for n in self.notices if n.notice_id == args["notice_id"]]
        if not rows:
            raise ValueError(f"No notice {args['notice_id']}.")
        return {"notices": rows, "matched_to_this_case": self.matched_notices.get(args["notice_id"], [])}

    def list_recent_notices(self, args: dict) -> dict:
        return {"notices": [{**n.row(text=False), "fetched_at": "2026-11-02T08:00:00"} for n in self.notices], "more": False}

    def get_relied_on(self, args: dict) -> dict:
        rows = [r for r in self.relied_on if args.get("approval_ref") in (None, r.get("approval_ref"))
                and args.get("regulator") in (None, r.get("regulator"))]
        return {"relied_on": rows, "more": False}

    def get_licence(self, args: dict) -> dict:
        for r in self.licences:
            if r["licence_id"] == args.get("licence_id"):
                return {"licence": r}
        raise ValueError(f"No licence {args.get('licence_id')}.")

    def get_gap_signals(self, args: dict) -> dict:
        kinds = args.get("kinds")
        return {"signals": [s for s in self.signals if not kinds or s["kind"] in kinds], "more": False}

    def get_pack_rule(self, args: dict) -> dict:
        if args.get("check_id"):
            for r in self.pack_rules:
                if r["check_id"] == args["check_id"]:
                    return {"pack_version_id": "pack-3", "version": r.get("version", 1), "rule": r["rule"]}
            raise ValueError(f"No check {args['check_id']} in this pack.")
        return {"checks": [{"pack_version_id": "pack-3", "version": r.get("version", 1), "check_id": r["check_id"],
                            "question": r["rule"].get("question"), "stage": r["rule"].get("stage"), "method": r["rule"].get("method")}
                           for r in self.pack_rules], "more": False}

    def compute(self, args: dict) -> dict:
        expression = str(args.get("expression", ""))
        return {"expression": expression, "value": _eval(ast.parse(expression, mode="eval"))}

    # ------------------------------------------------------------

    def tools(self, names: Iterable[str]) -> dict[str, Any]:
        """Handlers for these tool names, for FakeMcpServer."""
        return {name: getattr(self, name) for name in names}

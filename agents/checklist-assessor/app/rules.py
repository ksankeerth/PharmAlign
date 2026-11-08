"""What is specific to the Checklist Assessor: the first message, the instruction picked per run,
and the rules a result must keep before it leaves the agent."""
import json
from typing import Any

from pharmalign_agent_common import ToolLog, check_evidence
from pharmalign_agent_common.evidence import KB_TOOLS, evidence_in, fact_in_quotes, normalise, quote_in, unquoted_numbers
from pharmalign_contracts.agents.run_agents import AgentFinding, AssessmentRequest, AssessmentResult, TypedExtraction
from pharmalign_contracts.common import Evidence

MIN_WORDS = 8  # mirrors server/app/pipeline/citations.py
LONG_QUOTE_WORDS = 12
UNITS = frozenset({
    "c", "f", "k", "%", "mg", "g", "kg", "mcg", "ug", "μg", "ml", "l", "iu", "month", "months", "year", "years", "week", "weeks",
    "day", "days", "hour", "hours", "minute", "minutes", "ملغ", "مغ", "جم", "غ", "مل", "شهر", "شهرا", "اشهر", "سنه", "سنوات", "يوم", "ايام", "ساعه", "ساعات",
})
FILLER = frozenset({"a", "an", "the", "of", "in", "at", "for", "to", "and", "or", "is", "are", "was", "were", "في", "من", "على", "و"})


PLAN_WITHIN = {"agent": {"model_calls": 10, "tool_calls": 30}, "typed": {"model_calls": 6, "tool_calls": 12}}


def pick_variant(request: AssessmentRequest) -> str:
    """A typed check only extracts values. An agent check reads with its profile's instruction."""
    return "typed" if request.check.method == "typed" else request.profile


def law_sources(request: AssessmentRequest) -> list[dict]:
    """The only law this run's tools can read, and the only law it may cite: where a person moved the check, that law, else the
    check's source, then the supporting sources. The server sets the tools' reach the same way."""
    main = request.moved_source or request.check.source
    sources = ([main] if main else []) + list(request.check.supporting_sources)
    seen: set[tuple[str, str]] = set()
    out = []
    for s in sources:
        if (s.doc_id, s.clause) not in seen:
            seen.add((s.doc_id, s.clause))
            out.append({"doc_id": s.doc_id, "clause": s.clause})
    return out


def build_message(request: AssessmentRequest) -> str:
    """The run's input for the model. Tools supply all document and law text. The budget is included so the model can plan."""
    task = request.model_dump(
        mode="json",
        include={"language", "as_of", "budget", "case", "profile", "check", "facts", "reviewer_comment", "previous_rejection"},
    )
    # The budget is a hard limit. This is what a good run needs, and a run that wanders goes over it for nothing.
    task["plan_within"] = PLAN_WITHIN["typed" if request.check.method == "typed" else "agent"]
    if request.moved_source is not None:
        task["check"].pop("source", None)  # the old law is out of reach: only law_sources can be read
        task["law_moved_by_a_person"] = True
    task["law_sources"] = law_sources(request)
    return json.dumps(task, ensure_ascii=False)


def check_result(request: AssessmentRequest, result: AssessmentResult, log: ToolLog) -> None:
    """Mirrors the contract and the server's checks. Raises ValueError."""
    outcome = result.outcome
    if request.check.method == "typed" and not isinstance(outcome, TypedExtraction):
        raise ValueError("this is a typed check: answer with kind extraction, the values only and no verdict")
    if request.check.method == "agent" and not isinstance(outcome, AgentFinding):
        raise ValueError("this is an agent check: answer with kind finding")
    check_evidence(result, log)
    _check_quotes(result, log)
    if isinstance(outcome, TypedExtraction):
        _check_extraction(request, outcome, log)
    else:
        _check_finding(request, outcome, log)


# ---------------------------------------------------------------- quotes: what the server's citation check refuses

def _walk(value: Any, flag: dict[str, Any], key: str, inherited: Any = None) -> None:
    if isinstance(value, list):
        for item in value:
            _walk(item, flag, key, inherited)
    elif isinstance(value, dict):
        here = value.get(key, inherited)
        anchor = value.get("anchor") or value.get("text_anchor")
        if isinstance(anchor, str) and anchor and here is not None:
            flag[anchor] = here
        for v in value.values():
            if isinstance(v, dict | list):
                _walk(v, flag, key, here)


def _flags(log: ToolLog, tools: frozenset[str] | None, key: str) -> dict[str, Any]:
    """Anchor -> the value of `key` the tool gave for it (legal_status for law, suspect for a page), looking inside the whole answer."""
    found: dict[str, Any] = {}
    for c in log.calls:
        if not c.failed and (tools is None or c.tool in tools):
            _walk(c.payload, found, key)
    return found


def long_enough(quote: str) -> bool:
    """About eight words, or a number with its unit and what it measures."""
    tokens = normalise(quote).split()
    if len(tokens) >= MIN_WORDS:
        return True
    has_measure = any(t[-1].isdigit() and i + 1 < len(tokens) and tokens[i + 1] in UNITS for i, t in enumerate(tokens))
    return has_measure and any(t[-1].isalpha() and t not in UNITS and t not in FILLER for t in tokens)


def _document_key(anchor: str) -> str:
    return ":".join(anchor.split(":")[:2])


def _check_quotes(result: AssessmentResult, log: ToolLog) -> None:
    status = _flags(log, KB_TOOLS, "legal_status")
    suspect = _flags(log, None, "suspect")
    for e in evidence_in(result):
        if status.get(e.anchor, "in_force") != "in_force":
            raise ValueError(f"'{e.anchor}' is law that is {status[e.anchor].replace('_', ' ')}, not in force. Cite only in-force law")
        if suspect.get(e.anchor) is True:
            raise ValueError(f"'{e.anchor}' is a page with hidden text, so a quote from it can't stand alone. Use another page")
        if not long_enough(e.quote):
            raise ValueError(f"the quote '{e.quote[:60]}' is too short to show where it came from. Quote a full sentence (about eight words), or a number with its unit and what it measures")
        _check_repeated(e, log)


def _check_repeated(e: Evidence, log: ToolLog) -> None:
    """The server refuses a quote whose words also stand on other pages of the same file (once, if short; twice, if long)."""
    elsewhere = sum(
        1 for anchor, source in log.sources.items()
        if anchor != e.anchor and _document_key(anchor) == _document_key(e.anchor) and any(quote_in(e.quote, t) for t in source.texts)
    )
    if elsewhere >= 2 or (elsewhere == 1 and len(normalise(e.quote).split()) < LONG_QUOTE_WORDS):
        raise ValueError(f"the quote '{e.quote[:60]}' also stands on another page of the same file, so it doesn't point here. Quote a longer sentence that stands only once")


# ---------------------------------------------------------------- the two kinds of answer

def _check_extraction(request: AssessmentRequest, outcome: TypedExtraction, log: ToolLog) -> None:
    asked = [f.key for f in request.check.extract]
    keys = [f.key for f in outcome.extracted]
    if sorted(keys) != sorted(asked):
        raise ValueError(f"extract exactly these keys, each once: {', '.join(asked)}")
    for fact in outcome.extracted:
        if not fact_in_quotes(fact):
            raise ValueError(f"'{fact.key}': the value must appear in its own quote")
        if fact.status == "ambiguous" and len(fact.evidence) < 2:
            raise ValueError(f"'{fact.key}' is ambiguous: give a quote for each side")
        if fact.status == "not_found" and not log.looked():
            raise ValueError(f"'{fact.key}' is not_found, but nothing was searched or read. Search first")


def _check_finding(request: AssessmentRequest, f: AgentFinding, log: ToolLog) -> None:
    if f.status != "cannot_tell" and not f.evidence:
        raise ValueError(f"{f.status} needs evidence. With no evidence the status is cannot_tell")
    law = [e for e in f.evidence if log.from_tools(e.anchor, KB_TOOLS)]
    if f.status == "met" and not law:
        raise ValueError("met needs a quote from the law, returned by search_kb, read_clause or lookup_term")
    source = request.moved_source or request.check.source  # a person may have moved the check to other law
    if source is not None:
        allowed = {source.doc_id, *(s.doc_id for s in request.check.supporting_sources)}
        other = sorted({log.document_of(e.anchor) for e in law} - allowed)
        if other:
            raise ValueError(f"cite law only from {', '.join(sorted(allowed))}, the sources this check is set to, not {', '.join(other)}")
    if f.no_source is not None and not log.empty_searches(KB_TOOLS):
        raise ValueError("no_source needs a search_kb or lookup_term in this run that found nothing")
    quotes = [e.quote for e in f.evidence]
    # met: numbers only from quotes, like the server. Otherwise the check's own words may be repeated.
    also = [] if f.status == "met" else [request.check.question, request.check.id, *(k.value for k in request.facts)]
    missing = unquoted_numbers(f.summary, quotes, also)
    if missing:
        raise ValueError(
            f"the summary states {missing[0]:g}, which no quote you cite contains. Write the summary again without it: "
            "leave out article, clause and document numbers (the anchors name them), and state a number only if a quote you cite has it"
        )

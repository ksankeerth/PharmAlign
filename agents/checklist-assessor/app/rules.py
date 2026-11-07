"""What is specific to the Checklist Assessor: the first message, the instruction picked per run,
and the rules a result must keep before it leaves the agent."""
import json

from pharmalign_agent_common import ToolLog, check_evidence
from pharmalign_agent_common.evidence import KB_TOOLS, fact_in_quotes, unquoted_numbers
from pharmalign_contracts.agents.run_agents import AgentFinding, AssessmentRequest, AssessmentResult, TypedExtraction


def pick_variant(request: AssessmentRequest) -> str:
    """A typed check only extracts values. An agent check reads with its profile's instruction."""
    return "typed" if request.check.method == "typed" else request.profile


def build_message(request: AssessmentRequest) -> str:
    """The run's input for the model. Tools supply all document and law text."""
    task = request.model_dump(
        mode="json",
        include={"language", "as_of", "case", "profile", "check", "moved_source", "facts", "reviewer_comment", "previous_rejection"},
    )
    return json.dumps(task, ensure_ascii=False)


def check_result(request: AssessmentRequest, result: AssessmentResult, log: ToolLog) -> None:
    """Mirrors the contract and the server's checks. Raises ValueError."""
    outcome = result.outcome
    if request.check.method == "typed" and not isinstance(outcome, TypedExtraction):
        raise ValueError("this is a typed check: answer with kind extraction, the values only and no verdict")
    if request.check.method == "agent" and not isinstance(outcome, AgentFinding):
        raise ValueError("this is an agent check: answer with kind finding")
    check_evidence(result, log)
    if isinstance(outcome, TypedExtraction):
        _check_extraction(request, outcome, log)
    else:
        _check_finding(request, outcome, log)


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
        raise ValueError(f"the summary states {missing[0]:g}, which no quote you cite contains")

"""What is specific to the Gap Analyst: the first message and the rules a result must keep before it leaves."""
import json
import re

from pharmalign_agent_common import ToolLog, check_evidence
from pharmalign_agent_common.evidence import KB_TOOLS, normalise
from pharmalign_contracts.agents.run_agents import GapAnalysisRequest, GapAnalysisResult


MIN_QUOTE_WORDS = 8  # the server refuses a shorter quote
STATUS_WORDS = {"in_force": re.compile(r"\bin[ -]force\b", re.I)}


def plan_within(request: GapAnalysisRequest) -> dict:
    """What a good run needs: two steps of reads, then the answer, with a search or two per group of signals."""
    return {"model_calls": 4, "tool_calls": min(request.budget.max_tool_calls, 4 + 2 * len(request.signals))}


def build_message(request: GapAnalysisRequest) -> str:
    """The run's input for the model. The budget is included so the model can plan."""
    task = request.model_dump(
        mode="json", include={"language", "as_of", "budget", "signals", "pipeline_nodes", "readiness_dimensions", "previous_rejection"}
    )
    # The budget is a hard limit. This is what a good run needs, and a run that wanders goes over it for nothing.
    task["plan_within"] = plan_within(request)
    return json.dumps(task, ensure_ascii=False)


def signal_ids(log: ToolLog) -> set[str]:
    """Ids get_gap_signals returned in this run."""
    found: set[str] = set()
    for c in log.calls:
        if c.tool == "get_gap_signals" and not c.failed and isinstance(c.payload, dict):
            found |= {str(s.get("id")) for s in c.payload.get("signals", []) if isinstance(s, dict)}
    return found


def rule_ids(log: ToolLog) -> set[str]:
    """Check ids get_pack_rule returned in this run."""
    found: set[str] = set()
    for c in log.calls:
        if c.tool == "get_pack_rule" and not c.failed and isinstance(c.payload, dict):
            found |= {str(r.get("check_id")) for r in c.payload.get("checks", []) if isinstance(r, dict)}
            if c.args.get("check_id") and "rule" in c.payload:
                found.add(str(c.args["check_id"]))
    return found


def legal_status(log: ToolLog, anchor: str) -> str | None:
    """The legal status search_kb or read_clause gave for this anchor in this run, if they gave one."""
    for c in log.calls:
        if c.tool in KB_TOOLS and not c.failed and isinstance(c.payload, dict):
            for row in [*c.payload.get("hits", []), *c.payload.get("clauses", [])]:
                if isinstance(row, dict) and row.get("anchor") == anchor and row.get("legal_status"):
                    return str(row["legal_status"])
    return None


def looked_at_rules(log: ToolLog) -> bool:
    return any(c.tool == "get_pack_rule" and not c.failed for c in log.calls)


def check_result(request: GapAnalysisRequest, result: GapAnalysisResult, log: ToolLog) -> None:
    """Mirrors the contract and the server's checks. Raises ValueError."""
    check_evidence(result, log)
    returned = signal_ids(log)
    asked = {s.id for s in request.signals}
    rules = rule_ids(log)
    used: dict[str, int] = {}
    for n, cand in enumerate(result.candidates):
        if not returned:
            raise ValueError("no signals were read. Call get_gap_signals first and cite the ids it returns")
        invented = [s for s in cand.signal_ids if s not in returned]
        if invented:
            raise ValueError(f"signal '{invented[0]}' wasn't returned by get_gap_signals. Cite only ids it returned")
        unasked = [s for s in cand.signal_ids if s not in asked]
        if unasked:
            raise ValueError(f"signal '{unasked[0]}' isn't in the input: it is already covered. Cite only the input's signals")
        for s in cand.signal_ids:
            if used.get(s, n) != n:
                raise ValueError(f"signal '{s}' is in two candidates ('{result.candidates[used[s]].title}' and '{cand.title}'). Put each signal in one candidate")
            used[s] = n
        if cand.pack_rule is not None and cand.pack_rule not in rules:
            raise ValueError(f"'{cand.title}': pack_rule '{cand.pack_rule}' wasn't returned by get_pack_rule. Cite only ids it returned")
        if cand.pack_rule is None and not looked_at_rules(log):
            raise ValueError(f"'{cand.title}': say there is no pack rule only after looking. Call get_pack_rule first")
        for e in cand.global_examples:
            if not log.from_tools(e.anchor, KB_TOOLS):
                raise ValueError(f"global example '{e.anchor}' must come from search_kb or read_clause")
            status = legal_status(log, e.anchor)
            if status is not None and status != "in_force":
                raise ValueError(f"global example '{e.anchor}' is from a source that is {status}, not in force. The server drops it: "
                                 "cite only in-force sources, or leave it out")
            if len(normalise(e.quote).split()) < MIN_QUOTE_WORDS:
                raise ValueError(f"the quote at '{e.anchor}' is too short. Copy a whole sentence of at least {MIN_QUOTE_WORDS} words")
            if status in STATUS_WORDS and not STATUS_WORDS[status].search(cand.description):
                raise ValueError(f"'{cand.title}': the description must name each global example's legal status, "
                                 "such as \"a guideline of the issuer, in force\"")
        if cand.pipeline_node and request.pipeline_nodes and cand.pipeline_node not in request.pipeline_nodes:
            raise ValueError(f"pipeline_node '{cand.pipeline_node}' isn't in the list given")
        if cand.readiness_dimension and request.readiness_dimensions and cand.readiness_dimension not in request.readiness_dimensions:
            raise ValueError(f"readiness_dimension '{cand.readiness_dimension}' isn't in the list given")

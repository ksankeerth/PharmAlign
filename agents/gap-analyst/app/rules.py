"""What is specific to the Gap Analyst: the first message and the rules a result must keep before it leaves."""
import json

from pharmalign_agent_common import ToolLog, check_evidence
from pharmalign_agent_common.evidence import KB_TOOLS
from pharmalign_contracts.agents.run_agents import GapAnalysisRequest, GapAnalysisResult


def build_message(request: GapAnalysisRequest) -> str:
    """The run's input for the model."""
    task = request.model_dump(
        mode="json", include={"language", "as_of", "signals", "pipeline_nodes", "readiness_dimensions", "previous_rejection"}
    )
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


def looked_at_rules(log: ToolLog) -> bool:
    return any(c.tool == "get_pack_rule" and not c.failed for c in log.calls)


def check_result(request: GapAnalysisRequest, result: GapAnalysisResult, log: ToolLog) -> None:
    """Mirrors the contract and the server's checks. Raises ValueError."""
    check_evidence(result, log)
    returned = signal_ids(log)
    rules = rule_ids(log)
    for cand in result.candidates:
        if not returned:
            raise ValueError("no signals were read. Call get_gap_signals first and cite the ids it returns")
        invented = [s for s in cand.signal_ids if s not in returned]
        if invented:
            raise ValueError(f"signal '{invented[0]}' wasn't returned by get_gap_signals. Cite only ids it returned")
        if cand.pack_rule is not None and cand.pack_rule not in rules:
            raise ValueError(f"'{cand.title}': pack_rule '{cand.pack_rule}' wasn't returned by get_pack_rule. Cite only ids it returned")
        if cand.pack_rule is None and not looked_at_rules(log):
            raise ValueError(f"'{cand.title}': say there is no pack rule only after looking. Call get_pack_rule first")
        for e in cand.global_examples:
            if not log.from_tools(e.anchor, KB_TOOLS):
                raise ValueError(f"global example '{e.anchor}' must come from search_kb or read_clause")
        if cand.pipeline_node and request.pipeline_nodes and cand.pipeline_node not in request.pipeline_nodes:
            raise ValueError(f"pipeline_node '{cand.pipeline_node}' isn't in the list given")
        if cand.readiness_dimension and request.readiness_dimensions and cand.readiness_dimension not in request.readiness_dimensions:
            raise ValueError(f"readiness_dimension '{cand.readiness_dimension}' isn't in the list given")

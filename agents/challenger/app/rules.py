"""What is specific to the Challenger: the first message and the rules a result must keep."""
import json

from pharmalign_agent_common import ToolLog, check_evidence
from pharmalign_agent_common.evidence import KB_TOOLS, normalise, unquoted_numbers
from pharmalign_contracts.agents.run_agents import ChallengeRequest, ChallengeResult

NOT_VERIFIED = "No verified quote contradicts the claim, so it can't be disputed. "


def build_message(request: ChallengeRequest) -> str:
    """The run's input for the model: the claim and its evidence. The original reasoning is never sent."""
    task = request.model_dump(mode="json", include={"language", "as_of", "case", "target", "check_source", "previous_rejection"})
    return json.dumps(task, ensure_ascii=False)


def check_result(request: ChallengeRequest, result: ChallengeResult, log: ToolLog) -> ChallengeResult | None:
    """Mirrors the contract and the server's checks. Raises ValueError, or returns a result to keep instead."""
    check_evidence(result, log)
    source = request.check_source
    if source is not None:
        other = sorted({log.document_of(e.anchor) for e in result.evidence if log.from_tools(e.anchor, KB_TOOLS)} - {source.doc_id})
        if other:
            raise ValueError(f"cite law only from {source.doc_id}, the source this check is set to, not {', '.join(other)}")
    if result.verdict == "upheld" and not result.evidence:
        raise ValueError("upheld needs evidence from your own reading. With none, the verdict is cannot_verify")
    target = request.target
    also = [target.question, target.claim, *(e.quote for e in target.evidence)]
    missing = unquoted_numbers(result.reason, [e.quote for e in result.evidence], also)
    if missing:
        raise ValueError(f"the reason states {missing[0]:g}, which no quote you cite contains")
    if result.verdict == "disputed":
        claimed = {(e.anchor, normalise(e.quote)) for e in target.evidence}
        contradicting = [e for e in result.evidence if log.is_verified(e) and (e.anchor, normalise(e.quote)) not in claimed]
        if not contradicting:
            # Like the server: a dispute without a verified quote that differs from the claim's own stands as cannot_verify.
            return ChallengeResult(verdict="cannot_verify", reason=(NOT_VERIFIED + result.reason)[:2000], evidence=result.evidence)
    return None

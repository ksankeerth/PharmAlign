"""Output control: what an agent sent, judged before anything is stored. Pure, with no database or network.

An answer that was cut off or breaks its contract fails the run. It never reaches a finding.
"""
import json
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Union

from pharmalign_contracts.agents.run_agents import (
    RUN_AGENTS,
    AgentFinding,
    AssessmentResult,
    BriefResult,
    SamenessResult,
    TypedExtraction,
)
from pharmalign_contracts.common import RunResponse
from pydantic import BaseModel, ValidationError

from ..domain.status import RunState

AGENTS = {a.name: a for a in RUN_AGENTS}


@dataclass(frozen=True)
class Verdict:
    state: RunState
    accepted: bool = False
    code: Optional[str] = None

    @property
    def finished(self) -> bool:
        return self.state not in ("queued", "running")


def read_run(agent: str, body: Union[bytes, str, Mapping[str, Any]]) -> tuple[Optional[RunResponse], Optional[str]]:
    """The agent's contract model, or a code saying why the body was refused. Both agent clients read through this."""
    try:
        data = json.loads(body) if isinstance(body, (bytes, str)) else body
    except ValueError:
        return None, "body_not_json"
    try:
        return AGENTS[agent].run.model_validate(data), None
    except ValidationError:
        return None, "schema_invalid"


def review(run: RunResponse) -> Verdict:
    """Only a completed run with finish reason stop and a result can go any further."""
    if run.status in ("queued", "running"):
        return Verdict(run.status)
    if run.status == "completed":
        if run.finish_reason == "stop" and run.result is not None:
            return Verdict("completed", accepted=True)
        return Verdict("failed", code="not_finished_cleanly")
    if run.status == "cancelled":
        return Verdict("cancelled", code="cancelled")
    if run.status == "stopped_early":
        return Verdict("stopped_early", code=run.finish_reason)
    if run.finish_reason == "length":
        return Verdict("failed", code="truncated")
    return Verdict("failed", code=run.error.code if run.error else "failed")


def refused(code: str) -> Verdict:
    return Verdict("failed", code=code)


def _keys(items: list[dict]) -> set[str]:
    return {i["key"] for i in items}


def result_problem(agent: str, request: Mapping[str, Any], result: BaseModel) -> Optional[str]:
    """Output that fits its model but isn't what was asked for."""
    if isinstance(result, AssessmentResult):
        check = request["check"]
        outcome = result.outcome
        if check["method"] == "agent":
            return None if isinstance(outcome, AgentFinding) else "wrong_outcome_kind"
        if not isinstance(outcome, TypedExtraction):
            return "wrong_outcome_kind"
        return None if {f.key for f in outcome.extracted} == _keys(check["extract"]) else "wrong_values"
    if isinstance(result, SamenessResult):
        return None if {f.key for f in result.fields} == _keys(request["fields"]) else "wrong_values"
    if isinstance(result, BriefResult):
        if result.input_fingerprint != request["input_fingerprint"]:
            return "fingerprint_changed"
        if result.recommendation.choice not in request["recommendation_options"]:
            return "choice_not_offered"
        known = {r["check_id"] for r in request["results"]}
        if not set(result.recommendation.reason_evidence) <= known:
            return "reason_evidence_unknown"
    return None

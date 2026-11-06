"""What is specific to Reliance Watch: the first message and the rules a result must keep before it leaves."""
import json

from pharmalign_agent_common import ToolLog, check_evidence
from pharmalign_contracts.agents.run_agents import NoticeAssessmentRequest, NoticeAssessmentResult


def build_message(request: NoticeAssessmentRequest) -> str:
    """The run's input for the model. The notice text comes from the read_notice tool."""
    task = request.model_dump(
        mode="json",
        include={
            "language", "as_of", "trigger", "notice", "candidates", "change_types", "severity_options",
            "severity_floor", "open_case_checks", "previous_rejection",
        },
    )
    return json.dumps(task, ensure_ascii=False)


def check_result(request: NoticeAssessmentRequest, result: NoticeAssessmentResult, log: ToolLog) -> None:
    """Mirrors the contract and the server's checks. Raises ValueError."""
    if result.change_type not in request.change_types:
        raise ValueError(f"change_type '{result.change_type}' isn't offered. Use one of: {', '.join(request.change_types)}")
    options = request.severity_options
    if result.suggested_severity not in options:
        raise ValueError(f"suggested_severity '{result.suggested_severity}' isn't offered. Use one of: {', '.join(options)}")
    raised = options.index(result.suggested_severity) > options.index(request.severity_floor)
    if options.index(result.suggested_severity) < options.index(request.severity_floor):
        raise ValueError(f"suggested_severity can't be below the floor '{request.severity_floor}'. You may only raise it")
    check_evidence(result, log)

    if not result.candidates:
        raise ValueError("candidates is empty. Report a notice you can't match as one candidate with target_type none")
    given = {(c.target_type, c.target_id) for c in result.candidates}
    for c in request.candidates:
        if (c.target_type, c.target_id) not in given:
            raise ValueError(f"candidate {c.target_type} {c.target_id or ''} was dropped. Keep every candidate, even when it doesn't apply")
    listed = {(c.target_type, c.target_id) for c in request.candidates}
    seen = set(log.texts())
    for c in result.candidates:
        if (c.target_type, c.target_id) in listed or c.target_type == "none":
            continue
        if c.target_id not in seen:
            raise ValueError(f"target '{c.target_id}' wasn't returned by get_relied_on or get_licence. Use only ids a tool returned")

    unknown = [k for k in result.additional_checks if k not in request.open_case_checks]
    if unknown:
        raise ValueError(f"additional_checks '{unknown[0]}' isn't in open_case_checks. Add only checks the case has open")

    cites_notice = any(log.from_tools(e.anchor, {"read_notice"}) for e in result.evidence)
    if any(c.applies == "yes" for c in result.candidates) and not cites_notice:
        raise ValueError("a match needs a quote from the notice text, returned by read_notice")
    if raised and not cites_notice:
        raise ValueError("raising the severity above the floor needs a quote from the notice text")

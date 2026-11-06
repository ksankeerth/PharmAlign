"""The single mapping from what a run produced to what a check shows. Handlers never set a result status any other way."""
from typing import Literal, Optional

from pharmalign_contracts.agents.run_agents import CheckStatus
from pharmalign_contracts.server.models import CheckDetail

RunState = Literal["queued", "running", "completed", "stopped_early", "failed", "cancelled", "lost"]
Finding = Literal["met", "attention", "missing", "cannot_tell"]
Method = Literal["code", "typed", "agent", "person"]

NOT_A_VERDICT: frozenset[str] = frozenset({"stopped_early", "failed", "cancelled", "lost"})
UNFINISHED: frozenset[str] = frozenset({"queued", "running"})


def infrastructure_cause(
    run_status: Optional[RunState] = None,
    tool_errors: int = 0,
    reader_failed: bool = False,
    rejected_twice: bool = False,
) -> bool:
    """True when the system failed, so nothing here can count against the company."""
    return run_status in NOT_A_VERDICT or tool_errors > 0 or reader_failed or rejected_twice


def check_status(
    method: Method,
    run_status: Optional[RunState],
    may_pass_alone: bool = False,
    finding: Optional[Finding] = None,
    rule_passed: Optional[bool] = None,
    no_source_found: bool = False,
    tool_errors: int = 0,
    reader_failed: bool = False,
    rejected_twice: bool = False,
    disputed: bool = False,
    evidence_changed: bool = False,
    on_fail: str = "hold_for_officer",
) -> CheckStatus:
    """The status a check shows. The order is the rule: an earlier line beats a later one."""
    if evidence_changed:
        return "stale"
    if method == "person":
        return "person_confirms"
    if run_status is None or run_status == "queued":
        return "pending"
    if run_status == "running":
        return "running"
    if infrastructure_cause(run_status, tool_errors, reader_failed, rejected_twice):
        return "error"
    if no_source_found:
        return "person_confirms"
    if method == "agent":
        return _agent_status(finding, may_pass_alone, disputed)
    return _rule_status(method, rule_passed, may_pass_alone, disputed, on_fail)


def _agent_status(finding: Optional[Finding], may_pass_alone: bool, disputed: bool) -> CheckStatus:
    if finding == "met":
        return "pass" if may_pass_alone and not disputed else "person_confirms"
    if finding == "attention":
        return "look"
    if finding == "missing":
        return "held"
    if finding == "cannot_tell":
        return "person_confirms"
    return "error"  # a finished run with no finding has told us nothing


def _rule_status(method: Method, rule_passed: Optional[bool], may_pass_alone: bool, disputed: bool, on_fail: str) -> CheckStatus:
    if rule_passed is None:
        return "error"
    if not rule_passed:
        return "look" if on_fail == "look" else "held"
    if disputed:
        return "person_confirms"
    return "pass" if method == "code" or may_pass_alone else "person_confirms"


def not_run_status() -> CheckStatus:
    """A later check of a case held at an early gate. It waits for an officer."""
    return "skipped"


def is_clear(c: CheckDetail) -> bool:
    """Clear means Pass, Confirmed, or Waived with a waiver that is still valid."""
    if c.status == "pass":
        return True
    if c.status == "confirmed":
        return c.confirmation.valid if c.confirmation else True
    if c.status == "waived":
        return bool(c.waiver and c.waiver.valid)
    return False


def is_clear_on_current_evidence(c: CheckDetail) -> bool:
    """Stronger than is_clear: a person's act must be on record, and on the evidence the check holds now."""
    if c.status == "pass":
        return c.method != "agent" or c.may_pass_alone
    if c.status == "confirmed":
        return bool(c.confirmation and c.confirmation.valid and c.confirmation.fingerprint == c.fingerprint)
    if c.status == "waived":
        return bool(c.waiver and c.waiver.valid and c.waiver.fingerprint == c.fingerprint)
    return False

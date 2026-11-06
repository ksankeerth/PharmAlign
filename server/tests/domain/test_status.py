"""The single status mapping, as a table."""
from itertools import product

import pytest

from app.domain.status import check_status, is_clear, is_clear_on_current_evidence

from .builders import FP_B, check, confirmation, waiver

AGENT = dict(method="agent", run_status="completed")
TYPED = dict(method="typed", run_status="completed")
CODE = dict(method="code", run_status="completed")

TABLE = [
    # not finished
    ("no run yet", dict(method="agent", run_status=None), "pending"),
    ("queued", dict(method="agent", run_status="queued"), "pending"),
    ("running", dict(method="agent", run_status="running"), "running"),
    # the system failed: never Held
    ("run failed", dict(method="agent", run_status="failed"), "error"),
    ("run lost", dict(method="agent", run_status="lost"), "error"),
    ("run cancelled", dict(method="agent", run_status="cancelled"), "error"),
    ("stopped early with a partial result", dict(method="agent", run_status="stopped_early", finding="met", may_pass_alone=True), "error"),
    ("tool errors hide a missing", {**AGENT, "finding": "missing", "tool_errors": 1}, "error"),
    ("tool errors hide a met", {**AGENT, "finding": "met", "may_pass_alone": True, "tool_errors": 2}, "error"),
    ("tool errors hide no source", {**AGENT, "finding": "cannot_tell", "no_source_found": True, "tool_errors": 1}, "error"),
    ("reader failed", {**AGENT, "finding": "missing", "reader_failed": True}, "error"),
    ("rejected twice", {**AGENT, "finding": "missing", "rejected_twice": True}, "error"),
    ("typed run with tool errors", {**TYPED, "rule_passed": False, "tool_errors": 1}, "error"),
    ("code rule never ran", {**CODE, "rule_passed": None}, "error"),
    ("finished with no finding", {**AGENT, "finding": None}, "error"),
    # agent findings
    ("met, may pass alone", {**AGENT, "finding": "met", "may_pass_alone": True}, "pass"),
    ("met, may not pass alone", {**AGENT, "finding": "met", "may_pass_alone": False}, "person_confirms"),
    ("met, disputed by the challenger", {**AGENT, "finding": "met", "may_pass_alone": True, "disputed": True}, "person_confirms"),
    ("attention", {**AGENT, "finding": "attention"}, "look"),
    ("missing", {**AGENT, "finding": "missing"}, "held"),
    ("cannot tell", {**AGENT, "finding": "cannot_tell"}, "person_confirms"),
    ("no source found", {**AGENT, "finding": "cannot_tell", "no_source_found": True}, "person_confirms"),
    ("no source beats missing", {**AGENT, "finding": "missing", "no_source_found": True}, "person_confirms"),
    # rules in code
    ("code rule passed", {**CODE, "rule_passed": True}, "pass"),
    ("code rule failed", {**CODE, "rule_passed": False}, "held"),
    ("code rule failed, pack says look", {**CODE, "rule_passed": False, "on_fail": "look"}, "look"),
    ("typed rule passed, may pass alone", {**TYPED, "rule_passed": True, "may_pass_alone": True}, "pass"),
    ("typed rule passed, may not pass alone", {**TYPED, "rule_passed": True}, "person_confirms"),
    ("typed rule passed, disputed", {**TYPED, "rule_passed": True, "may_pass_alone": True, "disputed": True}, "person_confirms"),
    ("typed rule failed", {**TYPED, "rule_passed": False}, "held"),
    # people
    ("person check", dict(method="person", run_status=None), "person_confirms"),
    # evidence changed beats all of it
    ("evidence changed after a pass", {**CODE, "rule_passed": True, "evidence_changed": True}, "stale"),
    ("evidence changed while running", dict(method="agent", run_status="running", evidence_changed=True), "stale"),
    ("evidence changed after a failed run", dict(method="agent", run_status="failed", evidence_changed=True), "stale"),
    ("evidence changed on a person check", dict(method="person", run_status=None, evidence_changed=True), "stale"),
]


@pytest.mark.parametrize("inputs, expected", [(t[1], t[2]) for t in TABLE], ids=[t[0] for t in TABLE])
def test_status_table(inputs, expected):
    assert check_status(**inputs) == expected


def every_agent_input():
    runs = [None, "queued", "running", "completed", "stopped_early", "failed", "cancelled", "lost"]
    findings = [None, "met", "attention", "missing", "cannot_tell"]
    for run, finding, alone, no_source, tools, reader, rejected, disputed in product(
        runs, findings, [True, False], [True, False], [0, 1], [True, False], [True, False], [True, False]
    ):
        yield dict(method="agent", run_status=run, finding=finding, may_pass_alone=alone, no_source_found=no_source, tool_errors=tools, reader_failed=reader, rejected_twice=rejected, disputed=disputed)


def test_a_system_failure_is_never_held_across_every_combination():
    for inputs in every_agent_input():
        failed = inputs["run_status"] in ("failed", "lost", "cancelled", "stopped_early")
        finished = inputs["run_status"] == "completed"
        if failed or (finished and (inputs["tool_errors"] or inputs["reader_failed"] or inputs["rejected_twice"])):
            assert check_status(**inputs) == "error", inputs


def test_an_agent_alone_never_passes_a_check_it_may_not_pass_across_every_combination():
    for inputs in every_agent_input():
        if not inputs["may_pass_alone"]:
            assert check_status(**inputs) != "pass", inputs


def test_no_source_found_never_passes_or_holds_across_every_combination():
    for inputs in every_agent_input():
        if inputs["no_source_found"]:
            assert check_status(**inputs) not in ("pass", "held"), inputs


CLEAR_TABLE = [
    ("pass", {}, True, True),
    ("confirmed", {"confirmation": confirmation()}, True, True),
    ("confirmed", {"confirmation": confirmation(valid=False)}, False, False),
    ("confirmed", {"confirmation": confirmation(fingerprint=FP_B)}, True, False),
    ("confirmed", {}, True, False),
    ("waived", {"waiver": waiver()}, True, True),
    ("waived", {"waiver": waiver(valid=False)}, False, False),
    ("waived", {"waiver": waiver(fingerprint=FP_B)}, True, False),
    ("waived", {}, False, False),
    *[(s, {}, False, False) for s in ("pending", "running", "look", "person_confirms", "held", "error", "stale", "skipped")],
]


@pytest.mark.parametrize("status, over, clear, bound", CLEAR_TABLE)
def test_what_counts_as_clear(status, over, clear, bound):
    c = check(status=status, **over)
    assert is_clear(c) is clear
    assert is_clear_on_current_evidence(c) is bound


def test_an_agent_pass_it_was_not_allowed_is_not_clear_on_current_evidence():
    assert is_clear_on_current_evidence(check(status="pass", method="agent", may_pass_alone=False)) is False

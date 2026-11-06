"""One test per rule, each with the situation that broke the flow without it."""
from dataclasses import replace

import pytest
from pharmalign_contracts.server.models import AgentRunView

from app.domain import rules as R
from app.domain.fingerprints import CaseFacts, case_input_fingerprint
from app.domain.guards import SigningInput
from app.domain.status import check_status

from .builders import (
    CASE,
    FP_A,
    FP_B,
    HOUR,
    NOW,
    SIGNER,
    case,
    check,
    confirmation,
    decision,
    match,
    package,
    proposal,
    question,
    ready_case,
    source,
    thread,
    waiver,
    with_brief,
)


def failed(guards) -> list[str]:
    return [g.guard for g in guards if not g.passed]


def signing(facts, signer=SIGNER, fingerprint=None, version=1) -> SigningInput:
    return SigningInput(signer, fingerprint if fingerprint is not None else package(facts), version)


# ---------------------------------------------------------------- promote guard


def test_promote_passes_a_clean_file():
    assert failed(R.promote_guard(ready_case(status="in_review"))) == []


@pytest.mark.parametrize("status", ["error", "look", "person_confirms", "running", "pending", "stale", "held", "skipped"])
def test_promote_refuses_a_blocking_check_that_is_not_clear(status):
    """A tool outage left a blocking check on Error, and "Error isn't the company's fault" nearly let it through."""
    facts = ready_case(check(status=status), status="in_review")
    assert "blocking_checks_clear" in failed(R.promote_guard(facts))


def test_promote_refuses_a_recommendation_older_than_a_confirmation():
    """A person confirmed a check after the brief was written, so the brief no longer described the file."""
    facts = ready_case(status="in_review")
    changed = replace(facts, checks=(check(status="confirmed", confirmation=confirmation()),))
    assert failed(R.promote_guard(changed)) == ["current_recommendation"]


def test_promote_refuses_a_waiver_given_on_other_evidence():
    """The waiver's own flag still said valid, but it was given on an earlier dossier."""
    facts = with_brief(ready_case(check(status="waived", waiver=waiver(fingerprint=FP_B)), status="in_review"))
    assert failed(R.promote_guard(facts)) == ["clear_on_current_evidence"]


def test_promote_refuses_confirmed_with_no_confirmation_on_record():
    """A status with no person behind it is not a person's act."""
    facts = ready_case(check(status="confirmed"), status="in_review")
    assert failed(R.promote_guard(facts)) == ["clear_on_current_evidence"]


def test_promote_refuses_an_agent_pass_it_was_not_allowed_to_give():
    facts = ready_case(check(status="pass", may_pass_alone=False), status="in_review")
    assert failed(R.promote_guard(facts)) == ["clear_on_current_evidence"]


def test_promote_refuses_a_case_with_no_check_results():
    """A bad query returned no rows, and "every blocking check is clear" was true of nothing."""
    facts = with_brief(CaseFacts(case_id=CASE, case=case("in_review")))
    assert "check_results_present" in failed(R.promote_guard(facts))


def test_promote_refuses_any_open_comment_even_one_marked_not_blocking():
    facts = ready_case(status="in_review", threads=(thread(blocks=False),))
    assert failed(R.promote_guard(facts)) == ["no_open_comments"]


# ---------------------------------------------------------------- sign guard


def test_sign_passes_a_clean_file():
    facts = ready_case()
    assert failed(R.sign_guard(facts, signing(facts))) == []


def test_sign_refuses_a_check_that_went_held_after_promotion():
    """A suspension arrived after promotion and turned the foreign check Held while the file waited days for signature."""
    facts = with_brief(ready_case(check(status="held")))
    assert "blocking_checks_clear" in failed(R.sign_guard(facts, signing(facts)))


@pytest.mark.parametrize("status", ["error", "running", "person_confirms", "look", "stale"])
def test_sign_refuses_an_unresolved_blocking_check(status):
    facts = with_brief(ready_case(check(status=status)))
    assert "blocking_checks_clear" in failed(R.sign_guard(facts, signing(facts)))


def test_sign_refuses_a_comment_opened_after_promotion():
    """A reviewer raised a concern after promotion. Promotion's guard had already run, and nothing looked again."""
    facts = ready_case(threads=(thread(),))
    assert "no_open_threads" in failed(R.sign_guard(facts, signing(facts)))


def test_sign_refuses_a_waiver_on_other_evidence():
    facts = with_brief(ready_case(check(status="waived", waiver=waiver(fingerprint=FP_B))))
    assert "clear_on_current_evidence" in failed(R.sign_guard(facts, signing(facts)))


def test_signer_separation_still_applies_on_top():
    facts = with_brief(ready_case(check(status="waived", waiver=waiver(by=SIGNER))))
    assert "signer_did_not_assess" in failed(R.sign_guard(facts, signing(facts)))


# ---------------------------------------------------------------- sign compare-and-swap


def test_sign_compare_and_swap_refuses_when_a_new_recommendation_arrived():
    """The Decision Maker opened version 1, version 2 arrived, and they signed believing they had read the latest."""
    seen = ready_case()
    seen_fingerprint = package(seen)
    now = with_brief(seen, version=2)
    assert failed(R.sign_compare_and_swap(now, seen_fingerprint, 1)) == ["fingerprint_matches", "signed_version_is_latest"]


def test_sign_compare_and_swap_refuses_a_signature_naming_no_version():
    """The mock treats the version as optional. A signature that doesn't say what it signed can't prove anything."""
    facts = ready_case()
    assert failed(R.sign_compare_and_swap(facts, package(facts), None)) == ["signed_version_is_latest"]


def test_sign_compare_and_swap_refuses_no_fingerprint_and_passes_a_match():
    facts = ready_case()
    assert "fingerprint_matches" in failed(R.sign_compare_and_swap(facts, None, 1))
    assert failed(R.sign_compare_and_swap(facts, package(facts), 1)) == []


def test_sign_grant_guards_combines_all_of_them_once():
    facts = ready_case()
    assert failed(R.sign_grant_guards(facts, signing(facts), [source()], NOW)) == []
    both = R.sign_grant_guards(facts, signing(facts, fingerprint=FP_B), [source()], NOW)
    assert failed(both).count("fingerprint_matches") == 1


# ---------------------------------------------------------------- result fencing, apply once


def test_result_fencing_ignores_a_run_from_the_old_sequence():
    """A run started before the company's reply finished after it, and put an old-dossier answer on the check."""
    assert R.newest_run_only(1, 1, check(generation=2, attempt=1)) is False


def test_result_fencing_ignores_an_older_attempt_and_one_never_issued():
    assert R.newest_run_only(2, 1, check(generation=2, attempt=2)) is False
    assert R.newest_run_only(2, 3, check(generation=2, attempt=2)) is False
    assert R.newest_run_only(2, 2, check(generation=2, attempt=2)) is True


def test_a_late_failure_from_an_old_run_cant_put_error_on_a_good_check():
    waiting = check(status="running", generation=2)
    assert R.decide_result("run-old", 1, 1, waiting, "in_review", []) == "ignore_old_run"


def test_apply_once_ignores_a_second_delivery():
    assert R.apply_once("run-1", []) is True
    assert R.apply_once("run-1", ["run-1"]) is False


def test_a_duplicate_result_doesnt_wipe_a_persons_confirmation():
    """After a restart the same result came in again, the check fell back to Person confirms, and the confirmation was gone."""
    confirmed = check(status="confirmed", confirmation=confirmation())
    assert R.decide_result("run-1", 1, 1, confirmed, "in_review", ["run-1"]) == "ignore_duplicate"
    assert R.decide_result("run-2", 1, 1, confirmed, "in_review", []) == "ignore_not_waiting"


def test_a_good_result_for_a_waiting_check_is_applied():
    for status in ("running", "pending", "stale"):
        assert R.decide_result("run-1", 1, 1, check(status=status), "in_review", []) == "apply"


# ---------------------------------------------------------------- coordinator fence


def test_coordinator_fence_discards_a_brief_on_old_results():
    """A check went Held while the brief was being written. The stale brief arrived last, got the highest number, and offered proceed."""
    assert R.coordinator_fence(FP_A, FP_B, "in_review") == "discard_and_rebrief"


def test_coordinator_fence_accepts_a_current_brief_and_discards_any_after_signing():
    assert R.coordinator_fence(FP_A, FP_A, "in_review") == "accept"
    assert R.coordinator_fence(FP_A, FP_A, "recording") == "discard_frozen"


# ---------------------------------------------------------------- reconcile, lost runs


def run_row(run_id: str, status: str, is_current: bool = True) -> AgentRunView:
    return AgentRunView(run_id=run_id, agent="checklist-assessor", status=status, is_current=is_current)


def test_reconcile_reattaches_to_every_unfinished_run_after_a_restart():
    """The server restarted just after starting a run. Polling lived in memory, so the check waited for ever."""
    rows = [run_row("a", "running"), run_row("b", "queued"), run_row("c", "running", is_current=False), run_row("d", "completed"), run_row("e", "failed"), run_row("f", "lost")]
    assert [a.run_id for a in R.reconcile_on_boot(rows)] == ["a", "b", "c"]


def test_lost_run_404_sends_the_same_id_again_rather_than_failing():
    """An agent restart forgot an in-memory run. A 404 means forgotten, not failed; calling it failed left the case stuck."""
    assert R.recover_lost_run(404, "running", reposts=0) == "repost_same_id"


def test_lost_run_gives_up_after_repeated_404s_and_shows_error():
    assert R.recover_lost_run(404, "running", reposts=3) == "mark_lost"
    assert check_status("agent", "lost") == "error"


def test_lost_run_keeps_polling_a_healthy_run_and_stops_at_its_deadline():
    assert R.recover_lost_run(200, "running", reposts=0) == "keep_polling"
    assert R.recover_lost_run(500, "running", reposts=0) == "keep_polling"
    assert R.recover_lost_run(200, "running", reposts=0, past_deadline=True) == "mark_lost"


def test_lost_run_404_on_a_finished_run_does_nothing():
    assert R.recover_lost_run(404, "completed", reposts=0) == "nothing"


def test_a_failed_coordinator_restarts_then_reaches_a_person():
    """A brief failed. It has no check to show Error on and no brief means no promotion, so the case just stopped."""
    assert R.restart_failed_coordinator("in_review", restarts=0) == "start_new_run"
    assert R.restart_failed_coordinator("in_review", restarts=3) == "tell_person"
    assert R.restart_failed_coordinator("closed", restarts=0) == "nothing"


def test_a_recheck_gets_its_own_run_id():
    """Reusing the id returned the old run or a 409, so a person's re-check started nothing."""
    first = R.run_id_for("CASE-1", 1, "M-1", 1, 1)
    again = R.run_id_for("CASE-1", 1, "M-1", 1, 2)
    assert first != again
    assert AgentRunView(run_id=again, agent="checklist-assessor", status="queued").run_id == "case-1-s1-m-1-g1-a2"


# ---------------------------------------------------------------- waiver


def test_waiver_is_voided_when_the_evidence_changes():
    """An officer waived a check, then the company replied. The old waiver carried a grant on a dossier nobody had waived."""
    waived = check(status="waived", waiver=waiver())
    assert R.waiver_is_bound(waived) is True
    change = R.void_on_evidence_change(waived, FP_B, rerun_pending=False)
    assert (change.waiver_valid, change.status) == (False, "held")
    assert R.waiver_is_bound(check(status="waived", waiver=waiver(), fingerprint=FP_B)) is False


def test_waiver_void_shows_stale_while_a_rerun_is_pending():
    change = R.void_on_evidence_change(check(status="waived", waiver=waiver()), FP_B, rerun_pending=True)
    assert (change.waiver_valid, change.status) == (False, "stale")


def test_waiver_stands_when_the_evidence_is_the_same():
    change = R.void_on_evidence_change(check(status="waived", waiver=waiver()), FP_A, rerun_pending=False)
    assert (change.waiver_valid, change.status) == (True, "waived")


def test_a_confirmation_is_voided_the_same_way():
    change = R.void_on_evidence_change(check(status="confirmed", confirmation=confirmation()), FP_B, rerun_pending=False)
    assert (change.confirmation_valid, change.status) == (False, "person_confirms")


# ---------------------------------------------------------------- dependency closure


def test_closure_follows_facts_as_well_as_documents():
    """A reply replaced a certificate. The check comparing a fact from it read no document, so its old answer stood."""
    deps = [
        R.CheckDependencies("EXTRACT", reads=["certificate"], extracts_facts=["strength"]),
        R.CheckDependencies("COMPARE", reads=["label"], reads_facts=["strength"]),
        R.CheckDependencies("OTHER", reads=["label"]),
    ]
    assert R.rerun_closure(["certificate"], deps) == ["COMPARE", "EXTRACT"]


def test_closure_follows_facts_at_any_depth_in_any_order():
    deps = [
        R.CheckDependencies("C", reads_facts=["b"]),
        R.CheckDependencies("B", reads_facts=["a"], extracts_facts=["b"]),
        R.CheckDependencies("A", reads=["doc"], extracts_facts=["a"]),
    ]
    assert R.rerun_closure(["doc"], deps) == ["A", "B", "C"]


def test_closure_includes_a_check_that_declares_nothing():
    assert R.rerun_closure(["doc"], [R.CheckDependencies("UNDECLARED"), R.CheckDependencies("OTHER", reads=["other"])]) == ["UNDECLARED"]


# ---------------------------------------------------------------- notice gate


def test_notice_gate_tells_a_person_and_reruns_whatever_the_agent_does():
    """The agent that explains a notice failed before anyone was notified, and the notice was already marked as seen."""
    gate = R.notice_gate(case("in_review"), None, ["cr-foreign"])
    assert gate.tell_people == ("p-officer",)
    assert gate.rerun_results == ("cr-foreign",)
    assert gate.scope == "open_case"


def test_notice_gate_agent_can_add_but_never_narrow():
    """A model left the foreign check off its re-run list. Routing that decides safety can't belong to a model."""
    gate = R.notice_gate(case("in_review"), None, ["cr-foreign"], agent_rerun_results=["cr-other"])
    assert gate.rerun_results == ("cr-foreign", "cr-other")
    assert R.notice_gate(case("in_review"), None, ["cr-foreign"], agent_rerun_results=[]).rerun_results == ("cr-foreign",)


def test_notice_gate_counts_a_signed_but_unrecorded_case_as_a_match():
    """The notice came in after signature and before the record write. The case wasn't open and no licence existed, so nothing matched."""
    gate = R.notice_gate(case("recording"), decision(record_status="queued"), ["cr-foreign"])
    assert gate.scope == "signed_unrecorded"
    assert gate.hold_decision is True
    assert gate.tell_people == (SIGNER,)
    assert gate.rerun_results == ()


def test_notice_gate_still_tells_someone_when_the_case_has_no_assignee():
    gate = R.notice_gate(case("in_review", assigned_to=None), None, ["cr-foreign"])
    assert gate.tell_people == () and gate.tell_role == "case_officer"


def test_notice_gate_after_the_record_is_written_goes_to_the_decision_maker_without_a_hold():
    gate = R.notice_gate(case("closed"), decision(record_status="acknowledged"), [])
    assert (gate.scope, gate.hold_decision, gate.tell_role) == ("recorded", False, "decision_maker")


def test_notice_gate_on_an_ended_case_does_nothing():
    gate = R.notice_gate(case("closed"), decision("close", record_status="not_needed"), [])
    assert (gate.scope, gate.tell_people, gate.tell_role) == ("ended", (), None)


# ---------------------------------------------------------------- fetch at sign


def test_fetch_at_sign_refuses_a_source_not_read_since_signing_began():
    """A suspension was published between two scheduled fetches, and the grant was signed in that gap."""
    guard = R.fetch_at_sign_guard([source(last_success_at=NOW - HOUR)], NOW)
    assert guard.passed is False
    assert "last read" in guard.detail


def test_fetch_at_sign_that_finds_a_notice_stops_the_signature():
    """The forced fetch matched. The new match changes the fingerprint the signer sent."""
    before = ready_case()
    seen = package(before)
    after = replace(before, matches=(match(),))
    assert set(failed(R.sign_grant_guards(after, signing(after, fingerprint=seen), [source()], NOW))) == {"fingerprint_matches", "foreign_status_current"}


@pytest.mark.parametrize("over", [{"last_success_at": None}, {"health": "failing"}, {"health": "layout_changed"}, {"health": "zero_yield"}])
def test_fetch_at_sign_fails_closed_when_a_source_cant_be_read(over):
    assert R.fetch_at_sign_guard([source(**over)], NOW).passed is False


def test_fetch_at_sign_fails_closed_with_no_sources():
    assert R.fetch_at_sign_guard([], NOW).passed is False


def test_fetch_at_sign_passes_a_fresh_read_and_doesnt_chase_a_manual_source():
    manual = source(last_success_at=None, health="manual", kind="manual")
    assert R.fetch_at_sign_guard([source()], NOW).passed is True
    assert R.fetch_at_sign_guard([source(), manual], NOW).passed is True
    assert R.sources_to_fetch([source(), manual]) == ["src-1"]


# ---------------------------------------------------------------- superseded law

LAW = {"doc_id": "law-old"}


def test_law_superseded_turns_citing_findings_stale_and_tells_the_officer():
    """A law cited by a finding was repealed. Only library staff were told, and the grant was signed on repealed law."""
    citing = check("A", source=LAW, status="confirmed", confirmation=confirmation())
    other = check("B", source={"doc_id": "law-other"})
    change = R.law_superseded(CaseFacts(case_id=CASE, case=case("in_review"), checks=(citing, other)), "law-old")
    assert change.stale_results == (citing.id,)
    assert change.tell_person == "p-officer"


def test_law_superseded_finds_citations_in_evidence_too():
    cited = check("A", source=None, evidence=[{"anchor": "law-old:v1:art-7", "quote": "A quote."}])
    change = R.law_superseded(CaseFacts(case_id=CASE, case=case("in_review"), checks=(cited,)), "law-old")
    assert change.stale_results == (cited.id,)


def test_law_superseded_leaves_a_signed_case_alone():
    facts = CaseFacts(case_id=CASE, case=case("recording"), checks=(check(source=LAW),))
    assert R.law_superseded(facts, "law-old").stale_results == ()


def test_law_rerun_waits_for_a_person_to_set_the_transition():
    facts = CaseFacts(case_id=CASE, case=case("in_review"))
    assert failed(R.law_rerun_guards(facts, "law-old", None)) == ["transition_set_by_person"]
    by_agent = R.TransitionRule(CASE, "law-old", "law-new", "checklist-assessor", "agent")
    assert failed(R.law_rerun_guards(facts, "law-old", by_agent)) == ["transition_set_by_person"]
    by_person = R.TransitionRule(CASE, "law-old", "law-new", "p-officer", "person")
    assert failed(R.law_rerun_guards(facts, "law-old", by_person)) == []


def test_an_agent_cant_switch_law_silently():
    """A tool refused the repealed law and offered alternatives, so the agent answered from a different law with nobody deciding."""
    pinned = check(source=LAW)
    assert R.agent_law_allowed(pinned, ["law-old"]) is True
    assert R.agent_law_allowed(pinned, ["law-new"]) is False
    by_agent = R.TransitionRule(CASE, "law-old", "law-new", "checklist-assessor", "agent")
    assert R.agent_law_allowed(pinned, ["law-new"], by_agent) is False


def test_the_law_an_agent_is_given_is_the_one_the_server_allows():
    """The request carries the same document the output check will accept."""
    assert R.law_doc_for("law-old") == "law-old"
    assert R.law_doc_for("law-old", R.TransitionRule(CASE, "law-old", "law-new", "p-officer", "person")) == "law-new"
    assert R.law_doc_for("law-old", R.TransitionRule(CASE, "law-old", "law-new", "checklist-assessor", "agent")) == "law-old"
    assert R.law_doc_for("law-other", R.TransitionRule(CASE, "law-old", "law-new", "p-officer", "person")) == "law-other"


def test_after_a_persons_transition_the_new_law_is_the_only_one():
    moved = R.TransitionRule(CASE, "law-old", "law-new", "p-officer", "person")
    assert R.agent_law_allowed(check(source=LAW), ["law-new"], moved) is True
    assert R.agent_law_allowed(check(source=LAW), ["law-old"], moved) is False


# ---------------------------------------------------------------- record outbox, notify, hold


def test_record_outbox_job_is_keyed_on_the_decision():
    """The write reached the government system and the reply was lost, so a retry applied the licence action twice."""
    d = decision()
    assert R.record_job_for(d) == R.record_job_for(d)
    assert R.record_job_for(d).idempotency_key == "idem-dec-1"


def test_record_outbox_has_no_job_for_a_close():
    assert R.record_job_for(decision("close", record_status="not_needed")) is None


@pytest.mark.parametrize("state", ["queued", "sent", "unknown"])
def test_record_retry_uses_the_same_key_and_completes_rather_than_repeats(state):
    assert R.next_record_step(decision(), proposal(state)) == "send_same_key"


def test_record_retry_stops_when_done_and_reaches_a_person_when_it_cant_finish():
    """The connector kept failing and no person was ever assigned to it."""
    assert R.next_record_step(decision(), proposal("acknowledged")) == "done"
    assert R.next_record_step(decision(), proposal("rejected")) == "tell_person"
    assert R.next_record_step(decision(), proposal("unknown", attempts=5)) == "tell_person"


def test_applicant_is_told_only_after_the_record_is_confirmed():
    """The company held a signed decision that the system of record didn't show."""
    assert R.may_tell_applicant(decision(record_status="queued")) is False
    assert R.may_tell_applicant(decision(record_status="sent")) is False
    assert R.may_tell_applicant(decision(record_status="unknown")) is False
    assert R.may_tell_applicant(decision(record_status="acknowledged")) is True
    assert R.may_tell_applicant(decision("close", record_status="not_needed")) is True


def test_applicant_isnt_told_of_a_held_or_revoked_decision():
    assert R.may_tell_applicant(decision(state="on_hold", record_status="acknowledged")) is False
    assert R.may_tell_applicant(decision(state="revoked", record_status="acknowledged")) is False


def test_post_sign_hold_pauses_the_connector():
    """A suspension became known after signing while the connector was down. When it came back, the grant was written anyway."""
    d = decision(record_status="queued")
    assert R.post_sign_hold(d) is True
    held = decision(state="on_hold")
    assert R.connector_may_write(held) is False
    assert R.next_record_step(held, proposal("queued")) == "pause"
    assert R.next_record_step(decision(state="revoked"), proposal("queued")) == "stop"


def test_post_sign_hold_doesnt_apply_once_written_or_to_a_refusal():
    assert R.post_sign_hold(decision(record_status="acknowledged")) is False
    assert R.post_sign_hold(decision("refusal", record_status="queued")) is False


def test_a_new_suspension_holds_a_reconfirmed_grant_again():
    assert R.post_sign_hold(decision(state="reconfirmed", record_status="unknown")) is True


def test_only_a_decision_maker_ends_a_hold_on_current_facts():
    held = decision(state="on_hold")
    assert failed(R.hold_resolution_guards(held, "decision_maker", FP_A, FP_A, "Reference still valid.")) == []
    assert set(failed(R.hold_resolution_guards(held, "case_officer", FP_B, FP_A, " "))) == {"decision_maker_acts", "reason_given", "fingerprint_matches"}
    assert failed(R.hold_resolution_guards(decision(), "decision_maker", FP_A, FP_A, "Why.")) == ["decision_on_hold"]


# ---------------------------------------------------------------- freeze after sign


@pytest.mark.parametrize("status", ["signed", "recording", "closed"])
def test_freeze_after_sign_ignores_late_work(status):
    """A duplicate result landed after signature and changed a check under a signed decision."""
    for event in ("result", "brief", "recheck", "confirm", "waive"):
        assert R.after_sign_action(status, event) == "ignore"
    assert R.after_sign_action(status, "reply") == "log_only"
    assert R.decide_result("run-1", 1, 1, check(status="running"), status, []) == "ignore_frozen"


@pytest.mark.parametrize("status", ["in_review", "promoted", "held"])
def test_freeze_doesnt_touch_an_open_case(status):
    assert R.after_sign_action(status, "result") == "proceed"


# ---------------------------------------------------------------- infrastructure cause


def test_a_tool_outage_shows_as_error_not_as_the_companys_gap():
    """A tool failed mid-run, the agent carried on and said missing, and it looked like the company had left something out."""
    assert R.infrastructure_shows_as_error("completed", tool_errors=1) == "error"
    assert check_status("agent", "completed", finding="missing", tool_errors=1) == "error"


def test_a_reader_failure_or_two_rejections_show_as_error():
    assert R.infrastructure_shows_as_error("completed", reader_failed=True) == "error"
    assert R.infrastructure_shows_as_error("completed", rejected_twice=True) == "error"
    assert R.infrastructure_shows_as_error("completed") is None


def test_a_recheck_after_a_failed_reader_reads_again_first():
    assert R.recheck_steps(reader_failed=True) == ("reader", "check")
    assert R.recheck_steps(reader_failed=False) == ("check",)


def test_nobody_asks_the_company_about_our_own_failure():
    """A check on Error was sent to the company as a question, blaming it for our outage."""
    facts = ready_case(check(status="error"), status="in_review")
    assert failed(R.question_send_guards(facts, question("draft"), NOW + HOUR, NOW)) == ["not_about_system_failure"]


# ---------------------------------------------------------------- question blocks, silence, exit path


def held_case(*extra_checks, status="held", questions=(), **over) -> CaseFacts:
    """A file where the company's own gap holds a blocking check."""
    return ready_case(check(status="held"), *extra_checks, status=status, questions=questions, **over)


def leave(facts, decision_type="refusal", signer=SIGNER, view="decision_maker", reason="The reference is suspended.", now=NOW, fingerprint=None):
    return R.exit_path_guards(facts, signer, decision_type, view, fingerprint if fingerprint is not None else package(facts), reason, now)


def test_question_blocks_promote_and_sign():
    """A grant was signed with the company's answer still outstanding."""
    facts = ready_case(questions=(question(),))
    assert "no_open_questions" in failed(R.question_blocks(facts))
    assert "no_open_questions" in failed(R.sign_guard(facts, signing(facts)))
    assert "no_open_questions" in failed(R.promote_guard(replace(facts, case=case("in_review"))))


def test_every_question_leaves_with_a_future_deadline():
    facts = ready_case(status="in_review")
    assert failed(R.question_send_guards(facts, question("draft"), None, NOW)) == ["reply_deadline_set"]
    assert failed(R.question_send_guards(facts, question("draft"), NOW - HOUR, NOW)) == ["reply_deadline_set"]
    assert failed(R.question_send_guards(facts, question("draft"), NOW + HOUR, NOW)) == []


def test_company_silence_turns_overdue_at_the_deadline():
    assert R.company_silence(question(due=NOW + HOUR), NOW) == "waiting"
    assert R.company_silence(question(due=NOW), NOW) == "overdue"
    assert R.company_silence(question(due=None), NOW) == "overdue"
    assert R.company_silence(question("draft", due=None), NOW) == "not_waiting"
    assert R.company_silence(question("answered"), NOW) == "not_waiting"


def test_a_silent_company_cant_strand_the_file():
    """Making questions block created this one: the company never replied, so the file could never move again."""
    facts = held_case(questions=(question(due=NOW - HOUR),))
    assert "no_open_questions" in failed(R.promote_guard(facts))
    assert R.company_silence(facts.questions[0], NOW) == "overdue"
    assert failed(leave(facts)) == []


def test_exit_path_waits_while_the_company_can_still_reply():
    facts = held_case(questions=(question(due=NOW + HOUR),))
    assert failed(leave(facts)) == ["company_had_its_chance"]


def test_exit_path_waits_for_an_answer_nobody_has_read():
    assert failed(leave(held_case(questions=(question("answered"),)))) == ["company_had_its_chance"]


def test_a_case_officer_can_close_a_question_to_free_the_file():
    q = question(due=NOW + HOUR)
    facts = held_case(questions=(q,))
    assert failed(R.close_question_guards(facts, q, "case_officer", "No longer needed.")) == []
    closed = replace(facts, questions=(question("closed"),))
    assert failed(leave(closed)) == []


def test_closing_a_question_needs_the_right_person_and_a_reason():
    facts = held_case()
    assert set(failed(R.close_question_guards(facts, question("closed"), "auditor", ""))) == {"question_open", "view_may_close_question", "reason_given"}


@pytest.mark.parametrize("decision_type", ["refusal", "close"])
def test_exit_path_ends_a_held_case_with_no_waiver(decision_type):
    """A genuinely suspended reference could only be ended by waiving it, which is the wrong record for a refusal."""
    facts = held_case()
    assert all(c.waiver is None for c in facts.checks)
    assert failed(leave(facts, decision_type)) == []


@pytest.mark.parametrize("status", ["in_review", "held", "with_company", "promoted"])
def test_exit_path_is_open_from_every_stage_a_held_file_can_be_in(status):
    assert failed(leave(held_case(status=status))) == []


@pytest.mark.parametrize("dead", ["running", "pending", "error", "stale"])
def test_exit_path_refuses_while_a_run_is_dead(dead):
    """After a restart the run stopped, and refusing the company was the only move left: their file, our outage."""
    facts = held_case(check("N-2", status=dead))
    assert failed(leave(facts)) == ["no_dead_runs"]


def test_exit_path_needs_a_held_check_not_an_error_alone():
    facts = ready_case(check(status="error"), status="in_review")
    assert set(failed(leave(facts))) == {"has_held_check", "no_dead_runs"}


def test_exit_path_opens_once_the_dead_run_is_recovered():
    stuck = held_case(check("N-2", status="running"))
    assert failed(leave(stuck)) == ["no_dead_runs"]
    assert R.recover_lost_run(404, "running", reposts=0) == "repost_same_id"
    recovered = held_case(check("N-2", status="pass"))
    assert failed(leave(recovered)) == []


def test_a_grant_cant_use_the_exit_path():
    assert failed(leave(held_case(), "grant")) == ["decision_ends_case"]


def test_exit_path_still_needs_a_decision_maker_and_a_reason_and_separation():
    facts = held_case()
    assert set(failed(leave(facts, view="case_officer", reason=" "))) == {"decision_maker_acts", "reason_given"}
    assert failed(leave(facts, signer="p-promoter")) == ["signer_did_not_promote"]
    assert failed(leave(facts, signer="p-officer")) == ["signer_did_not_assess"]


def test_exit_path_refuses_an_old_view_a_signed_case_and_an_open_comment():
    facts = held_case()
    assert failed(leave(facts, fingerprint=FP_B)) == ["fingerprint_matches"]
    assert "case_not_frozen" in failed(leave(held_case(status="recording")))
    assert failed(leave(held_case(threads=(thread(),)))) == ["no_open_threads"]


def test_exit_path_needs_a_current_recommendation_so_a_failed_brief_is_recovered_first():
    facts = replace(held_case(), recommendations=())
    assert failed(leave(facts)) == ["current_recommendation"]
    assert R.restart_failed_coordinator("held", restarts=0) == "start_new_run"


def test_input_fingerprint_is_what_the_coordinator_fence_compares():
    facts = ready_case()
    changed = replace(facts, checks=(check(status="held"),))
    assert R.coordinator_fence(case_input_fingerprint(facts), case_input_fingerprint(changed), "in_review") == "discard_and_rebrief"


def test_a_case_with_no_foreign_reliance_needs_no_watch_source():
    assert R.fetch_at_sign_guard([], NOW, relies_on_foreign=False).passed is True


def test_a_reliance_case_with_no_watch_source_needs_a_reason():
    assert R.fetch_at_sign_guard([], NOW, relies_on_foreign=True).passed is False
    assert R.fetch_at_sign_guard([], NOW, relies_on_foreign=True, signer_reason="Agency site retired.").passed is True


def test_an_unreadable_source_blocks_until_the_signer_gives_a_reason():
    stale = source(last_success_at=NOW - HOUR)
    assert R.fetch_at_sign_guard([stale], NOW).passed is False
    assert R.fetch_at_sign_guard([stale], NOW, signer_reason="   ").passed is False
    allowed = R.fetch_at_sign_guard([stale], NOW, signer_reason="Agency site down. Checked by phone.")
    assert allowed.passed is True and "gave a reason" in allowed.detail

"""The rules of the case flow, as pure decisions. Each function answers one question and changes nothing.

Guards return `GuardResult`s for `enforce_guards`. The rest return a small answer for the handler to act on.
A rule that compares against current state only holds if the handler locks those rows in the transaction that writes.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Literal, Optional, Sequence

from pharmalign_contracts.server.models import (
    AgentRunView,
    CaseDetail,
    CheckDetail,
    Decision,
    GuardResult,
    Proposal,
    Question,
    WatchSource,
)

from .fingerprints import CaseFacts, case_checks, case_package_fingerprint, current_recommendation
from .guards import (
    FROZEN,
    SigningInput,
    fingerprint_guard,
    frozen_guard,
    guard,
    promotion_guards,
    question_guards,
    recommendation_guard,
    signer_guards,
    signing_guards,
    thread_guards,
)
from .status import RunState, infrastructure_cause, is_clear, is_clear_on_current_evidence

UNWRITTEN = frozenset({"queued", "sent", "unknown", "rejected"})
UNFINISHED_CHECK = frozenset({"error", "running", "pending", "stale"})
WAITING_FOR_RESULT = frozenset({"running", "pending", "stale"})
UNREADABLE_SOURCE = frozenset({"failing", "layout_changed", "zero_yield"})
MAX_REPOSTS = 3
MAX_COORDINATOR_RESTARTS = 3
MAX_RECORD_ATTEMPTS = 5


def _unique(guards: Iterable[GuardResult]) -> list[GuardResult]:
    seen: dict[tuple, GuardResult] = {}
    for g in guards:
        seen.setdefault((g.guard, g.passed, g.detail, g.check_id), g)
    return list(seen.values())


# ---------------------------------------------------------------- promote and sign


def _checks_loaded_guard(facts: CaseFacts) -> GuardResult:
    # With no rows, "every blocking check is clear" is true of nothing. Never let that through.
    ok = bool(case_checks(facts))
    return guard("check_results_present", ok, "Check results are present." if ok else "This case has no check results.")


def _evidence_bound_guards(facts: CaseFacts) -> list[GuardResult]:
    unbound = [c for c in case_checks(facts) if c.blocking and is_clear(c) and not is_clear_on_current_evidence(c)]
    if not unbound:
        return [guard("clear_on_current_evidence", True, "Every clear check is clear on the evidence it holds now.")]
    return [
        guard("clear_on_current_evidence", False, f"{c.check_id} is {c.status} without a person's act on its current evidence.", c.id)
        for c in unbound
    ]


def _all_comments_guard(facts: CaseFacts) -> list[GuardResult]:
    still_open = [t for t in facts.threads if t.case_id == facts.case_id and t.status == "open" and not t.blocks_promotion]
    if not still_open:
        return [guard("no_open_comments", True, "No review comment is open.")]
    return [guard("no_open_comments", False, f"Comment {t.id} is still open.") for t in still_open]


def promote_guard(facts: CaseFacts) -> list[GuardResult]:
    """Promote needs every blocking check clear on its current evidence, and a recommendation built on current results."""
    return [*promotion_guards(facts), _checks_loaded_guard(facts), *_evidence_bound_guards(facts), *_all_comments_guard(facts)]


def sign_guard(facts: CaseFacts, signing: SigningInput) -> list[GuardResult]:
    """The same test again at the moment of signing, because anything can change while a file waits for signature."""
    return [*signing_guards(facts, signing), _checks_loaded_guard(facts), *_evidence_bound_guards(facts), *_all_comments_guard(facts)]


def sign_compare_and_swap(facts: CaseFacts, sent_fingerprint: Optional[str], sent_recommendation_version: Optional[int]) -> list[GuardResult]:
    """The signature must carry what the signer saw, and that must still be the latest."""
    current = current_recommendation(facts)
    same_version = sent_recommendation_version is not None and current is not None and current.version == sent_recommendation_version
    return [
        fingerprint_guard(sent_fingerprint, case_package_fingerprint(facts)),
        guard(
            "signed_version_is_latest",
            same_version,
            f"Signing against v{sent_recommendation_version}." if same_version else "The signature must name the latest recommendation version.",
        ),
    ]


def sign_grant_guards(
    facts: CaseFacts,
    signing: SigningInput,
    sources: Sequence[WatchSource],
    fetch_started_at: datetime,
    *,
    relies_on_foreign: bool = True,
    signer_reason: Optional[str] = None,
) -> list[GuardResult]:
    """Everything a grant must pass. Handlers call this one."""
    return _unique([
        *sign_guard(facts, signing),
        *sign_compare_and_swap(facts, signing.fingerprint, signing.recommendation_version),
        fetch_at_sign_guard(sources, fetch_started_at, relies_on_foreign=relies_on_foreign, signer_reason=signer_reason),
    ])


def sign_other_guards(facts: CaseFacts, signing: SigningInput) -> list[GuardResult]:
    """A refusal, close or licence action on a file with nothing held. No foreign fetch: only a grant relies on it."""
    return _unique([*sign_guard(facts, signing), *sign_compare_and_swap(facts, signing.fingerprint, signing.recommendation_version)])


# ---------------------------------------------------------------- results from agents

ResultAction = Literal["apply", "ignore_frozen", "ignore_duplicate", "ignore_old_run", "ignore_not_waiting"]
BriefAction = Literal["accept", "discard_and_rebrief", "discard_frozen"]


def newest_run_only(generation: int, attempt: int, check: CheckDetail) -> bool:
    """A result counts only from the generation and attempt the check is waiting for."""
    return (generation, attempt) == (check.generation, check.attempt)


def apply_once(run_id: str, applied_run_ids: Iterable[str]) -> bool:
    """True the first time a run's result is seen. A second delivery does nothing."""
    return run_id not in set(applied_run_ids)


def decide_result(
    run_id: str, generation: int, attempt: int, check: CheckDetail, case_status: str, applied_run_ids: Iterable[str]
) -> ResultAction:
    """What to do with a delivered result. Store it in every case; only `apply` touches the check."""
    if case_status in FROZEN:
        return "ignore_frozen"
    if not apply_once(run_id, applied_run_ids):
        return "ignore_duplicate"
    if not newest_run_only(generation, attempt, check):
        return "ignore_old_run"
    if check.status not in WAITING_FOR_RESULT:
        return "ignore_not_waiting"
    return "apply"


def coordinator_fence(brief_input_fingerprint: str, current_input_fingerprint: str, case_status: str) -> BriefAction:
    """A brief counts only if it was built on the results as they stand now."""
    if case_status in FROZEN:
        return "discard_frozen"
    return "accept" if brief_input_fingerprint == current_input_fingerprint else "discard_and_rebrief"


# ---------------------------------------------------------------- restarts and lost runs

LostAction = Literal["keep_polling", "repost_same_id", "mark_lost", "nothing"]
CoordinatorAction = Literal["start_new_run", "tell_person", "nothing"]


@dataclass(frozen=True)
class RunAction:
    run_id: str
    action: Literal["poll"]


def reconcile_on_boot(runs: Iterable[AgentRunView]) -> list[RunAction]:
    """Polling comes from the database, so a restart loses nothing. Old runs are polled too; their results get fenced."""
    return [RunAction(r.run_id, "poll") for r in runs if r.status in ("queued", "running")]


def recover_lost_run(poll_status: int, run_status: RunState, reposts: int, past_deadline: bool = False, max_reposts: int = MAX_REPOSTS) -> LostAction:
    """A 404 on an unfinished run means the agent forgot it, not that it failed: send the same id and body again."""
    if run_status not in ("queued", "running"):
        return "nothing"
    if poll_status == 404:
        return "repost_same_id" if reposts < max_reposts else "mark_lost"
    return "mark_lost" if past_deadline else "keep_polling"


def restart_failed_coordinator(case_status: str, restarts: int, max_restarts: int = MAX_COORDINATOR_RESTARTS) -> CoordinatorAction:
    """A failed brief has no check to show Error on, so it must restart or reach a person. It can't just stop."""
    if case_status in FROZEN:
        return "nothing"
    return "start_new_run" if restarts < max_restarts else "tell_person"


def run_id_for(case_id: str, sequence: int, check_id: str, generation: int, attempt: int) -> str:
    """A re-check raises the attempt, so it always gets its own run id and never collides with the old run."""
    return f"{case_id.lower()}-s{sequence}-{check_id.lower()}-g{generation}-a{attempt}"


def recheck_steps(reader_failed: bool) -> tuple[str, ...]:
    """A re-check on a failed Reader must re-read first, or the check runs again with no facts."""
    return ("reader", "check") if reader_failed else ("check",)


def infrastructure_shows_as_error(
    run_status: Optional[RunState] = None, tool_errors: int = 0, reader_failed: bool = False, rejected_twice: bool = False
) -> Optional[Literal["error"]]:
    """"error" when the system failed, whatever the agent went on to say. None when the finding can stand."""
    return "error" if infrastructure_cause(run_status, tool_errors, reader_failed, rejected_twice) else None


# ---------------------------------------------------------------- waivers and partial re-runs


@dataclass(frozen=True)
class EvidenceChange:
    waiver_valid: Optional[bool]
    confirmation_valid: Optional[bool]
    status: str


def waiver_is_bound(check: CheckDetail) -> bool:
    """A waiver counts only on the evidence it was given on."""
    w = check.waiver
    return bool(w and w.valid and w.fingerprint == check.fingerprint)


def void_on_evidence_change(check: CheckDetail, new_fingerprint: str, rerun_pending: bool) -> EvidenceChange:
    """New evidence voids a person's act on the old. The check shows Stale until a re-run, or goes back to where a person must act."""
    w, c = check.waiver, check.confirmation
    waiver_voided = bool(w and w.valid and w.fingerprint != new_fingerprint)
    confirmation_voided = bool(c and c.valid and c.fingerprint != new_fingerprint)
    if rerun_pending:
        status = "stale"
    elif waiver_voided:
        status = "held"
    elif confirmation_voided:
        status = "person_confirms"
    else:
        status = check.status
    return EvidenceChange(
        waiver_valid=None if w is None else w.valid and not waiver_voided,
        confirmation_valid=None if c is None else c.valid and not confirmation_voided,
        status=status,
    )


@dataclass(frozen=True)
class CheckDependencies:
    check_id: str
    reads: Sequence[str] = ()
    reads_facts: Sequence[str] = ()
    extracts_facts: Sequence[str] = ()


def rerun_closure(changed_documents: Iterable[str], checks: Iterable[CheckDependencies]) -> list[str]:
    """Checks to re-run after a reply: those reading a changed document, or any fact extracted from one, at any depth.
    A check that declares nothing is re-run, since nobody can say it is unaffected."""
    documents = set(changed_documents)
    facts: set[str] = set()
    remaining = list(checks)
    chosen: set[str] = set()
    grew = True
    while grew:
        grew = False
        for d in remaining:
            if d.check_id in chosen:
                continue
            undeclared = not d.reads and not d.reads_facts
            if undeclared or documents.intersection(d.reads) or facts.intersection(d.reads_facts):
                chosen.add(d.check_id)
                facts.update(d.extracts_facts)
                grew = True
    return sorted(chosen)


# ---------------------------------------------------------------- foreign notices

NoticeScope = Literal["open_case", "signed_unrecorded", "recorded", "ended"]


@dataclass(frozen=True)
class NoticeGate:
    scope: NoticeScope
    tell_people: tuple[str, ...]
    tell_role: Optional[str]
    rerun_results: tuple[str, ...]
    hold_decision: bool


def notice_gate(
    case: CaseDetail, decision: Optional[Decision], foreign_status_results: Iterable[str], agent_rerun_results: Iterable[str] = ()
) -> NoticeGate:
    """Code acts the moment a notice matches, without waiting for the agent. The agent may add re-runs, never remove one."""
    if case.status not in FROZEN:
        return NoticeGate(
            scope="open_case",
            tell_people=(case.assigned_to,) if case.assigned_to else (),
            tell_role="case_officer",
            rerun_results=tuple(sorted({*foreign_status_results, *agent_rerun_results})),
            hold_decision=False,
        )
    signers = tuple(s.person_id for s in decision.signers) if decision else ()
    if decision is None or (decision.record_status in UNWRITTEN and decision.state != "revoked"):
        hold = decision is not None and post_sign_hold(decision)
        return NoticeGate("signed_unrecorded", signers, "decision_maker", (), hold)
    if decision.record_status == "acknowledged":
        return NoticeGate("recorded", signers, "decision_maker", (), False)
    return NoticeGate("ended", (), None, (), False)


def sources_to_fetch(sources: Iterable[WatchSource]) -> list[str]:
    """What signing a grant must fetch first. A manual source has nothing to fetch."""
    return [s.id for s in sources if s.kind != "manual"]


def fetch_at_sign_guard(
    sources: Sequence[WatchSource], fetch_started_at: datetime, *, relies_on_foreign: bool = True, signer_reason: Optional[str] = None
) -> GuardResult:
    """Every source read successfully since signing began. It closes the gap, but only as far as the source publishes.
    A foreign outage must not stop a country's approvals, so the signer can go on by giving a reason."""
    has_reason = bool(signer_reason and signer_reason.strip())
    if not sources:
        if not relies_on_foreign:
            return guard("foreign_sources_fresh", True, "This case doesn't rely on a foreign authority.")
        if has_reason:
            return guard("foreign_sources_fresh", True, "No watch source is set. The signer gave a reason.")
        return guard("foreign_sources_fresh", False, "No watch source is set, so foreign status can't be shown as current.")
    fetched = [s for s in sources if s.kind != "manual"]
    behind = [s for s in fetched if s.health in UNREADABLE_SOURCE or s.last_success_at is None or s.last_success_at < fetch_started_at]
    if behind:
        s = behind[0]
        last = s.last_success_at.isoformat() if s.last_success_at else "never"
        problem = f"{len(behind)} watch source{'' if len(behind) == 1 else 's'} could not be fetched. {s.regulator} last read: {last}."
        return guard("foreign_sources_fresh", has_reason, f"{problem} The signer gave a reason." if has_reason else f"{problem} Give a reason to sign anyway.")
    manual = len(sources) - len(fetched)
    return guard("foreign_sources_fresh", True, f"Fetched now. {manual} manual source{'' if manual == 1 else 's'} not fetched." if manual else "Fetched now.")


# ---------------------------------------------------------------- superseded law


@dataclass(frozen=True)
class TransitionRule:
    """A person's decision on which law applies to one case from now on."""

    case_id: str
    from_doc_id: str
    to_doc_id: str
    set_by: str
    set_by_type: Literal["person", "agent", "system"]


@dataclass(frozen=True)
class LawChange:
    stale_results: tuple[str, ...]
    tell_person: Optional[str]
    tell_role: Optional[str]


def _cites(c: CheckDetail, doc_id: str) -> bool:
    if c.source and c.source.doc_id == doc_id:
        return True
    return any(doc_id in e.anchor for e in c.evidence)


def law_superseded(facts: CaseFacts, doc_id: str) -> LawChange:
    """Findings citing superseded law go stale and the case's officer is told. Nothing re-runs until a person sets a transition."""
    if facts.case is None or facts.case.status in FROZEN:
        return LawChange((), None, None)
    settled = [c for c in case_checks(facts) if c.status not in ("pending", "running", "stale", "skipped") and _cites(c, doc_id)]
    if not settled:
        return LawChange((), None, None)
    return LawChange(tuple(c.id for c in settled), facts.case.assigned_to, "case_officer")


def law_rerun_guards(facts: CaseFacts, doc_id: str, transition: Optional[TransitionRule]) -> list[GuardResult]:
    ok = bool(
        transition
        and transition.set_by_type == "person"
        and transition.case_id == facts.case_id
        and transition.from_doc_id == doc_id
    )
    return [
        frozen_guard(facts),
        guard("transition_set_by_person", ok, "A person set which law applies." if ok else "A person must set which law applies to this case first."),
    ]


def law_doc_for(source_doc_id: str, transition: Optional[TransitionRule] = None) -> str:
    """The law an agent cites for a check: the pinned source, or the one a person moved it to."""
    moved = transition and transition.set_by_type == "person" and transition.from_doc_id == source_doc_id
    return transition.to_doc_id if moved else source_doc_id


def agent_law_allowed(check: CheckDetail, cited_doc_ids: Iterable[str], transition: Optional[TransitionRule] = None, supporting: Iterable[str] = ()) -> bool:
    """An agent answers from the law the pinned check names (or the one a person moved it to) and the check's supporting sources. Refuse anything else."""
    if check.source is None:
        return True
    allowed = {law_doc_for(check.source.doc_id, transition), *supporting}
    return all(doc_id in allowed for doc_id in cited_doc_ids)


# ---------------------------------------------------------------- the government record

RecordStep = Literal["send_same_key", "pause", "stop", "done", "tell_person"]


@dataclass(frozen=True)
class RecordJob:
    decision_id: str
    idempotency_key: str


def record_job_for(decision: Decision) -> Optional[RecordJob]:
    """Written in the signing transaction, so a restart can't lose it. The key is the decision, so a retry can't apply it twice,
    as long as the receiving system honours the key."""
    if decision.type == "close":
        return None
    return RecordJob(decision_id=decision.id, idempotency_key=f"idem-{decision.id}")


def next_record_step(decision: Decision, proposal: Proposal, max_attempts: int = MAX_RECORD_ATTEMPTS) -> RecordStep:
    """A retry completes a partial write with the same key. It never starts a new one."""
    if decision.state == "on_hold":
        return "pause"
    if decision.state == "revoked":
        return "stop"
    if proposal.state == "acknowledged":
        return "done"
    if proposal.state == "rejected" or proposal.attempts >= max_attempts:
        return "tell_person"
    return "send_same_key"


def may_tell_applicant(decision: Decision) -> bool:
    """The applicant hears only once the record shows the same thing."""
    return decision.state in ("signed", "reconfirmed") and decision.record_status in ("acknowledged", "not_needed")


def post_sign_hold(decision: Decision) -> bool:
    """True if a suspension learned now must pause this decision: a grant not yet written to the record."""
    return decision.type == "grant" and decision.state in ("signed", "reconfirmed") and decision.record_status in UNWRITTEN


def connector_may_write(decision: Decision) -> bool:
    return decision.state in ("signed", "reconfirmed")


def hold_resolution_guards(decision: Decision, view: str, sent_fingerprint: Optional[str], current_fingerprint: str, reason: Optional[str]) -> list[GuardResult]:
    """Only a Decision Maker ends a hold, by reconfirming or revoking, on what is true now."""
    is_dm = view == "decision_maker"
    has_reason = bool(reason and reason.strip())
    return [
        guard("decision_on_hold", decision.state == "on_hold", f"The decision is {decision.state}."),
        guard("decision_maker_acts", is_dm, "A Decision Maker is acting." if is_dm else "Only a Decision Maker can end a hold."),
        guard("reason_given", has_reason, "A reason is given." if has_reason else "A reason is required."),
        fingerprint_guard(sent_fingerprint, current_fingerprint),
    ]


# ---------------------------------------------------------------- after signing

SignedEvent = Literal["result", "brief", "reply", "recheck", "confirm", "waive"]


def after_sign_action(case_status: str, event: SignedEvent) -> Literal["proceed", "ignore", "log_only"]:
    """A signed case stays as it was signed. A late reply is written down, not run."""
    if case_status not in FROZEN:
        return "proceed"
    return "log_only" if event == "reply" else "ignore"


# ---------------------------------------------------------------- questions and ending a held case
# These three belong together. A question that blocks needs a deadline and a way out, or a silent company strands
# the file. The way out must refuse dead runs, or refusing the company becomes the cure for our own outage.


def question_blocks(facts: CaseFacts) -> list[GuardResult]:
    """Promote and sign wait while a question to the company is open."""
    return question_guards(facts)


def company_silence(question: Question, now: datetime) -> Literal["not_waiting", "waiting", "overdue"]:
    """Past the deadline, a person is told and may close the question or end the case."""
    if question.status != "sent":
        return "not_waiting"
    if question.reply_due_at is None or now >= question.reply_due_at:
        return "overdue"
    return "waiting"


def question_send_guards(facts: CaseFacts, question: Question, reply_due_at: Optional[datetime], now: datetime) -> list[GuardResult]:
    """Every question leaves with a deadline, and never asks the company about our own failure."""
    in_future = reply_due_at is not None and reply_due_at > now
    ours = [c for c in case_checks(facts) if c.id in question.check_ids or c.check_id in question.check_ids]
    errors = [c for c in ours if c.status == "error"]
    guards = [frozen_guard(facts), guard("reply_deadline_set", in_future, "The reply has a deadline." if in_future else "A question needs a reply deadline in the future.")]
    if not errors:
        return [*guards, guard("not_about_system_failure", True, "No question is about a system failure.")]
    return [*guards, *(guard("not_about_system_failure", False, f"{c.check_id} shows Error. Re-check it before asking the company.", c.id) for c in errors)]


def close_question_guards(facts: CaseFacts, question: Question, view: str, reason: Optional[str]) -> list[GuardResult]:
    """A case officer or Decision Maker can close an open question, so a silent company can't hold the file for ever."""
    may_act = view in ("case_officer", "decision_maker")
    is_open = question.status != "closed"
    has_reason = bool(reason and reason.strip())
    return [
        frozen_guard(facts),
        guard("question_open", is_open, f"Question {question.id} is {question.status}."),
        guard("view_may_close_question", may_act, "You may close this question." if may_act else "Only a case officer or Decision Maker can close a question."),
        guard("reason_given", has_reason, "A reason is given." if has_reason else "A reason is required."),
    ]


def _no_dead_runs_guards(facts: CaseFacts) -> list[GuardResult]:
    dead = [c for c in case_checks(facts) if c.blocking and c.status in UNFINISHED_CHECK]
    if not dead:
        return [guard("no_dead_runs", True, "Every blocking check has a real answer.")]
    return [guard("no_dead_runs", False, f"{c.check_id} is {c.status}, which is not the company's doing. Recover it first.", c.id) for c in dead]


def _company_had_its_chance_guards(facts: CaseFacts, now: datetime) -> list[GuardResult]:
    pending = [
        q for q in facts.questions
        if q.case_id == facts.case_id and (q.status == "answered" or company_silence(q, now) == "waiting")
    ]
    if not pending:
        return [guard("company_had_its_chance", True, "No reply is outstanding or unread.")]
    return [guard("company_had_its_chance", False, f"Question {q.id} is {q.status}. Wait for the deadline, or close it.") for q in pending]


def exit_path_guards(
    facts: CaseFacts, signer_id: str, decision_type: str, view: str, fingerprint: Optional[str], reason: Optional[str], now: datetime
) -> list[GuardResult]:
    """A Decision Maker can refuse or close a held case with no waiver. It needs a held check, and no check that is
    only unresolved because the system failed."""
    status = facts.case.status if facts.case else None
    ends_case = decision_type in ("refusal", "close")
    stage_ok = status in ("in_review", "held", "with_company", "promoted")
    is_dm = view == "decision_maker"
    has_reason = bool(reason and reason.strip())
    held = [c for c in case_checks(facts) if c.blocking and c.status == "held"]
    return [
        guard("decision_ends_case", ends_case, "This decision ends the case." if ends_case else "Only a refusal or a close can leave a held case."),
        frozen_guard(facts),
        guard("case_can_exit", stage_ok, f"The case is {status or 'unknown'}."),
        guard("decision_maker_acts", is_dm, "A Decision Maker is acting." if is_dm else "Only a Decision Maker can end a held case."),
        guard("reason_given", has_reason, "A reason is given." if has_reason else "A reason is required."),
        guard("has_held_check", bool(held), f"{len(held)} blocking check{'' if len(held) == 1 else 's'} held." if held else "No blocking check is held."),
        *_no_dead_runs_guards(facts),
        recommendation_guard(facts),
        *thread_guards(facts),
        *_all_comments_guard(facts),
        *_company_had_its_chance_guards(facts, now),
        fingerprint_guard(fingerprint, case_package_fingerprint(facts)),
        *signer_guards(facts, signer_id),
    ]

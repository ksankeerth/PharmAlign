"""The guards. Their results match the stored vectors in server/tests/vectors/guards.json. Each returns every guard with `passed` set, so screens can show what is
clear as well as what blocks. Handlers pass the result to `enforce_guards`, which refuses with a 409."""
from dataclasses import dataclass
from typing import Optional

from pharmalign_contracts.server.models import (
    CheckDefinition,
    CheckDetail,
    DocumentVersion,
    GuardResult,
    PackValidation,
    PackVersionSummary,
    Thread,
)

from .fingerprints import (
    CaseFacts,
    case_checks,
    case_input_fingerprint,
    case_package_fingerprint,
    current_recommendation,
    locale_sort_key,
    open_case_matches,
)
from .status import is_clear

MAX_TEXT = 500
FROZEN = frozenset({"signed", "recording", "closed"})
STATUS_WORD = {
    "pending": "Pending",
    "running": "Running",
    "pass": "Pass",
    "look": "Look",
    "person_confirms": "Person confirms",
    "confirmed": "Confirmed",
    "held": "Held",
    "waived": "Waived",
    "error": "Error",
    "stale": "Stale",
    "skipped": "Skipped",
}


def guard(name: str, passed: bool, detail: Optional[str] = None, check_id: Optional[str] = None) -> GuardResult:
    return GuardResult(guard=name, passed=passed, detail=detail[:MAX_TEXT] if detail else None, check_id=check_id)


def blocking_guards(facts: CaseFacts) -> list[GuardResult]:
    still_open = [c for c in case_checks(facts) if c.blocking and not is_clear(c)]
    if not still_open:
        return [guard("blocking_checks_clear", True, "Every blocking check is clear.")]
    return [guard("blocking_checks_clear", False, f"{c.check_id} {c.name} is {STATUS_WORD[c.status]}.", c.id) for c in still_open]


def recommendation_guard(facts: CaseFacts) -> GuardResult:
    rec = current_recommendation(facts)
    if rec is None:
        return guard("current_recommendation", False, "There is no recommendation yet.")
    if rec.input_fingerprint != case_input_fingerprint(facts):
        return guard("current_recommendation", False, f"Recommendation v{rec.version} predates the latest check changes. An updated brief is on its way.")
    return guard("current_recommendation", True, f"Recommendation v{rec.version} is current.")


def _thread_target(facts: CaseFacts, t: Thread) -> str:
    if t.target.type == "check":
        found = next((c for c in facts.checks if c.id == t.target.ref and c.is_current), None)
        return found.check_id if found else t.target.ref
    if t.target.type == "case":
        return "the case"
    return f"{t.target.type} {t.target.ref}"


def thread_guards(facts: CaseFacts) -> list[GuardResult]:
    still_open = [t for t in facts.threads if t.case_id == facts.case_id and t.status == "open" and t.blocks_promotion]
    if not still_open:
        return [guard("no_open_threads", True, "No open review comments.")]
    return [
        guard("no_open_threads", False, f"Open comment on {_thread_target(facts, t)}.", t.target.ref if t.target.type == "check" else None)
        for t in still_open
    ]


def question_guards(facts: CaseFacts) -> list[GuardResult]:
    still_open = [q for q in facts.questions if q.case_id == facts.case_id and q.status != "closed"]
    if not still_open:
        return [guard("no_open_questions", True, "No open company questions.")]
    return [
        guard("no_open_questions", False, f"Question {q.id} is {'a draft not yet sent or closed' if q.status == 'draft' else q.status}.")
        for q in still_open
    ]


def promotion_guards(facts: CaseFacts) -> list[GuardResult]:
    """What would block promotion now. Used by both the promotion check and promote."""
    status = facts.case.status if facts.case else None
    stage_ok = status in ("in_review", "held", "with_company")
    if stage_ok:
        detail = "The case is in review."
    elif status in ("in_pipeline", "intake"):
        detail = "Checks are still running."
    else:
        detail = f"The case is already {status if status is not None else 'undefined'}."
    return [
        guard("case_ready_for_promotion", stage_ok, detail),
        *blocking_guards(facts),
        recommendation_guard(facts),
        *thread_guards(facts),
        *question_guards(facts),
    ]


def fingerprint_guard(sent: Optional[str], current: str) -> GuardResult:
    ok = sent == current
    return guard("fingerprint_matches", ok, "Nothing changed since you read it." if ok else "Something changed since you read it. Review the latest version.")


def _case_rows(facts: CaseFacts) -> list[CheckDetail]:
    """Every result of the case, replaced ones too: a person who acted on an earlier one still took part."""
    rows = [c for c in facts.checks if c.case_id == facts.case_id]
    return sorted(rows, key=lambda c: locale_sort_key(c.check_id))


def assessors_of(facts: CaseFacts) -> set[str]:
    """People who assessed a case: the assignee, anyone who confirmed or waived a check, and recorded assessors."""
    ids = set(facts.assessors)
    if facts.case and facts.case.assigned_to:
        ids.add(facts.case.assigned_to)
    for c in _case_rows(facts):
        if c.confirmation:
            ids.add(c.confirmation.by)
        if c.waiver:
            ids.add(c.waiver.by)
    return ids


def _assessed_how(facts: CaseFacts, person_id: str) -> str:
    """How a person took part in the assessment, in words for the refusal."""
    rows = _case_rows(facts)
    waived = list(dict.fromkeys(c.check_id for c in rows if c.waiver and c.waiver.by == person_id))
    if waived:
        return f"You waived {', '.join(waived)} on this case"
    confirmed = list(dict.fromkeys(c.check_id for c in rows if c.confirmation and c.confirmation.by == person_id))
    if confirmed:
        return f"You confirmed {', '.join(confirmed)} on this case"
    return "You assessed this case"


@dataclass(frozen=True)
class SigningInput:
    signer_id: str
    fingerprint: Optional[str]
    recommendation_version: Optional[int] = None


def _applies_to_case(facts: CaseFacts, conflict) -> bool:
    if conflict.case_id == facts.case_id:
        return True
    return bool(conflict.party_id and facts.case and conflict.party_id == facts.case.applicant_party_id)


def signer_guards(facts: CaseFacts, signer_id: str) -> list[GuardResult]:
    """Separation of duties."""
    assessed = signer_id in assessors_of(facts)
    promoted_by = facts.promotion.promoted_by if facts.promotion else None
    conflict = any(c.person_id == signer_id and _applies_to_case(facts, c) for c in facts.conflicts)
    return [
        guard("signer_did_not_assess", not assessed, f"{_assessed_how(facts, signer_id)}, so you can't sign it." if assessed else "You did not assess or waive anything on this case."),
        guard("signer_did_not_promote", promoted_by != signer_id, "You promoted this case, so you can't sign it." if promoted_by == signer_id else "You did not promote this case."),
        guard("no_declared_conflict", not conflict, "You declared a conflict of interest for this case or company." if conflict else "No conflict declared."),
    ]


def signing_guards(facts: CaseFacts, signing: SigningInput) -> list[GuardResult]:
    """Signing repeats the promotion guards, plus fingerprint, separation of duties and foreign status."""
    status = facts.case.status if facts.case else None
    promoted = status == "promoted"
    current = current_recommendation(facts)
    matches = len(open_case_matches(facts))
    guards = [
        guard("case_promoted", promoted, "Promoted by a case officer." if promoted else f"The case is {status or 'unknown'}, not promoted."),
        *blocking_guards(facts),
        recommendation_guard(facts),
        *thread_guards(facts),
        *question_guards(facts),
        fingerprint_guard(signing.fingerprint, case_package_fingerprint(facts)),
        *signer_guards(facts, signing.signer_id),
        guard(
            "foreign_status_current",
            matches == 0,
            "No open foreign notices on this case." if matches == 0 else f"{matches} foreign notice match{' is' if matches == 1 else 'es are'} still open.",
        ),
    ]
    if signing.recommendation_version is not None:
        ok = current is not None and current.version == signing.recommendation_version
        guards.append(guard(
            "recommendation_version_current",
            ok,
            f"Signing against v{signing.recommendation_version}." if ok else f"The current recommendation is v{current.version if current else 'none'}.",
        ))
    return guards


def check_definition(facts: CaseFacts, c: CheckDetail) -> Optional[CheckDefinition]:
    return next((d for d in facts.pack_checks if d.id == c.check_id), None)


def frozen_guard(facts: CaseFacts) -> GuardResult:
    frozen = bool(facts.case and facts.case.status in FROZEN)
    return guard("case_not_frozen", not frozen, "A signed case is frozen." if frozen else "The case is open.")


def waive_guards(facts: CaseFacts, c: CheckDetail, view: str, fingerprint: Optional[str]) -> list[GuardResult]:
    """Waiving: only held, look or person-confirms checks; binding law needs a Decision Maker."""
    definition = check_definition(facts, c)
    waive_by = definition.waive_by if definition else ("decision_maker" if c.legal_force == "binding" else "case_officer")
    waivable = c.status in ("held", "look", "person_confirms")
    permitted = True
    detail = "You may waive this check."
    if waive_by == "nobody":
        permitted = False
        detail = "The pack says nobody may waive this check."
    elif waive_by == "decision_maker" and view != "decision_maker":
        permitted = False
        detail = "Waiving binding law needs a Decision Maker." if c.legal_force == "binding" else "The pack says only a Decision Maker may waive this check."
    word = STATUS_WORD[c.status]
    return [
        frozen_guard(facts),
        guard("check_waivable", waivable, f"{word} checks can be waived." if waivable else f"A {word} check can't be waived.", c.id),
        guard("waiver_permitted", permitted, detail, c.id),
        fingerprint_guard(fingerprint, c.fingerprint),
    ]


def confirm_guards(facts: CaseFacts, c: CheckDetail, fingerprint: Optional[str]) -> list[GuardResult]:
    """Confirming: only person-confirms or look checks, against the evidence the person read."""
    needs_person = c.status in ("person_confirms", "look")
    return [
        frozen_guard(facts),
        guard("check_needs_person", needs_person, "This check waits for a person." if needs_person else f"A {STATUS_WORD[c.status]} check has nothing to confirm.", c.id),
        fingerprint_guard(fingerprint, c.fingerprint),
    ]


def pack_approve_guards(pack: PackVersionSummary, approver_id: str, validation: PackValidation) -> list[GuardResult]:
    """A second person approves a simulated, valid pack. Nobody approves their own change."""
    own = pack.created_by == approver_id
    errors = sum(1 for i in validation.issues if i.severity == "error")
    if pack.state == "simulated":
        state_detail = "Simulated before approval."
    elif pack.state == "draft":
        state_detail = "Run a simulation before approval."
    else:
        state_detail = f"The version is already {pack.state}."
    return [
        guard("not_own_change", not own, "You wrote this change. A second Process Owner must approve it." if own else "Approved by someone other than the author."),
        guard("pack_simulated", pack.state == "simulated", state_detail),
        guard("pack_valid", validation.valid, "Validation passes." if validation.valid else f"{errors} validation error{'' if errors == 1 else 's'}."),
    ]


def pack_activate_guards(pack: PackVersionSummary) -> list[GuardResult]:
    ok = pack.state == "approved"
    return [guard("pack_approved", ok, "Approved by a second person." if ok else f"The version is {pack.state}, not approved.")]


def document_review_guards(version: DocumentVersion, reviewer_id: str, decision: str) -> list[GuardResult]:
    """Nobody approves a document version they uploaded; a failed quality gate can't be approved."""
    own = version.added_by == reviewer_id
    approving = decision in ("approved", "approved_with_notes")
    quality_ok = not approving or version.quality_status != "fail"
    return [
        guard("not_own_upload", not own, "You added this version. Another person must review it." if own else "Reviewed by someone other than the uploader."),
        guard("quality_gate_passed", quality_ok, "Quality gate allows this decision." if quality_ok else "The quality gate failed. Re-ingest before approving."),
    ]


def assign_guards(facts: CaseFacts, person_id: str) -> list[GuardResult]:
    """A declared conflict of interest blocks assignment."""
    conflict = next((c for c in facts.conflicts if c.person_id == person_id and _applies_to_case(facts, c)), None)
    return [
        frozen_guard(facts),
        guard("no_declared_conflict", conflict is None, f"Conflict declared: {conflict.reason}" if conflict else "No conflict declared."),
    ]

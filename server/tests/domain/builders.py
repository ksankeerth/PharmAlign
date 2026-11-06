"""Small contract objects for the domain tests."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from pharmalign_contracts.server.models import (
    CaseDetail,
    CheckDetail,
    ConfirmationView,
    Decision,
    Promotion,
    Proposal,
    Question,
    RecommendationVersion,
    Thread,
    WaiverView,
    WatchMatch,
    WatchSource,
)

from app.domain.fingerprints import CaseFacts, case_input_fingerprint, case_package_fingerprint

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
HOUR = timedelta(hours=1)
CASE = "CASE-1"
FP_A = "sha256:aaaa"
FP_B = "sha256:bbbb"
SIGNER = "p-signer"


def check(check_id: str = "M-1", **over) -> CheckDetail:
    fields = dict(
        id=f"cr-{check_id.lower()}", check_id=check_id, case_id=CASE, pipeline_run_id="run-1", name="A check", method="agent",
        status="pass", blocking=True, generation=1, attempt=1, updated_at=NOW, question="Is it met?", legal_force="binding",
        may_pass_alone=True, fingerprint=FP_A,
    )
    return CheckDetail(**{**fields, **over})


def waiver(fingerprint: str = FP_A, valid: bool = True, by: str = "p-waiver") -> WaiverView:
    return WaiverView(id="wv-1", by=by, reason="Accepted.", fingerprint=fingerprint, at=NOW, valid=valid)


def confirmation(fingerprint: str = FP_A, valid: bool = True, by: str = "p-confirmer") -> ConfirmationView:
    return ConfirmationView(id="cf-1", by=by, fingerprint=fingerprint, at=NOW, valid=valid)


def case(status: str = "promoted", **over) -> CaseDetail:
    fields = dict(
        id=CASE, case_type="new_registration", status=status, environment="simulation", medicine_id="med-1", applicant_party_id="party-1",
        sequence=1, assigned_to="p-officer", created_at=NOW, pack_version_id="pack-1", kb_snapshot_id="snap-1",
    )
    return CaseDetail(**{**fields, **over})


def thread(status: str = "open", blocks: bool = True) -> Thread:
    return Thread(id="t-1", case_id=CASE, target={"type": "case", "ref": CASE}, status=status, blocks_promotion=blocks, comments=[], created_at=NOW)


def question(status: str = "sent", due=NOW + HOUR, check_ids=("M-1",)) -> Question:
    return Question(id="q-1", case_id=CASE, status=status, check_ids=list(check_ids), texts={"en": "Please send it."}, drafted_by="person", reply_due_at=due)


def match(status: str = "open", id: str = "m-1") -> WatchMatch:
    return WatchMatch(id=id, notice_id="n-1", target_type="case", target_id=CASE, match_basis=["Same product."], match_confidence="high", status=status)


def source(last_success_at=NOW, health: str = "healthy", kind: str = "feed") -> WatchSource:
    return WatchSource(id="src-1", regulator="A regulator", kind=kind, check_every_minutes=60, health=health, last_success_at=last_success_at)


def decision(type: str = "grant", state: str = "signed", record_status: str = "queued") -> Decision:
    return Decision(
        id="dec-1", case_id=CASE, type=type, state=state, signers=[{"person_id": SIGNER, "role": "decision_maker", "signed_at": NOW}],
        signed_payload_hash=FP_A, recommendation_version=1, accepts_recommendation=True, reason="Reason." if type != "grant" else None,
        legal_effect="legal_act", record_status=record_status,
    )


def proposal(state: str = "queued", attempts: int = 0) -> Proposal:
    return Proposal(id="prop-1", decision_id="dec-1", action="grant_licence", state=state, idempotency_key="idem-dec-1", attempts=attempts, created_at=NOW, updated_at=NOW)


def with_brief(facts: CaseFacts, version: int = 1) -> CaseFacts:
    """Gives the case a recommendation built on its results as they stand."""
    rec = RecommendationVersion(
        version=version, case_id=CASE, choice="proceed", reason="Clear.", summary="Clear.", items_to_confirm=[],
        input_fingerprint=case_input_fingerprint(facts), is_current=True, created_at=NOW,
    )
    return replace(facts, recommendations=(rec,))


def ready_case(*checks, status: str = "promoted", **over) -> CaseFacts:
    """A promoted case that may be signed, unless a test changes something."""
    promotion = Promotion(id="pr-1", case_id=CASE, promoted_by="p-promoter", fingerprint=FP_A, at=NOW)
    facts = CaseFacts(case_id=CASE, case=case(status), checks=checks or (check(),), promotion=promotion, **over)
    return with_brief(facts)


def package(facts: CaseFacts) -> str:
    return case_package_fingerprint(facts)

"""The regulator records API: what PharmAlign expects from a government system of record.

The built-in mock implements it for simulation, and a real connector adapts a government system to it.
"""
from datetime import date
from typing import Annotated, Any, Literal, Optional

from pydantic import AwareDatetime, Field

from .common import CONTRACT_VERSION, Fingerprint, Health, Identifier, Language, ShortText, Slug, Strict
from .server.models import page_of
from .spec import ApiDoc, Q, R


class RecordLicence(Strict):
    id: Identifier
    version: Identifier = Field(description="Changes whenever this record changes.")
    status: Slug
    medicine_name: ShortText
    substances: list[ShortText]
    strength: Optional[ShortText] = None
    form: Optional[ShortText] = None
    holder: ShortText
    manufacturer: Optional[ShortText] = None
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None
    updated_at: AwareDatetime


class SubmissionDocument(Strict):
    id: Identifier
    filename: ShortText
    content_hash: Fingerprint
    language: Language
    size_bytes: Annotated[int, Field(ge=0)]
    module_hint: Optional[ShortText] = None


class Submission(Strict):
    id: Identifier
    sequence: Annotated[int, Field(ge=0)]
    case_type: Slug
    applicant: ShortText
    medicine_name: ShortText
    received_at: AwareDatetime
    documents: list[SubmissionDocument]
    licence_id: Optional[Identifier] = None


class ProposalCreate(Strict):
    """A proposed change. The government system decides whether to apply it, and remains the record."""

    action: Literal["grant", "refuse", "suspend", "revoke", "update", "send_question"]
    decision_ref: Identifier = Field(description="The signed decision behind this proposal.")
    signed_payload_hash: Fingerprint
    signed_by: ShortText
    signed_at: AwareDatetime
    licence_id: Optional[Identifier] = None
    submission_id: Optional[Identifier] = None
    based_on_version: Optional[Identifier] = Field(None, description="The record version this was based on. A mismatch is refused.")
    payload: dict[str, Any] = Field(default_factory=dict)


class ProposalStatus(Strict):
    id: Identifier
    idempotency_key: Identifier
    state: Literal["received", "applied", "rejected"]
    gov_ref: Optional[Identifier] = None
    resulting_version: Optional[Identifier] = None
    reason: Optional[ShortText] = None
    updated_at: AwareDatetime


class RecordEvent(Strict):
    id: Identifier
    at: AwareDatetime
    kind: Literal["submission_received", "reply_received", "licence_changed", "proposal_applied", "proposal_rejected"]
    object_id: Identifier


class ReviewMetrics(Strict):
    """Aggregates only. No individual records."""

    period_from: date
    period_to: date
    submissions_received: int
    decisions_recorded: int


class FailureMode(Strict):
    mode: Literal["normal", "applied_but_timed_out", "reject_next", "unavailable", "slow", "timeout_before_applying"]


class FailureModeSet(FailureMode):
    set_at: AwareDatetime


DESCRIPTION = """What PharmAlign expects from a government system of record. The built-in mock serves it at `/mock` in simulation, and a real connector adapts a government system to it.

**Principles**

- The government system stays the record. PharmAlign never overwrites it.
- Reading is the default. A change is a proposal backed by a signed decision.
- Only the fields a step needs. Nothing here is specific to one country.

**Exactly once.** `POST /proposals` requires `Idempotency-Key`. The same key returns the first result and applies nothing new. If a call times out, the caller reads `GET /proposals` by key to learn the outcome, and doesn't guess.

**Stale data.** `based_on_version` protects against changing a record that has since moved on.

**Test controls** exist only in the mock. A real connector doesn't implement them.
"""

REGULATOR_API = ApiDoc(
    title="PharmAlign Regulator Records API",
    version=CONTRACT_VERSION,
    description=DESCRIPTION,
    servers=("/mock",),
    security_description="Service token from the connection's settings.",
    routes={
        "Licences": [
            R("GET", "/licences", "List licences", page_of(RecordLicence),
              query=(Q("changed_since", "Only records changed after this time."), Q("substance"), Q("cursor"), Q("limit", type="integer"))),
            R("GET", "/licences/{licence_id}", "One licence, with its current version", RecordLicence),
        ],
        "Submissions": [
            R("GET", "/submissions", "List submissions", page_of(Submission), query=(Q("received_since"), Q("cursor"), Q("limit", type="integer"))),
            R("GET", "/submissions/{submission_id}", "One submission", Submission),
            R("GET", "/submissions/{submission_id}/documents/{document_id}", "Download a document", None,
              description="Returns the file. Treat it as untrusted."),
        ],
        "Proposals": [
            R("POST", "/proposals", "Propose a change", ProposalStatus, 202, body=ProposalCreate, idempotent=True,
              conflict="`based_on_version` no longer matches the record, or this key was used with a different body.",
              description="`Idempotency-Key` is required here."),
            R("GET", "/proposals", "Find a proposal's outcome by key", page_of(ProposalStatus), query=(Q("idempotency_key", required=True),)),
            R("GET", "/proposals/{proposal_id}", "A proposal's outcome", ProposalStatus),
        ],
        "Events": [
            R("GET", "/events", "Changes since a point in time", page_of(RecordEvent), query=(Q("since", required=True), Q("cursor"), Q("limit", type="integer"))),
        ],
        "Metrics": [
            R("GET", "/metrics/review", "Aggregate figures", ReviewMetrics, query=(Q("from", required=True), Q("to", required=True))),
        ],
        "Test controls": [
            R("POST", "/test-controls/failure-mode", "Make the mock misbehave", FailureModeSet, body=FailureMode, simulation_only=True,
              description="Plays failures such as a change that applied but timed out. Exists only in the mock."),
        ],
        "Operations": [
            R("GET", "/health", "Liveness", Health, public=True),
        ],
    },
)

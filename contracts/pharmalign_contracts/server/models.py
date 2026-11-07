"""Request and response models for the PharmAlign server API."""
from datetime import date
from typing import Annotated, Any, Generic, Literal, Optional, TypeVar

from pydantic import AwareDatetime, BaseModel, Field, create_model, model_validator

from ..agents.run_agents import ChallengeVerdict, CheckStatus, FactKey, GapSignal
from ..common import (
    Evidence,
    FactValue,
    Fingerprint,
    Identifier,
    Language,
    ModelCall,
    Problem,
    ShortText,
    Slug,
    Strict,
    Usage,
)

T = TypeVar("T", bound=BaseModel)
Timestamp = AwareDatetime
Reason = Annotated[str, Field(min_length=1, max_length=2000)]
View = Literal["platform_admin", "process_owner", "case_officer", "decision_maker", "auditor"]
Mode = Literal["simulation", "shadow", "live"]


class Page(Strict, Generic[T]):
    items: list[T]
    next_cursor: Optional[str] = Field(None, description="Pass as `cursor` for the next page. Null on the last page.")


def page_of(model: type[T]) -> type[BaseModel]:
    return create_model(f"{model.__name__}Page", __base__=Page[model])


class GuardResult(Strict):
    guard: Slug
    passed: bool
    detail: Optional[ShortText] = None
    check_id: Optional[Identifier] = None


class GuardProblem(Problem):
    """A refused action. Lists every guard that failed, not only the first."""

    failed: Annotated[list[GuardResult], Field(min_length=1)]
    current_fingerprint: Optional[Fingerprint] = None


class Ack(Strict):
    id: Identifier
    status: Slug
    at: Timestamp


# ------------------------------------------------------------ Identity

RoleType = Literal[
    "platform_admin", "authority_admin", "process_owner", "knowledge_steward", "intake_officer", "case_manager",
    "assessor", "inspector", "lab_analyst", "committee_secretary", "decision_maker", "watch_officer", "auditor",
    "executive_viewer", "applicant_contact", "peer_regulator",
]


class RoleGrant(Strict):
    id: Identifier
    person_id: Identifier
    role: RoleType
    discipline: Optional[Slug] = None
    scope: Optional[ShortText] = None
    valid_from: date
    valid_to: Optional[date] = None


class RoleGrantCreate(Strict):
    person_id: Identifier
    role: RoleType
    discipline: Optional[Slug] = None
    scope: Optional[ShortText] = None
    valid_from: date
    valid_to: Optional[date] = None


class Person(Strict):
    id: Identifier
    display_name: ShortText
    is_demo: bool
    grants: list[RoleGrant] = Field(default_factory=list)


class PersonCreate(Strict):
    display_name: ShortText
    email: Optional[ShortText] = None
    view: View = "case_officer"
    title: Optional[ShortText] = None


class WorkspaceSummary(Strict):
    id: Identifier
    name: ShortText
    country_code: str
    authority_name: ShortText
    mode: Literal["draft", "simulation", "shadow", "live"]


class Me(Strict):
    person_id: Identifier
    display_name: ShortText
    is_demo: bool
    view: View
    roles: list[RoleType]
    workspace_id: Optional[Identifier] = None
    workspace: Optional[WorkspaceSummary] = None
    mode: Mode


class DemoPerson(Strict):
    person_id: Identifier
    display_name: ShortText
    view: View


class ViewAsRequest(Strict):
    person_id: Identifier


class SessionWorkspaceRequest(Strict):
    workspace_id: Identifier


class DemoStatus(Strict):
    loaded_at: Optional[Timestamp] = None
    with_library: bool
    resetting: bool
    workspaces: list[WorkspaceSummary]
    people: int
    cases: int


class DemoResetRequest(Strict):
    confirm: Literal["RESET"]
    with_library: bool = True


class DelegationCreate(Strict):
    from_person_id: Identifier
    to_person_id: Identifier
    role: RoleType
    valid_from: date
    valid_to: date
    reason: Reason

    @model_validator(mode="after")
    def _end_after_start(self) -> "DelegationCreate":
        if self.valid_to < self.valid_from:
            raise ValueError("valid_to must not be before valid_from")
        return self


class Delegation(DelegationCreate):
    id: Identifier
    created_by: Identifier
    created_at: Timestamp


class ConflictCreate(Strict):
    person_id: Identifier
    party_id: Optional[Identifier] = None
    case_id: Optional[Identifier] = None
    reason: Reason

    @model_validator(mode="after")
    def _has_target(self) -> "ConflictCreate":
        if not self.party_id and not self.case_id:
            raise ValueError("a conflict needs a party or a case")
        return self


class Conflict(ConflictCreate):
    id: Identifier
    declared_at: Timestamp


# ------------------------------------------------------------ Workspaces and setup

SetupStage = Literal[
    "workspace", "library", "rules_and_workflows", "watch_sources", "connections", "people_and_notifications",
    "agents_and_limits", "test_and_simulate", "sign_off",
]


class WorkspaceCreate(Strict):
    name: ShortText
    country_code: Annotated[str, Field(pattern=r"^[A-Z]{2,3}$")]
    authority_name: ShortText
    languages: Annotated[list[Language], Field(min_length=1)]
    time_zone: ShortText
    day_counting: Literal["mon_fri", "sun_thu", "calendar"]
    data_location: ShortText
    template: Literal["platform", "region", "copy"]
    region: Optional[Slug] = None
    copy_from_workspace_id: Optional[Identifier] = None

    @model_validator(mode="after")
    def _template_source(self) -> "WorkspaceCreate":
        if self.template == "copy" and not self.copy_from_workspace_id:
            raise ValueError("copy needs copy_from_workspace_id")
        if self.template == "region" and not self.region:
            raise ValueError("region template needs region")
        return self


class Workspace(Strict):
    id: Identifier
    name: ShortText
    country_code: str
    authority_name: ShortText
    languages: list[Language]
    time_zone: ShortText
    day_counting: Literal["mon_fri", "sun_thu", "calendar"]
    data_location: ShortText
    mode: Literal["draft", "simulation", "shadow", "live"]
    active_pack_version_id: Optional[Identifier] = None
    active_kb_snapshot_id: Optional[Identifier] = None
    created_at: Timestamp


class SetupStageStatus(Strict):
    stage: SetupStage
    status: Literal["not_started", "in_progress", "done", "blocked"]
    done_when: ShortText
    blockers: list[ShortText] = Field(default_factory=list)
    completed_by: Optional[Identifier] = None
    completed_at: Optional[Timestamp] = None


class SetupView(Strict):
    workspace_id: Identifier
    stages: list[SetupStageStatus]


class StageCompleteRequest(Strict):
    note: Optional[Reason] = None


class ReadinessCheck(Strict):
    key: Slug
    label: ShortText
    status: Literal["pass", "fail", "mock", "not_run"]
    detail: Optional[ShortText] = None
    proof_ref: Optional[Identifier] = None


class ReadinessRun(Strict):
    id: Identifier
    workspace_id: Identifier
    status: Literal["running", "completed", "failed"]
    all_passed: bool
    checks: list[ReadinessCheck]
    started_at: Timestamp
    finished_at: Optional[Timestamp] = None


class SignOffRequest(Strict):
    readiness_run_id: Identifier
    pack_version_id: Identifier
    kb_snapshot_id: Identifier
    target_mode: Literal["simulation", "shadow", "live"]
    reason: Reason


class SignOff(SignOffRequest):
    id: Identifier
    workspace_id: Identifier
    signed_by: Identifier
    signed_at: Timestamp


# ------------------------------------------------------------ Document library

LegalStatus = Literal["in_force", "draft", "superseded", "review_needed", "not_applicable"]
DocReview = Literal["unreviewed", "approved", "approved_with_notes", "rejected", "needs_update"]
UpdatePattern = Literal["fixed", "versioned", "periodic", "living", "point_in_time", "draft"]
Shelf = Literal["shared", "country"]


class DocumentCreate(Strict):
    title: ShortText
    title_local: Optional[ShortText] = None
    issuer: ShortText
    jurisdiction: ShortText
    shelf: Shelf
    doc_type: Slug
    language: Language
    source_url: Optional[Annotated[str, Field(max_length=2000)]] = None
    watch_url: Optional[Annotated[str, Field(max_length=2000)]] = None
    update_pattern: UpdatePattern
    sharing_rights: Literal["yes", "no", "unknown"] = "unknown"
    slot_id: Optional[Identifier] = None


class Document(DocumentCreate):
    id: Identifier
    latest_version_id: Optional[Identifier] = None
    created_at: Timestamp
    next_check_on: Optional[date] = None


class DocumentVersion(Strict):
    id: Identifier
    document_id: Identifier
    version_label: Optional[ShortText] = None
    file_hash: Fingerprint
    filename: ShortText
    content_type: ShortText
    size_bytes: Annotated[int, Field(ge=0)]
    published_on: Optional[date] = None
    effective_on: Optional[date] = None
    source: Literal["url", "upload", "bundle", "connector"]
    added_by: Identifier
    added_at: Timestamp
    review_status: DocReview
    duplicate_of: Optional[Identifier] = None
    quality_status: Optional[Literal["pass", "attention", "fail"]] = None
    held: bool = Field(False, description="A person must look before this version is approved or ingested. Hidden text carried an instruction.")


class DocumentDetail(Document):
    versions: list[DocumentVersion]


class VersionUpload(Strict):
    """Upload a file. Records who added it and where it came from."""

    file: bytes
    version_label: Optional[ShortText] = None
    published_on: Optional[date] = None
    effective_on: Optional[date] = None
    provenance_note: Optional[Reason] = None


class VersionFetch(Strict):
    """Fetch from a URL."""

    source_url: Annotated[str, Field(max_length=2000)]
    version_label: Optional[ShortText] = None
    published_on: Optional[date] = None
    effective_on: Optional[date] = None


class IngestProfile(Strict):
    preset: Literal["law", "guideline", "web_page", "data_table", "scanned", "custom"]
    parser: Literal["best_layout", "fast_text", "web_page", "table"]
    page_range: Optional[Annotated[str, Field(pattern=r"^\d+(-\d+)?(,\d+(-\d+)?)*$")]] = None
    include_annexes: bool = True
    images: Literal["ignore_all", "ignore_and_flag", "ocr_image_only", "describe_with_person_check"] = "ignore_and_flag"
    ocr_languages: list[Language] = Field(default_factory=list)
    tables: Literal["keep", "flatten", "ignore"] = "keep"
    remove_headers_footers: bool = True
    remove_page_numbers: bool = True
    remove_contents_pages: bool = True
    split_by: Literal["article", "heading", "fixed"]
    words_per_chunk: Annotated[int, Field(ge=20, le=2000)] = 256
    overlap_words: Annotated[int, Field(ge=0, le=500)] = 30
    min_chunk_words: Annotated[int, Field(ge=0, le=200)] = 8

    @model_validator(mode="after")
    def _overlap(self) -> "IngestProfile":
        if self.overlap_words >= self.words_per_chunk:
            raise ValueError("overlap must be smaller than the chunk size")
        return self


class IngestPreset(Strict):
    key: Slug
    label: ShortText
    good_for: ShortText
    profile: IngestProfile


class IngestJobCreate(Strict):
    mode: Literal["trial", "full"] = Field(description="`trial` parses and chunks with no embedding. `full` needs an approved version.")
    profile: IngestProfile


class IngestJob(Strict):
    id: Identifier
    document_version_id: Identifier
    mode: Literal["trial", "full"]
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    profile: IngestProfile
    progress_pct: Annotated[int, Field(ge=0, le=100)]
    pages_done: Annotated[int, Field(ge=0)] = 0
    chunks: Annotated[int, Field(ge=0)] = 0
    warnings: list[ShortText] = Field(default_factory=list)
    error: Optional[ShortText] = None
    started_at: Optional[Timestamp] = None
    finished_at: Optional[Timestamp] = None


class ChunkView(Strict):
    anchor: Identifier
    document_version_id: Identifier
    position: Annotated[int, Field(ge=0)]
    chunk_type: Slug
    page: Optional[int] = None
    page_end: Optional[int] = None
    article: Optional[ShortText] = None
    section_path: Optional[Annotated[str, Field(max_length=1000)]] = None
    language: Language
    text: str
    text_hash: Fingerprint


class FlaggedPage(Strict):
    page: Annotated[int, Field(ge=1)]
    reason: Literal["image_only", "hidden_text", "garbled", "low_text", "other"]
    detail: Optional[ShortText] = None


class QualityCheck(Strict):
    key: Slug
    status: Literal["pass", "attention", "fail"]
    detail: Optional[ShortText] = None


class QualityReport(Strict):
    document_version_id: Identifier
    status: Literal["pass", "attention", "fail"]
    checks: list[QualityCheck]
    held: bool = Field(False, description="A person must look before this version is approved or ingested.")
    flagged_pages: list[FlaggedPage] = Field(default_factory=list)
    expected_articles: Optional[int] = None
    found_articles: Optional[int] = None
    words_lost: Optional[int] = None


class DocumentReviewRequest(Strict):
    decision: Literal["approved", "approved_with_notes", "rejected", "needs_update"]
    notes: Optional[Reason] = None

    @model_validator(mode="after")
    def _notes(self) -> "DocumentReviewRequest":
        if self.decision != "approved" and not self.notes:
            raise ValueError("this decision needs notes")
        return self


class DocumentReview(DocumentReviewRequest):
    id: Identifier
    document_version_id: Identifier
    reviewed_by: Identifier
    reviewed_at: Timestamp


class AdoptionUpdate(Strict):
    """Per country: the same shared document can hold a different status elsewhere."""

    adopted: bool = True
    legal_status: LegalStatus
    authority_level: Annotated[int, Field(ge=1, le=5, description="1 is binding law. 5 is context only.")]
    notes: Optional[Reason] = None


class Adoption(AdoptionUpdate):
    workspace_id: Identifier
    document_version_id: Identifier
    decided_by: Identifier
    decided_at: Timestamp


class Slot(Strict):
    id: Identifier
    key: Slug
    label: ShortText
    importance: Literal["must", "nice"]
    status: Literal["filled", "replace", "bad_copy", "missing"]
    document_id: Optional[Identifier] = None
    gap_id: Optional[Identifier] = None
    notes: Optional[Reason] = None


class SlotUpdate(Strict):
    status: Optional[Literal["filled", "replace", "bad_copy", "missing"]] = None
    document_id: Optional[Identifier] = None
    notes: Optional[Reason] = None


class SnapshotCreate(Strict):
    label: ShortText
    note: Optional[Reason] = None


class SnapshotItem(Strict):
    document_version_id: Identifier
    title: ShortText
    legal_status: LegalStatus
    authority_level: int


class Snapshot(Strict):
    id: Identifier
    workspace_id: Identifier
    label: ShortText
    content_hash: Fingerprint
    item_count: Annotated[int, Field(ge=0)]
    created_by: Identifier
    created_at: Timestamp


class SnapshotDetail(Snapshot):
    items: list[SnapshotItem]


class KbSearchRequest(Strict):
    query: Annotated[str, Field(min_length=1, max_length=2000)]
    purpose: Literal["requirement", "context"] = Field(
        "requirement", description="`requirement` returns in-force sources only. The same filters apply to agents."
    )
    snapshot_id: Optional[Identifier] = None
    top_k: Annotated[int, Field(ge=1, le=50)] = 10


class KbHit(Strict):
    anchor: Identifier
    text: str
    score: float
    document_id: Identifier
    title: ShortText
    issuer: ShortText
    jurisdiction: ShortText
    legal_status: LegalStatus
    authority_level: int
    article: Optional[ShortText] = None
    page: Optional[int] = None


class SearchTried(Strict):
    """One search made before reporting that no source was found."""

    search: ShortText
    candidates: int
    best_score: Optional[float] = None
    floor: Optional[float] = None
    result: Optional[ShortText] = None


class KbSearchResponse(Strict):
    no_source_found: bool
    filters_applied: dict[str, Any]
    searches_tried: list[SearchTried] = Field(
        default_factory=list, description="When no source was found: what was searched, to show nothing was missed."
    )
    hits: list[KbHit]


class RetrievalTestCreate(Strict):
    snapshot_id: Identifier
    question_set: Slug


class RetrievalTestResult(Strict):
    question: ShortText
    language: Language
    expected_document_id: Optional[Identifier] = None
    rank_of_document: Optional[int] = None
    rank_of_passage: Optional[int] = None
    no_source_found: bool


class RetrievalTestRun(Strict):
    id: Identifier
    status: Literal["running", "completed", "failed"]
    document_hit_at_1: Optional[float] = None
    document_hit_at_5: Optional[float] = None
    passage_hit_at_1: Optional[float] = None
    passage_hit_at_10: Optional[float] = None
    results: list[RetrievalTestResult] = Field(default_factory=list)


# ------------------------------------------------------------ Country pack

AgentAutonomy = Literal["off", "suggest", "do_and_report", "person_signs"]
StepKind = Literal["submit", "door", "work", "wait", "question", "decide", "record"]
LOCKED_STEP_KINDS = {"submit", "door", "decide", "record"}


class SourceRef(Strict):
    doc_id: Identifier
    clause: Optional[ShortText] = None


class CheckDefinition(Strict):
    """One check, written by a Process Owner."""

    id: Identifier
    version: Annotated[int, Field(ge=1)]
    question: Annotated[str, Field(min_length=1, max_length=1000)]
    source: Optional[SourceRef] = Field(None, description="Required unless marked local practice.")
    supporting_sources: list[SourceRef] = Field(
        default_factory=list, max_length=5,
        description="Other documents this check may read and cite, each with its clause, e.g. the guideline that implements the law.")
    local_practice: bool = False
    legal_force: Literal["binding", "guidance", "none"]
    case_types: list[Slug] = Field(default_factory=list, description="Empty means all.")
    product_types: list[Slug] = Field(default_factory=list, description="Empty means all.")
    routes: list[Slug] = Field(default_factory=list, description="Empty means all.")
    stage: Slug
    reads: list[ShortText] = Field(default_factory=list)
    method: Literal["code", "typed", "agent", "person"]
    profile: Optional[Literal["quality", "safety", "label_language"]] = None
    extract: list[FactKey] = Field(default_factory=list)
    rule: Optional[Annotated[str, Field(max_length=2000)]] = Field(None, description="For `code` and `typed` checks. Evaluated by code.")
    may_pass_alone: bool = False
    severity: Literal["blocking", "warning", "info"] = "blocking"
    on_fail: Literal["hold_for_officer", "draft_return_notice", "look"] = "hold_for_officer"
    waive_by: Literal["case_officer", "decision_maker", "nobody"] = "case_officer"

    @model_validator(mode="after")
    def _rules(self) -> "CheckDefinition":
        if self.source is None and not self.local_practice:
            raise ValueError("a check needs a source, or must be marked local practice")
        if self.supporting_sources and self.source is None:
            raise ValueError("supporting sources need a main source")
        if any(not s.clause for s in self.supporting_sources):
            raise ValueError("each supporting source needs a clause")
        pairs = [(s.doc_id, s.clause) for s in self.supporting_sources]
        if len(set(pairs)) != len(pairs) or (self.source is not None and (self.source.doc_id, self.source.clause) in pairs):
            raise ValueError("a supporting source repeats another source of this check")
        if self.method == "typed" and (not self.extract or not self.rule):
            raise ValueError("a typed check needs values to extract and a rule")
        if self.method == "code" and not self.rule:
            raise ValueError("a code check needs a rule")
        if self.method == "agent" and (self.source is None or self.profile is None):
            raise ValueError("an agent check needs a source and a profile")
        if self.legal_force == "binding" and self.waive_by == "case_officer":
            raise ValueError("binding law can't be waived by a case officer")
        if self.on_fail == "draft_return_notice" and self.method != "code":
            raise ValueError("only a code check can draft a return notice")
        return self


class NotifyRule(Strict):
    event: Slug
    audience: list[Slug]
    channels: list[Literal["in_app", "email", "sms"]] = ["in_app"]
    languages: list[Language] = Field(default_factory=list)
    remind_before_hours: Optional[Annotated[int, Field(ge=0)]] = None
    ack_within_hours: Optional[Annotated[int, Field(ge=0)]] = None
    escalate_to: list[Slug] = Field(default_factory=list)


class WorkflowStep(Strict):
    id: Slug
    name: ShortText
    kind: StepKind
    role: Slug
    discipline: Optional[Slug] = None
    deadline_days: Optional[Annotated[int, Field(ge=0)]] = None
    day_type: Literal["working", "calendar"] = "working"
    only_if: Optional[ShortText] = None
    agent_autonomy: AgentAutonomy = "off"
    check_ids: list[Identifier] = Field(default_factory=list)
    source: Literal["published", "template"] = "template"
    legal_basis: Optional[SourceRef] = None
    locked: bool = Field(False, description="A locked step can't be removed, and keeps its kind.")

    @model_validator(mode="after")
    def _locks(self) -> "WorkflowStep":
        if self.kind == "decide" and self.agent_autonomy != "person_signs":
            raise ValueError("a decision step always ends with a person signing")
        if self.kind == "question" and self.agent_autonomy in ("do_and_report", "person_signs"):
            raise ValueError("an agent can only suggest what goes to a company")
        return self


class RelianceRule(Strict):
    trusted_regulators: Annotated[list[ShortText], Field(min_length=1)]
    match: Literal["all", "any"]
    max_age_months: Optional[Annotated[int, Field(ge=0)]] = None
    product_types: Annotated[list[Slug], Field(min_length=1)]
    must_be_identical: bool = True
    source: Optional[SourceRef] = None
    local_practice: bool = False

    @model_validator(mode="after")
    def _source(self) -> "RelianceRule":
        if self.source is None and not self.local_practice:
            raise ValueError("a rule needs a source, or must be marked local practice")
        return self


class Route(Strict):
    id: Slug
    label: ShortText
    enabled: bool = True
    status_note: Optional[ShortText] = Field(None, description="Such as `proposed`, for a route no published source describes.")
    reliance_rule: Optional[RelianceRule] = Field(None, description="Null marks the fallback route.")
    published_time: Optional[ShortText] = None
    steps: Annotated[list[WorkflowStep], Field(min_length=1)]

    @model_validator(mode="after")
    def _order(self) -> "Route":
        kinds = [s.kind for s in self.steps]
        for needed in ("door", "decide", "record"):
            if needed not in kinds:
                raise ValueError(f"a route must have a {needed} step")
        if kinds.index("door") > kinds.index("decide"):
            raise ValueError("a door check must come before the decision")
        if kinds.index("decide") > kinds.index("record"):
            raise ValueError("the record can only update after a signed decision")
        ids = [s.id for s in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("step ids must be unique within a route")
        return self


class CaseType(Strict):
    id: Slug
    label: ShortText
    routes: Annotated[list[Route], Field(min_length=1)]


class SeverityFloor(Strict):
    change_type: Slug
    floor: Slug


class PackDocument(Strict):
    """Everything configurable for a country. Stored fully resolved, so a template change can't alter a running case."""

    schema_version: Literal["1"] = "1"
    country_code: Annotated[str, Field(pattern=r"^[A-Z]{2,3}$")]
    day_counting: Literal["mon_fri", "sun_thu", "calendar"]
    holidays: list[date] = Field(default_factory=list)
    languages: Annotated[list[Language], Field(min_length=1)]
    decision_body: ShortText
    licence_validity_years: Optional[Annotated[int, Field(ge=1)]] = None
    hot_climate_gap: Literal["ask", "commit", "block", "none"]
    source_precedence: list[Literal["authority_level", "in_force", "newer"]] = ["authority_level", "in_force", "newer"]
    case_types: Annotated[list[CaseType], Field(min_length=1)]
    checks: list[CheckDefinition] = Field(default_factory=list)
    notification_rules: list[NotifyRule] = Field(default_factory=list)
    final_escalation_holder: Slug
    severity_options: Annotated[list[Slug], Field(min_length=1)]
    severity_floors: list[SeverityFloor] = Field(default_factory=list)
    watch_change_types: list[Slug] = Field(default_factory=list)
    recommendation_options: list[Slug] = Field(default_factory=list)
    spot_check_rate: Annotated[float, Field(ge=0, le=1)] = 0.0

    @model_validator(mode="after")
    def _references(self) -> "PackDocument":
        check_ids = [c.id for c in self.checks]
        if len(check_ids) != len(set(check_ids)):
            raise ValueError("check ids must be unique")
        for case_type in self.case_types:
            if sum(1 for r in case_type.routes if r.reliance_rule is None) > 1:
                raise ValueError("a case type has at most one fallback route")
            for route in case_type.routes:
                for step in route.steps:
                    missing = set(step.check_ids) - set(check_ids)
                    if missing:
                        raise ValueError(f"step {step.id} refers to unknown checks: {sorted(missing)}")
        return self


PackState = Literal["draft", "simulated", "approved", "active", "retired"]


class PackVersionCreate(Strict):
    based_on_version_id: Optional[Identifier] = None
    template: Optional[Slug] = None

    @model_validator(mode="after")
    def _one_source(self) -> "PackVersionCreate":
        if bool(self.based_on_version_id) == bool(self.template):
            raise ValueError("give exactly one of based_on_version_id or template")
        return self


class PackVersionSummary(Strict):
    id: Identifier
    workspace_id: Identifier
    version: Annotated[int, Field(ge=1)]
    state: PackState
    content_hash: Fingerprint
    based_on_version_id: Optional[Identifier] = None
    created_by: Identifier
    created_at: Timestamp
    approved_by: Optional[Identifier] = None
    approved_at: Optional[Timestamp] = None


class PackVersion(PackVersionSummary):
    document: PackDocument


class PackDraftUpdate(Strict):
    document: PackDocument


class PackIssue(Strict):
    path: ShortText
    rule: Slug
    severity: Literal["error", "warning"]
    message: ShortText


class PackValidation(Strict):
    valid: bool
    issues: list[PackIssue]


class PackSimulateRequest(Strict):
    scenario_ids: Annotated[list[Identifier], Field(min_length=1)]
    execution: Literal["live_model", "replay"] = "replay"


class PackApproveRequest(Strict):
    reason: Reason


class PackChange(Strict):
    path: ShortText
    before: Any = None
    after: Any = None


class PackDiff(Strict):
    from_version_id: Identifier
    to_version_id: Identifier
    changes: list[PackChange]


class RepinRequest(Strict):
    pack_version_id: Identifier
    reason: Reason


# ------------------------------------------------------------ Medicines and parties


class Party(Strict):
    id: Identifier
    name: ShortText
    kind: Literal["applicant", "licence_holder", "agent", "manufacturer"]
    country: Optional[ShortText] = None
    gov_ref: Optional[Identifier] = None


class Medicine(Strict):
    id: Identifier
    name: ShortText
    substances: list[ShortText]
    strength: Optional[ShortText] = None
    form: Optional[ShortText] = None
    product_type: Slug


class Licence(Strict):
    """A mirror of the government record. It changes only when the government system confirms a change."""

    id: Identifier
    medicine_id: Identifier
    holder_party_id: Identifier
    gov_ref: Optional[Identifier] = None
    gov_status: Slug
    gov_version: Optional[Identifier] = None
    gov_as_of: Timestamp
    valid_to: Optional[date] = None


# ------------------------------------------------------------ Cases

CaseStatus = Literal[
    "intake", "in_pipeline", "held", "with_company", "in_review", "promoted", "signed", "recording", "closed"
]


class NewDossierDocument(Strict):
    filename: ShortText
    blob_ref: Identifier = Field(description="File already stored. Comes from the connector or simulation.")
    language: Language
    module_hint: Optional[ShortText] = None


class CaseCreate(Strict):
    case_type: Slug
    route: Optional[Slug] = None
    medicine_id: Identifier
    applicant_party_id: Identifier
    external_ref: Optional[Identifier] = None
    documents: list[NewDossierDocument] = Field(default_factory=list)


class CaseSummary(Strict):
    id: Identifier
    case_type: Slug
    route: Optional[Slug] = None
    status: CaseStatus
    environment: Literal["simulation", "live"]
    medicine_id: Identifier
    applicant_party_id: Identifier
    sequence: int
    assigned_to: Optional[Identifier] = None
    blocking_checks_open: int = 0
    next_deadline: Optional[Timestamp] = None
    created_at: Timestamp


class CaseDetail(CaseSummary):
    pack_version_id: Identifier
    kb_snapshot_id: Identifier
    current_pipeline_run_id: Optional[Identifier] = None
    current_recommendation_version: Optional[int] = None
    decision_id: Optional[Identifier] = None
    clock_running: bool = True
    days_used: Optional[int] = None


class AssignRequest(Strict):
    person_id: Identifier
    role: RoleType


class TimelineEvent(Strict):
    id: Identifier
    at: Timestamp
    kind: Slug
    summary: ShortText
    actor_type: Literal["person", "agent", "system"]
    actor_id: Identifier
    object_ref: Optional[Identifier] = None


class SequenceCreate(Strict):
    documents: Annotated[list[NewDossierDocument], Field(min_length=1)]
    note: Optional[Reason] = None


class Sequence(Strict):
    case_id: Identifier
    number: int
    received_at: Timestamp
    document_ids: list[Identifier]
    changed_check_ids: list[Identifier] = Field(default_factory=list, description="Checks that will re-run.")


class DossierDocument(Strict):
    id: Identifier
    case_id: Identifier
    version: Identifier
    sequence: int
    filename: ShortText
    module: Optional[ShortText] = None
    pages: Optional[int] = None
    language: Language
    suspect_pages: list[int] = Field(default_factory=list)


class DossierPage(Strict):
    document_id: Identifier
    page: Annotated[int, Field(ge=1)]
    anchor: Identifier
    text: str = Field(description="Visible text only. Hidden text is removed and reported separately.")
    suspect: bool = False
    hidden_text: Optional[str] = None


class RelianceBasisCreate(Strict):
    regulator: ShortText
    approval_ref: ShortText
    approved_on: date
    document_refs: list[Identifier] = Field(default_factory=list)


class RelianceBasis(RelianceBasisCreate):
    id: Identifier
    target_type: Literal["case", "licence"]
    target_id: Identifier
    verified: bool = False
    declared_same_by: Optional[ShortText] = None


# ------------------------------------------------------------ Pipeline and checks


class PipelineRunCreate(Strict):
    reason: Optional[Reason] = None


class StageStatus(Strict):
    key: Slug
    label: ShortText
    check_ids: list[Identifier]


class PipelineRun(Strict):
    id: Identifier
    case_id: Identifier
    sequence: int
    generation: int
    status: Literal["queued", "running", "completed", "superseded", "cancelled"]
    stages: list[StageStatus]
    started_at: Timestamp
    finished_at: Optional[Timestamp] = None


class CheckResult(Strict):
    id: Identifier
    check_id: Identifier
    case_id: Identifier
    pipeline_run_id: Identifier
    name: ShortText
    method: Literal["code", "typed", "agent", "person"]
    agent: Optional[Slug] = None
    status: CheckStatus
    blocking: bool
    summary: Optional[ShortText] = None
    generation: int
    attempt: int
    is_current: bool = Field(True, description="False for an earlier generation or attempt. It is kept for the record and no longer counts.")
    current_result_id: Optional[Identifier] = Field(None, description="For a result that is not current: the result that replaced it.")
    updated_at: Timestamp


class RuleOutcome(Strict):
    rule: str
    inputs: dict[str, Any]
    passed: bool


class ChallengeSummary(Strict):
    verdict: ChallengeVerdict
    reason: str
    evidence: list[Evidence] = Field(default_factory=list)
    counted: bool = Field(description="False if a dispute had no verified contradicting quote.")


class WaiverView(Strict):
    id: Identifier
    by: Identifier
    reason: Reason
    fingerprint: Fingerprint
    at: Timestamp
    valid: bool = Field(description="False once the evidence has changed.")


class ConfirmationView(Strict):
    id: Identifier
    by: Identifier
    note: Optional[Reason] = None
    fingerprint: Fingerprint
    at: Timestamp
    valid: bool


class CheckDetail(CheckResult):
    question: str
    source: Optional[SourceRef] = None
    legal_force: Literal["binding", "guidance", "none"]
    may_pass_alone: bool
    agent_status: Optional[Literal["met", "attention", "missing", "cannot_tell"]] = None
    extracted: list[FactValue] = Field(default_factory=list)
    rule_outcome: Optional[RuleOutcome] = None
    evidence: list[Evidence] = Field(default_factory=list)
    question_for_company: Optional[str] = None
    no_source_found: bool = False
    challenge: Optional[ChallengeSummary] = None
    waiver: Optional[WaiverView] = None
    confirmation: Optional[ConfirmationView] = None
    suspect_pages: list[int] = Field(default_factory=list)
    open_threads: int = 0
    fingerprint: Fingerprint = Field(description="Send this back when confirming or waiving.")
    evidence_opened_at: Optional[Timestamp] = None


class GateDecisionView(Strict):
    at: Timestamp
    checkpoint: Literal["tool_call", "source_read", "output", "effect"]
    allowed: bool
    rule: Slug
    detail: Optional[ShortText] = None


class ToolCallView(Strict):
    tool: Slug
    summary: Optional[ShortText] = None
    denied: bool = False
    error: Optional[ShortText] = None


class CheckTrace(Strict):
    """What was saved when the check ran. Nothing here is written afterwards."""

    check_result_id: Identifier
    basis: Literal["saved_reasoning", "rule_trace", "not_saved"]
    reasoning: Optional[str] = None
    rule_outcome: Optional[RuleOutcome] = None
    run_id: Optional[Identifier] = None
    agent_version: Optional[Identifier] = None
    prompt_hash: Optional[Fingerprint] = None
    pack_version_id: Identifier
    kb_snapshot_id: Identifier
    usage: Optional[Usage] = None
    model_calls: list[ModelCall] = Field(default_factory=list)
    tool_calls: list[ToolCallView] = Field(default_factory=list)
    gate_decisions: list[GateDecisionView] = Field(default_factory=list)


class ConfirmRequest(Strict):
    fingerprint: Fingerprint
    note: Optional[Reason] = None


class WaiveRequest(Strict):
    fingerprint: Fingerprint
    reason: Reason


class RecheckRequest(Strict):
    thread_id: Optional[Identifier] = None
    comment: Optional[Annotated[str, Field(max_length=4000)]] = None

    @model_validator(mode="after")
    def _has_context(self) -> "RecheckRequest":
        if not self.thread_id and not self.comment:
            raise ValueError("a re-check needs a thread or a comment")
        return self


class ConflictView(Strict):
    fact: Slug
    values: list[ShortText]
    check_ids: list[Identifier]


class RecommendationVersion(Strict):
    version: Annotated[int, Field(ge=1)]
    case_id: Identifier
    choice: Slug
    reason: ShortText
    summary: str
    items_to_confirm: list[Identifier] = Field(description="Built by code.")
    conflicts: list[ConflictView] = Field(default_factory=list, description="Built by code.")
    input_fingerprint: Fingerprint
    is_current: bool
    created_at: Timestamp


class SpotCheck(Strict):
    id: Identifier
    case_id: Identifier
    check_result_id: Identifier
    assigned_to: Optional[Identifier] = None
    status: Literal["open", "agree", "disagree"]
    note: Optional[Reason] = None


class SpotCheckComplete(Strict):
    status: Literal["agree", "disagree"]
    note: Optional[Reason] = None

    @model_validator(mode="after")
    def _disagree_note(self) -> "SpotCheckComplete":
        if self.status == "disagree" and not self.note:
            raise ValueError("a disagreement needs a note")
        return self


# ------------------------------------------------------------ Review and questions


class ThreadTarget(Strict):
    type: Literal["check", "page", "clause", "case"]
    ref: Identifier


class ThreadCreate(Strict):
    target: ThreadTarget
    text: Annotated[str, Field(min_length=1, max_length=4000)]


class CommentCreate(Strict):
    text: Annotated[str, Field(min_length=1, max_length=4000)]


class Comment(Strict):
    id: Identifier
    author_type: Literal["person", "agent"]
    author_id: Identifier
    text: str
    at: Timestamp


class Thread(Strict):
    id: Identifier
    case_id: Identifier
    target: ThreadTarget
    status: Literal["open", "resolved"]
    blocks_promotion: bool
    comments: list[Comment]
    created_at: Timestamp
    resolved_by: Optional[Identifier] = None
    resolved_at: Optional[Timestamp] = None


class ThreadResolve(Strict):
    note: Optional[Reason] = None


class Question(Strict):
    id: Identifier
    case_id: Identifier
    status: Literal["draft", "sent", "answered", "closed"]
    check_ids: list[Identifier]
    texts: dict[Language, str]
    drafted_by: Literal["agent", "person"]
    sent_by: Optional[Identifier] = None
    sent_at: Optional[Timestamp] = None
    reply_due_at: Optional[Timestamp] = None
    clock_stopped: bool = False


class QuestionCreate(Strict):
    check_ids: Annotated[list[Identifier], Field(min_length=1)]
    texts: dict[Language, Annotated[str, Field(min_length=1)]]


class QuestionUpdate(Strict):
    texts: dict[Language, Annotated[str, Field(min_length=1)]]


class QuestionSend(Strict):
    """A person sends it. This stops the clock if the pack says so."""

    reply_due_at: Timestamp


class QuestionClose(Strict):
    reason: Reason


# ------------------------------------------------------------ Promote and decide


class PromotionCheck(Strict):
    can_promote: bool
    guards: list[GuardResult]
    fingerprint: Fingerprint


class PromoteRequest(Strict):
    fingerprint: Fingerprint
    reason: Optional[Reason] = None


class Promotion(Strict):
    id: Identifier
    case_id: Identifier
    promoted_by: Identifier
    fingerprint: Fingerprint
    at: Timestamp


class ForeignStatus(Strict):
    reliance_basis_id: Optional[Identifier] = None
    last_checked_at: Optional[Timestamp] = None
    sources_healthy: bool
    open_notices: int = 0
    warning: Optional[ShortText] = None


class SigningPackage(Strict):
    """What will be signed. Signing sends its fingerprint back, and is refused if anything has changed."""

    case_id: Identifier
    fingerprint: Fingerprint
    recommendation: RecommendationVersion
    checks: list[CheckResult]
    waivers: list[WaiverView] = Field(default_factory=list)
    foreign_status: ForeignStatus
    pack_version_id: Identifier
    kb_snapshot_id: Identifier
    proposed_action: ShortText
    guards: list[GuardResult]
    legal_effect: Literal["legal_act", "record_only", "demonstration_only"]


class DecisionCreate(Strict):
    type: Literal["grant", "refusal", "close", "licence_action"]
    fingerprint: Fingerprint
    recommendation_version: Annotated[int, Field(ge=1)]
    accepts_recommendation: bool
    reason: Optional[Reason] = None
    conditions: list[ShortText] = Field(default_factory=list)
    foreign_status_reason: Optional[Reason] = Field(default=None, description="Why a grant goes ahead although foreign status could not be read")

    @model_validator(mode="after")
    def _reason_rules(self) -> "DecisionCreate":
        if not self.accepts_recommendation and not self.reason:
            raise ValueError("overriding a recommendation needs a reason")
        if self.type in ("refusal", "close", "licence_action") and not self.reason:
            raise ValueError("this decision needs a reason")
        return self


class Signer(Strict):
    person_id: Identifier
    role: RoleType
    signed_at: Timestamp


class Decision(Strict):
    id: Identifier
    case_id: Identifier
    type: Literal["grant", "refusal", "close", "licence_action"]
    state: Literal["signed", "on_hold", "reconfirmed", "revoked"]
    signers: Annotated[list[Signer], Field(min_length=1)]
    signed_payload_hash: Fingerprint
    recommendation_version: int
    accepts_recommendation: bool
    reason: Optional[Reason] = None
    conditions: list[ShortText] = Field(default_factory=list)
    legal_effect: Literal["legal_act", "record_only", "demonstration_only"]
    record_status: Literal["not_needed", "queued", "sent", "acknowledged", "unknown", "rejected"]
    proposal_id: Optional[Identifier] = None
    foreign_status_reason: Optional[Reason] = Field(
        default=None, description="Why this grant went ahead although foreign status could not be read. The signer's own words."
    )


class DecisionHoldAction(Strict):
    fingerprint: Fingerprint
    reason: Reason


# ------------------------------------------------------------ Government proposals and connections

ProposalState = Literal["queued", "sent", "acknowledged", "unknown", "rejected"]


class Proposal(Strict):
    id: Identifier
    decision_id: Identifier
    action: Slug
    state: ProposalState
    idempotency_key: Identifier
    based_on_gov_version: Optional[Identifier] = None
    attempts: int = 0
    gov_ref: Optional[Identifier] = None
    last_error: Optional[ShortText] = None
    created_at: Timestamp
    updated_at: Timestamp


class ManualAck(Strict):
    outcome: Literal["applied", "rejected"]
    gov_ref: Optional[Identifier] = None
    note: Optional[Reason] = None

    @model_validator(mode="after")
    def _ref(self) -> "ManualAck":
        if self.outcome == "applied" and not self.gov_ref:
            raise ValueError("an applied outcome needs the government reference")
        return self


class ConnectionCreate(Strict):
    name: ShortText
    kind: Literal["mock", "api", "manual"]
    base_url: Optional[Annotated[str, Field(max_length=2000)]] = None
    spec_version: ShortText


class ConformanceResult(Strict):
    key: Slug
    passed: bool
    detail: Optional[ShortText] = None


class Connection(ConnectionCreate):
    id: Identifier
    status: Literal["unchecked", "failing", "passing", "approved"]
    last_checked_at: Optional[Timestamp] = None
    last_sync_at: Optional[Timestamp] = None
    results: list[ConformanceResult] = Field(default_factory=list)
    approved_by: Optional[Identifier] = None


class ConnectionApprove(Strict):
    reason: Reason


# ------------------------------------------------------------ Foreign watch

SourceHealth = Literal["healthy", "failing", "zero_yield", "layout_changed", "manual", "unchecked"]
WatchTarget = Literal["licence", "case", "site", "none"]


class WatchSourceCreate(Strict):
    regulator: ShortText
    url: Optional[Annotated[str, Field(max_length=2000)]] = None
    kind: Literal["web_page", "feed", "staged", "manual"]
    check_every_minutes: Annotated[int, Field(ge=1)]
    severity_floor: Optional[Slug] = Field(
        None, description="The lowest severity a notice from this source gets. A pack's floor for the change type can only raise it."
    )
    notes: Optional[Reason] = None


class WatchSource(WatchSourceCreate):
    id: Identifier
    health: SourceHealth
    last_success_at: Optional[Timestamp] = None
    last_error: Optional[ShortText] = None
    items_last_30_days: int = 0


class FetchResult(Strict):
    source_id: Identifier
    fetched_at: Timestamp
    health: SourceHealth
    new_notices: int
    new_revisions: int


class NoticeSummary(Strict):
    id: Identifier
    regulator: ShortText
    reference: Optional[ShortText] = Field(
        None, description="The regulator's own reference for the notice. With a reference, a correction is the same notice when regulator and reference match. Without one, regulator and title decide."
    )
    title: ShortText
    published_on: Optional[date] = None
    fetched_at: Timestamp
    latest_revision: int
    retracted: bool = False
    language: Language
    severity: Slug = Field(description="Set first by code. An explanation can raise it, never lower it.")
    match_count: int
    unmatched: bool


class NoticeRevision(Strict):
    revision: int
    fetched_at: Timestamp
    text_anchor: Identifier
    content_hash: Fingerprint
    is_retraction: bool = False
    text: Optional[str] = Field(None, description="The notice text of this revision, as the regulator published it.")


class WatchMatch(Strict):
    id: Identifier
    notice_id: Identifier
    target_type: WatchTarget
    target_id: Optional[Identifier] = None
    match_basis: list[ShortText]
    match_confidence: Literal["high", "medium", "low"]
    status: Literal["open", "dismissed", "rereview_opened"]
    decided_by: Optional[Identifier] = None
    decided_at: Optional[Timestamp] = None
    reason: Optional[Reason] = None
    rereview_case_id: Optional[Identifier] = Field(None, description="The re-review case opened from this match.")


class NoticeAssessmentView(Strict):
    change_type: Slug
    summary: str
    suggested_severity: Slug
    evidence: list[Evidence] = Field(default_factory=list)
    run_id: Identifier


class NoticeDetail(NoticeSummary):
    revisions: list[NoticeRevision]
    matches: list[WatchMatch]
    assessment: Optional[NoticeAssessmentView] = None
    incident_id: Optional[Identifier] = None


class MatchDismiss(Strict):
    reason: Reason


class OpenRereview(Strict):
    reason: Reason


class WatchIncident(Strict):
    id: Identifier
    notice_id: Identifier
    site: Optional[ShortText] = None
    match_count: int
    severity: Slug
    status: Literal["open", "closed"]


class WatchCoverage(Strict):
    licences_total: int
    licences_with_reliance_link: int
    licences_without_link: list[Identifier]
    sources_total: int
    unhealthy_sources: list[Identifier]


class StagedNotice(Strict):
    regulator: ShortText
    reference: Optional[ShortText] = Field(None, description="The regulator's own reference for the notice.")
    title: ShortText
    text: Annotated[str, Field(min_length=1)]
    published_on: Optional[date] = None
    language: Language = "en"


# ------------------------------------------------------------ Notifications


class Notification(Strict):
    id: Identifier
    event: Slug
    severity: Slug
    title: ShortText
    body: Optional[str] = None
    link: Optional[Annotated[str, Field(max_length=500)]] = None
    state: Literal["pending", "sent", "failed", "acknowledged", "escalated"]
    ack_due_at: Optional[Timestamp] = None
    escalation_level: int = 0
    created_at: Timestamp
    acknowledged_at: Optional[Timestamp] = None


# ------------------------------------------------------------ Knowledge Assistant


class AssistantSessionCreate(Strict):
    """The person comes from the session. Without a `case_id` the chat is knowledge-only. With one, it also knows that case, and only people who may see the case can open it."""

    case_id: Optional[Identifier] = None
    language: Language = "en"


class AssistantSession(Strict):
    id: Identifier
    case_id: Optional[Identifier] = None
    created_at: Timestamp


class AssistantCitation(Evidence):
    """A quote the server checked against its source. Title and legal status come from the knowledge base, never the model.
    `legal_status` is the status in the pinned snapshot; it is empty for a case file or a foreign notice, which are not law."""

    source: Literal["knowledge", "case_file", "notice"]
    title: ShortText
    document_id: Identifier
    document_version_id: Optional[Identifier] = None
    legal_status: Optional[LegalStatus] = None
    article: Optional[ShortText] = None
    page: Optional[int] = None


# ------------------------------------------------------------ Gaps


class GapAnalysisCreate(Strict):
    signal_ids: list[Identifier] = Field(default_factory=list, description="Empty means all open signals.")


class GapAnalysis(Strict):
    id: Identifier
    status: Literal["queued", "running", "completed", "failed"]
    started_at: Timestamp
    finished_at: Optional[Timestamp] = None
    candidates_created: int = 0


class Gap(Strict):
    id: Identifier
    workspace_id: Identifier
    title: ShortText
    kind: Literal["policy", "standards"]
    status: Literal["candidate", "accepted", "rejected", "needs_evidence"]
    signal_ids: list[Identifier]
    description: str
    global_examples: list[Evidence] = Field(default_factory=list)
    pipeline_node: Optional[Slug] = None
    readiness_dimension: Optional[ShortText] = None
    suggested_next_step: Optional[ShortText] = None
    reviewed_by: Optional[Identifier] = None
    review_note: Optional[Reason] = None
    pack_version_id: Optional[Identifier] = Field(None, description="For an accepted gap: the pack draft it opened.")


class GapReview(Strict):
    status: Literal["accepted", "rejected", "needs_evidence"]
    review_note: Reason


# ------------------------------------------------------------ Platform Admin


class AgentHealthView(Strict):
    agent: Slug
    status: Literal["ok", "degraded", "down"]
    version: Optional[Identifier] = None
    contract_version: Optional[Identifier] = None
    models_available: int = 0
    models_total: int = 0
    last_success_at: Optional[Timestamp] = None
    failures_last_hour: int = 0


class AgentRunView(Strict):
    """Numbers and ids only. Holds no dossier content."""

    run_id: Identifier
    agent: Slug
    case_id: Optional[Identifier] = None
    check_id: Optional[Identifier] = None
    status: Literal["queued", "running", "completed", "stopped_early", "failed", "cancelled", "lost"]
    finish_reason: Optional[Slug] = None
    generation: Optional[int] = None
    attempt: Optional[int] = None
    is_current: bool = True
    duration_ms: Optional[int] = None
    usage: Optional[Usage] = None
    fallback_used: bool = False
    error_code: Optional[Slug] = None
    started_at: Optional[Timestamp] = None


class AgentUsage(Strict):
    agent: Slug
    runs: int
    failures: int
    usage: Usage
    gate_blocks: int
    citation_failures: int
    retrieval_misses: int
    dispute_rate: Optional[float] = None


class UsageReport(Strict):
    period_from: Timestamp
    period_to: Timestamp
    agents: list[AgentUsage]


# ------------------------------------------------------------ Simulation


class Scenario(Strict):
    id: Identifier
    label: ShortText
    kind: Literal["normal", "error", "gate"]
    description: str
    expected_outcome: ShortText


class SimulationCreate(Strict):
    scenario_id: Identifier
    pack_version_id: Identifier
    kb_snapshot_id: Identifier
    execution: Literal["live_model", "replay"] = "replay"
    gate: Literal["on", "off"] = "on"


class Simulation(SimulationCreate):
    id: Identifier
    status: Literal["queued", "running", "waiting_for_event", "completed", "failed"]
    case_id: Optional[Identifier] = None
    outcome: Optional[ShortText] = None
    expected_met: Optional[bool] = None
    started_at: Timestamp
    finished_at: Optional[Timestamp] = None


class SimulationEvent(Strict):
    kind: Literal["foreign_notice", "company_reply", "model_outage", "agent_restart", "server_restart", "gov_failure"]
    notice: Optional[StagedNotice] = None
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _notice(self) -> "SimulationEvent":
        if self.kind == "foreign_notice" and self.notice is None:
            raise ValueError("a foreign notice event needs a notice")
        return self


class ComparisonRow(Strict):
    aspect: ShortText
    governed: ShortText
    ungoverned: ShortText


class SimulationComparison(Strict):
    """Results of real runs, one with the gate on and one with it off."""

    scenario_id: Identifier
    governed_simulation_id: Identifier
    ungoverned_simulation_id: Identifier
    rows: list[ComparisonRow]


# ------------------------------------------------------------ Audit


class AuditEvent(Strict):
    id: Identifier
    at: Timestamp
    actor_type: Literal["person", "agent", "system", "connector"]
    actor_id: Identifier
    account_id: Optional[Identifier] = None
    role_used: Optional[Slug] = None
    action: Slug
    object_type: Slug
    object_id: Identifier
    case_id: Optional[Identifier] = None
    before: Any = None
    after: Any = None
    reason: Optional[str] = None
    row_hash: Optional[Fingerprint] = None


class CaseReconstruction(Strict):
    """Exactly what happened, from saved records. This doesn't re-run anything."""

    case: CaseDetail
    pipeline_runs: list[PipelineRun]
    checks: list[CheckDetail]
    traces: list[CheckTrace]
    recommendations: list[RecommendationVersion]
    threads: list[Thread]
    questions: list[Question]
    decision: Optional[Decision] = None
    proposal: Optional[Proposal] = None
    audit_events: list[AuditEvent]
    reconstructed_fingerprint: Optional[Fingerprint] = Field(
        None, description="The signing fingerprint worked out again from these saved records. It equals the decision's `signed_payload_hash` when nothing changed. Empty until a decision is signed."
    )


class AuditVerification(Strict):
    ok: bool
    rows_checked: int
    first_bad_row: Optional[Identifier] = None


# ------------------------------------------------------------ Operations


class ServerHealthDetails(Strict):
    status: Literal["ok", "degraded", "down"]
    version: Identifier
    contract_version: Identifier
    database: Literal["ok", "down"]
    agent_mode: Literal["http", "replay"] = Field(description="http: agent services, asked just now. replay: no services run, and the server answers from recordings.")
    agents: list[AgentHealthView]
    scheduler_last_tick_at: Optional[Timestamp] = None
    escalation_loop_last_run_at: Optional[Timestamp] = None
    ingest_worker: Literal["ok", "down"]


class TickRequest(Strict):
    tasks: list[Literal["watch", "escalations", "reconcile_runs"]] = Field(
        default_factory=list,
        description="`watch` fetches due sources, `escalations` sends reminders and escalates unacknowledged notifications, `reconcile_runs` re-attaches to unfinished agent runs. Empty means everything that is due.",
    )


class TickResult(Strict):
    started_at: Timestamp
    done: dict[str, int]

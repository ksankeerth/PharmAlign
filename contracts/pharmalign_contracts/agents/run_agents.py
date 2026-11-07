"""Contracts for the seven agents that are started and polled."""
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, Field, model_validator

from ..common import (
    CaseRef,
    Evidence,
    FactValue,
    Fingerprint,
    Identifier,
    Language,
    LongText,
    RunRequest,
    RunResponse,
    ShortText,
    Slug,
    Strict,
    base_request,
    completed_response,
)

CheckStatus = Literal[
    "pending", "running", "pass", "look", "person_confirms", "confirmed", "held", "waived", "error", "stale", "skipped"
]
ChallengeVerdict = Literal["upheld", "disputed", "cannot_verify"]


class FactKey(Strict):
    """A value to extract. The pack defines these."""

    key: Slug
    description: ShortText
    value_type: Literal["text", "number", "date", "boolean"]
    unit: Optional[Annotated[str, Field(max_length=32)]] = None
    look_in: list[ShortText] = Field(default_factory=list, description="Modules or sections to read first.")


class KnownFact(Strict):
    key: Slug
    value: ShortText
    unit: Optional[Annotated[str, Field(max_length=32)]] = None


# ---------------------------------------------------------------- Dossier Reader


class DocumentRef(Strict):
    doc_id: Identifier
    version: Identifier
    filename: ShortText
    pages: Optional[Annotated[int, Field(ge=1)]] = None
    language: Language
    suspect_pages: list[int] = Field(
        default_factory=list, description="Pages where code found hidden text. It has been removed from what tools return."
    )


class DossierReadRequest(RunRequest):
    case: CaseRef
    documents: Annotated[list[DocumentRef], Field(min_length=1)]
    changed_doc_ids: list[Identifier] = Field(
        default_factory=list, description="Empty means read everything. Otherwise only these changed."
    )
    modules: list[ShortText] = Field(description="Module labels allowed in `document_map`.")
    fact_keys: Annotated[list[FactKey], Field(min_length=1)]


class DocumentMapEntry(Strict):
    doc_id: Identifier
    module: Optional[ShortText] = Field(None, description="One of the given modules, or null if unclear.")
    basis: ShortText


class ReadIssue(Strict):
    doc_id: Identifier
    page: Optional[int] = None
    kind: Literal["unreadable", "language_mismatch", "looks_incomplete", "other"]
    detail: ShortText


class DossierReadResult(Strict):
    document_map: list[DocumentMapEntry]
    facts: list[FactValue]
    issues: list[ReadIssue] = Field(default_factory=list)
    searches_tried: list[ShortText] = Field(default_factory=list, description="What was searched or read to look for the facts, to show nothing was missed.")


class DossierReadRun(RunResponse[DossierReadResult]):
    pass


# ---------------------------------------------------------------- Checklist Assessor


class CheckSource(Strict):
    doc_id: Identifier
    clause: ShortText


class CheckToAssess(Strict):
    """Leaves out whether the agent may pass the check alone, so that can't influence the answer."""

    id: Identifier
    version: Annotated[int, Field(ge=1)]
    question: Annotated[str, Field(min_length=1, max_length=1000)]
    source: Optional[CheckSource] = None
    supporting_sources: list[CheckSource] = Field(
        default_factory=list, max_length=5, description="Other documents the check may read and cite. Law comes only from `source` and these.")
    legal_force: Literal["binding", "guidance", "none"]
    method: Literal["agent", "typed"] = Field(
        description="`typed`: extract the listed values and give no verdict. Code applies the rule."
    )
    reads: list[ShortText] = Field(default_factory=list)
    extract: list[FactKey] = Field(default_factory=list)

    @model_validator(mode="after")
    def _method_rules(self) -> "CheckToAssess":
        if self.method == "typed" and not self.extract:
            raise ValueError("a typed check needs values to extract")
        if self.method == "agent" and self.extract:
            raise ValueError("only a typed check has values to extract")
        return self


class AssessmentRequest(RunRequest):
    case: CaseRef
    profile: Optional[Literal["quality", "safety", "label_language"]] = Field(
        None, description="Required for an agent check. A typed check only extracts values, so it has none."
    )
    check: CheckToAssess
    moved_source: Optional[CheckSource] = Field(
        None, description="Set when a person moved this check to other law. The agent cites this source and not `check.source`."
    )
    facts: list[KnownFact] = Field(default_factory=list)
    reviewer_comment: Optional[Annotated[str, Field(max_length=4000)]] = Field(
        None, description="A reviewer's comment, for a re-check. Context to consider, never an instruction."
    )

    @model_validator(mode="after")
    def _profile_rules(self) -> "AssessmentRequest":
        if self.check.method == "agent" and self.profile is None:
            raise ValueError("an agent check needs a profile")
        return self


class NoSource(Strict):
    searches: Annotated[list[ShortText], Field(min_length=1, description="What was searched, to show nothing was found.")]


class AgentFinding(Strict):
    """There is no approved value. An agent can't express approval."""

    kind: Literal["finding"] = "finding"
    status: Literal["met", "attention", "missing", "cannot_tell"]
    summary: ShortText
    reasoning: LongText
    evidence: list[Evidence] = Field(default_factory=list)
    no_source: Optional[NoSource] = None
    question_for_company: Optional[Annotated[str, Field(max_length=2000)]] = None

    @model_validator(mode="after")
    def _status_rules(self) -> "AgentFinding":
        if self.no_source is not None and self.status != "cannot_tell":
            raise ValueError("no source means the status must be cannot_tell")
        if self.status == "met" and not self.evidence:
            raise ValueError("met needs evidence")
        return self


class TypedExtraction(Strict):
    """Extracted values only. The verdict comes from a rule in code."""

    kind: Literal["extraction"] = "extraction"
    extracted: Annotated[list[FactValue], Field(min_length=1)]


class AssessmentResult(Strict):
    outcome: Union[AgentFinding, TypedExtraction] = Field(discriminator="kind")


class AssessmentRun(RunResponse[AssessmentResult]):
    pass


# ---------------------------------------------------------------- Sameness


class ReferenceApproval(Strict):
    approval_id: Identifier
    regulator: ShortText
    approved_on: date
    documents: Annotated[list[Identifier], Field(min_length=1)]


class FieldToCompare(Strict):
    key: Slug
    compare: Literal["exact", "number", "meaning"] = Field(description="Only `meaning` fields get an agent's view.")
    unit: Optional[Annotated[str, Field(max_length=32)]] = None
    here: ShortText
    here_number: Optional[float] = None

    @model_validator(mode="after")
    def _number_field(self) -> "FieldToCompare":
        if self.compare == "number" and self.here_number is None:
            raise ValueError("a number field needs here_number")
        return self


class SamenessRequest(RunRequest):
    case: CaseRef
    reference: ReferenceApproval
    fields: Annotated[list[FieldToCompare], Field(min_length=1)]


class ComparedField(Strict):
    """Reports the foreign value. Code decides same or different for exact and number fields."""

    key: Slug
    there_status: Literal["found", "not_found", "ambiguous"]
    there_value: Optional[ShortText] = None
    there_number: Optional[float] = None
    reference_evidence: list[Evidence] = Field(default_factory=list, description="Quotes from the reference documents.")
    dossier_evidence: list[Evidence] = Field(default_factory=list, description="Quotes from the case's files, for the value here.")
    agent_view: Optional[Literal["same", "different", "unclear"]] = None

    @model_validator(mode="after")
    def _found_needs_evidence(self) -> "ComparedField":
        if self.there_status == "found" and (self.there_value is None or not self.reference_evidence):
            raise ValueError("a found value needs a value and a quote from the reference")
        if self.agent_view == "different" and not self.dossier_evidence:
            raise ValueError("a difference needs a quote from the case's files as well")
        return self


class SamenessResult(Strict):
    fields: list[ComparedField]
    searches_tried: list[ShortText] = Field(default_factory=list, description="What was read or searched in the reference documents.")


class SamenessRun(RunResponse[SamenessResult]):
    pass


# ---------------------------------------------------------------- Reliance Watch


class NoticeRef(Strict):
    notice_id: Identifier
    revision: Annotated[int, Field(ge=1)]
    regulator: ShortText
    title: ShortText
    published_on: Optional[date] = None
    language: Language
    text_anchor: Identifier = Field(description="Handle for `read_notice`: `<notice_id>:r<revision>:c<n>`, such as `n-501:r1:c1`.")

    @model_validator(mode="after")
    def _anchor_names_this_revision(self) -> "NoticeRef":
        if not self.text_anchor.startswith(f"{self.notice_id}:r{self.revision}:c"):
            raise ValueError("text_anchor must be <notice_id>:r<revision>:c<n>")
        return self


class WatchCandidate(Strict):
    """Found by code before the agent runs."""

    target_type: Literal["licence", "case", "site", "none"]
    target_id: Optional[Identifier] = None
    match_basis: list[ShortText] = Field(default_factory=list)
    match_confidence: Literal["high", "medium", "low"]


class NoticeAssessmentRequest(RunRequest):
    """A person has already been alerted by code. This run explains. Its outcome can't delay that alert."""

    trigger: Literal["scheduled", "at_submission", "manual"]
    notice: NoticeRef
    candidates: list[WatchCandidate]
    change_types: Annotated[list[Slug], Field(min_length=1)]
    severity_options: Annotated[list[Slug], Field(min_length=1, description="Lowest to highest.")]
    severity_floor: Slug = Field(description="The result can raise severity above this, never lower it.")
    open_case_checks: list[Identifier] = Field(default_factory=list)

    @model_validator(mode="after")
    def _floor_is_an_option(self) -> "NoticeAssessmentRequest":
        if self.severity_floor not in self.severity_options:
            raise ValueError("severity_floor must be one of severity_options")
        return self


class CandidateAssessment(Strict):
    target_type: Literal["licence", "case", "site", "none"]
    target_id: Optional[Identifier] = None
    applies: Literal["yes", "no", "unclear"]
    reason: ShortText


class NoticeAssessmentResult(Strict):
    change_type: Slug
    summary: Annotated[str, Field(min_length=1, max_length=2000)]
    suggested_severity: Slug
    candidates: list[CandidateAssessment]
    additional_checks: list[Identifier] = Field(
        default_factory=list, description="Checks to add to a re-run. Code decides the base list, and this can't remove any."
    )
    evidence: list[Evidence] = Field(default_factory=list)


class NoticeAssessmentRun(RunResponse[NoticeAssessmentResult]):
    pass


# ---------------------------------------------------------------- Challenger


class ChallengeTarget(Strict):
    """Leaves out the original reasoning, so this reading stays independent."""

    finding_id: Identifier
    kind: Literal["finding", "difference", "impact"]
    question: Annotated[str, Field(min_length=1, max_length=1000)]
    claimed_status: Slug
    claim: Annotated[str, Field(min_length=1, max_length=2000)]
    evidence: list[Evidence]


class ChallengeRequest(RunRequest):
    case: Optional[CaseRef] = None
    target: ChallengeTarget
    check_source: Optional[CheckSource] = Field(
        None, description="The law the check is pinned to, or the one a person moved it to. Law is cited only from here."
    )


class ChallengeResult(Strict):
    verdict: ChallengeVerdict
    reason: Annotated[str, Field(min_length=1, max_length=2000)]
    evidence: list[Evidence] = Field(default_factory=list)
    searches_tried: list[ShortText] = Field(default_factory=list, description="What was read or searched to test the claim.")

    @model_validator(mode="after")
    def _dispute_needs_evidence(self) -> "ChallengeResult":
        if self.verdict == "disputed" and not self.evidence:
            raise ValueError("a dispute needs evidence that contradicts the claim")
        return self


class ChallengeRun(RunResponse[ChallengeResult]):
    pass


# ---------------------------------------------------------------- Coordinator


class CheckSummary(Strict):
    check_id: Identifier
    status: CheckStatus
    summary: ShortText
    challenge: Optional[ChallengeVerdict] = None


class FactConflict(Strict):
    """Found by code."""

    fact: Slug
    values: Annotated[list[ShortText], Field(min_length=2)]
    check_ids: list[Identifier]


class OpenComment(Strict):
    thread_id: Identifier
    check_id: Optional[Identifier] = None
    text: Annotated[str, Field(min_length=1, max_length=4000)]


class PreviousRecommendation(Strict):
    version: Annotated[int, Field(ge=1)]
    choice: Slug
    reason: ShortText


class CaseEvent(Strict):
    kind: Slug
    summary: ShortText


class BriefRequest(RunRequest):
    input_fingerprint: Fingerprint = Field(description="Returned unchanged. A stale result is discarded.")
    case: CaseRef
    results: Annotated[list[CheckSummary], Field(min_length=1)]
    conflicts: list[FactConflict] = Field(default_factory=list)
    open_comments: list[OpenComment] = Field(default_factory=list)
    previous_recommendation: Optional[PreviousRecommendation] = None
    new_event: Optional[CaseEvent] = None
    recommendation_options: Annotated[list[Slug], Field(min_length=1, description="Listed by code. Proceed isn't offered while anything blocks.")]
    output_languages: Annotated[list[Language], Field(min_length=1)]


class Recommendation(Strict):
    choice: Slug
    reason: ShortText
    reason_evidence: Annotated[list[Identifier], Field(min_length=1)] = Field(
        description="The check ids (or finding ids) in `results` that the reason rests on."
    )


class DraftQuestion(Strict):
    check_ids: Annotated[list[Identifier], Field(min_length=1)]
    texts: dict[Language, Annotated[str, Field(min_length=1, max_length=2000)]] = Field(description="By language.")


class BriefResult(Strict):
    """Prose and drafts only. Code builds the item and conflict lists, and numbers the versions."""

    input_fingerprint: Fingerprint
    recommendation: Recommendation
    summary: Annotated[str, Field(min_length=1, max_length=4000)]
    draft_questions: list[DraftQuestion] = Field(default_factory=list)


class BriefRun(RunResponse[BriefResult]):
    pass


# ---------------------------------------------------------------- Gap Analyst


class GapSignal(Strict):
    """Counted by code."""

    id: Identifier
    kind: Literal["empty_slot", "no_source", "local_practice_rule", "repeated_waiver", "repeated_override"]
    detail: ShortText
    count: Annotated[int, Field(ge=1)] = 1


class GapAnalysisRequest(RunRequest):
    signals: Annotated[list[GapSignal], Field(min_length=1)]
    pipeline_nodes: list[Slug] = Field(default_factory=list)
    readiness_dimensions: list[ShortText] = Field(default_factory=list)


class GapCandidate(Strict):
    title: ShortText
    kind: Literal["policy", "standards"]
    signal_ids: Annotated[list[Identifier], Field(min_length=1)]
    pack_rule: Optional[Identifier] = Field(description="The check id of the pack rule this concerns, or null when there is no pack rule.")
    description: Annotated[str, Field(min_length=1, max_length=4000)]
    global_examples: list[Evidence] = Field(default_factory=list)
    pipeline_node: Optional[Slug] = None
    readiness_dimension: Optional[ShortText] = None
    suggested_next_step: Optional[ShortText] = None

    @model_validator(mode="after")
    def _no_rule_is_said(self) -> "GapCandidate":
        if self.pack_rule is None and "no pack rule" not in self.description.lower():
            raise ValueError('with no pack_rule, the description says "no pack rule"')
        return self


class GapAnalysisResult(Strict):
    """Candidates for a person to review. Nothing here is a confirmed gap."""

    candidates: list[GapCandidate]


class GapAnalysisRun(RunResponse[GapAnalysisResult]):
    pass


# ---------------------------------------------------------------- Catalogue

_CASE = {"case_id": "case-1042", "sequence": 2, "case_type": "new_registration", "route": "abridged", "product_type": "generic"}


@dataclass(frozen=True)
class RunAgent:
    name: str
    title: str
    path: str
    summary: str
    request: type[BaseModel]
    run: type[BaseModel]
    tools: tuple[str, ...]
    request_example: dict
    result_example: dict
    caller_rules: tuple[str, ...]


RUN_AGENTS = (
    RunAgent(
        name="dossier-reader",
        title="Dossier Reader",
        path="/dossier-reads",
        summary="Sorts a company's files by module and extracts key facts, each with a quote. It doesn't judge compliance.",
        request=DossierReadRequest,
        run=DossierReadRun,
        tools=("list_documents", "read_page", "read_table", "search_dossier", "verify_quote", "lookup_term"),
        request_example=base_request(
            "case-1042-s2-reader-g1-a1",
            case=_CASE,
            documents=[{"doc_id": "d-17", "version": "v1", "filename": "stability.pdf", "pages": 64, "language": "en", "suspect_pages": []}],
            changed_doc_ids=["d-17"],
            modules=["1", "2", "3.2.P.8", "4", "5"],
            fact_keys=[
                {"key": "shelf_life_months", "description": "Proposed shelf life", "value_type": "number", "unit": "months", "look_in": ["3.2.P.8"]},
                {"key": "manufacturing_site", "description": "Finished product site", "value_type": "text", "look_in": ["3.2.P.3"]},
            ],
        ),
        result_example={
            "document_map": [{"doc_id": "d-17", "module": "3.2.P.8", "basis": "Title and table of contents"}],
            "facts": [
                {"key": "shelf_life_months", "status": "found", "value": "36 months", "number": 36, "unit": "months",
                 "evidence": [{"anchor": "d-17:v1:p2", "quote": "a shelf life of 36 months"}]},
                {"key": "manufacturing_site", "status": "not_found", "evidence": []},
            ],
            "issues": [],
            "searches_tried": ["search_dossier: finished product site", "read_page: d-17 p2-5"],
        },
        caller_rules=("Checks every quote against the stored text.", "Accepts only the fact keys it asked for."),
    ),
    RunAgent(
        name="checklist-assessor",
        title="Checklist Assessor",
        path="/assessments",
        summary="Handles one check per run. For an `agent` check it reads the law and the file. For a `typed` check it only extracts values.",
        request=AssessmentRequest,
        run=AssessmentRun,
        tools=("search_kb", "read_clause", "get_doc_status", "read_page", "read_table", "search_dossier", "get_facts", "get_site", "verify_quote", "compute", "lookup_term"),
        request_example=base_request(
            "case-1042-s2-qual-07-g1-a1",
            case=_CASE,
            profile="quality",
            check={
                "id": "QUAL-07", "version": 3,
                "question": "Is long-term stability data given at 30 C / 65% RH for 12 months?",
                "source": {"doc_id": "E-S25", "clause": "3.2.6.1"},
                "legal_force": "guidance", "method": "typed", "reads": ["3.2.P.8"],
                "extract": [
                    {"key": "long_term_temp_c", "description": "Long-term storage temperature", "value_type": "number", "unit": "C"},
                    {"key": "long_term_rh_pct", "description": "Long-term relative humidity", "value_type": "number", "unit": "%"},
                    {"key": "long_term_months", "description": "Months of long-term data", "value_type": "number", "unit": "months"},
                ],
            },
            moved_source=None,
            facts=[{"key": "shelf_life_months", "value": "36", "unit": "months"}],
            reviewer_comment=None,
        ),
        result_example={"outcome": {"kind": "extraction", "extracted": [
            {"key": "long_term_temp_c", "status": "found", "value": "25", "number": 25, "unit": "C", "evidence": [{"anchor": "d-17:v1:p41", "quote": "25 C / 60% RH"}]},
            {"key": "long_term_rh_pct", "status": "found", "value": "60", "number": 60, "unit": "%", "evidence": [{"anchor": "d-17:v1:p41", "quote": "25 C / 60% RH"}]},
            {"key": "long_term_months", "status": "found", "value": "12", "number": 12, "unit": "months", "evidence": [{"anchor": "d-17:v1:p41", "quote": "long-term data for 12 months"}]},
        ]}},
        caller_rules=(
            "Runs the rule for typed checks.",
            "Decides the check status. A finding of met may still need a person to confirm.",
            "Refuses met without a verified quote from the knowledge base.",
            "Creates a gap candidate when `no_source` is set.",
        ),
    ),
    RunAgent(
        name="sameness",
        title="Sameness",
        path="/sameness-comparisons",
        summary="Finds each field's value in the foreign approval, so the product here can be compared with the one approved abroad.",
        request=SamenessRequest,
        run=SamenessRun,
        tools=("read_reference", "read_page", "get_facts", "verify_quote", "compute", "lookup_term"),
        request_example=base_request(
            "case-1042-s2-sameness-g1-a1",
            case=_CASE,
            reference={"approval_id": "ref-88", "regulator": "Example Regulator", "approved_on": "2025-09-01", "documents": ["ref-doc-3"]},
            fields=[
                {"key": "shelf_life_months", "compare": "number", "unit": "months", "here": "36 months", "here_number": 36},
                {"key": "manufacturing_site", "compare": "meaning", "here": "Example Pharma, Plant 2"},
            ],
        ),
        result_example={"fields": [
            {"key": "shelf_life_months", "there_status": "found", "there_value": "24 months", "there_number": 24,
             "reference_evidence": [{"anchor": "ref-doc-3:v1:p31", "quote": "a shelf life of 24 months"}], "dossier_evidence": [], "agent_view": None},
            {"key": "manufacturing_site", "there_status": "found", "there_value": "Example Pharma, Plant 2",
             "reference_evidence": [{"anchor": "ref-doc-3:v1:p12", "quote": "finished product made at Example Pharma, Plant 2 for this market"}],
             "dossier_evidence": [], "agent_view": "same"},
        ], "searches_tried": ["read_reference: ref-88", "read_page: ref-doc-3 p12, p31"]},
        caller_rules=(
            "Decides same or different for exact and number fields.",
            "Applies the pack's route triggers. The agent can't call a difference acceptable.",
        ),
    ),
    RunAgent(
        name="reliance-watch",
        title="Reliance Watch",
        path="/notice-assessments",
        summary="Explains what a foreign regulator's notice means for licences and cases that relied on it. Code has already alerted a person.",
        request=NoticeAssessmentRequest,
        run=NoticeAssessmentRun,
        tools=("read_notice", "list_recent_notices", "get_relied_on", "get_licence", "search_kb", "read_clause", "verify_quote", "compute"),
        request_example=base_request(
            "notice-501-r1-a1",
            priority="urgent",
            trigger="scheduled",
            notice={"notice_id": "n-501", "revision": 1, "regulator": "Example Regulator", "title": "Suspension of marketing authorisation", "published_on": "2026-11-02", "language": "en", "text_anchor": "n-501:r1:c1"},
            candidates=[{"target_type": "licence", "target_id": "lic-7", "match_basis": ["substance", "strength", "form", "manufacturer"], "match_confidence": "high"}],
            change_types=["withdrawal", "suspension", "label_change", "gmp_noncompliance", "recall", "other"],
            severity_options=["info", "review", "urgent"],
            severity_floor="urgent",
            open_case_checks=["R3", "M1", "QUAL-07"],
        ),
        result_example={
            "change_type": "suspension",
            "summary": "The reference approval was suspended over a quality finding.",
            "suggested_severity": "urgent",
            "candidates": [{"target_type": "licence", "target_id": "lic-7", "applies": "yes", "reason": "Same manufacturer and strength."}],
            "additional_checks": ["M1"],
            "evidence": [{"anchor": "notice-501:r1:c2", "quote": "the marketing authorisation is suspended with immediate effect"}],
        },
        caller_rules=(
            "Keeps severity at or above the floor.",
            "Decides what re-runs. `additional_checks` can only add to that list.",
            "Stores the notice even if nothing matches.",
        ),
    ),
    RunAgent(
        name="challenger",
        title="Challenger",
        path="/challenges",
        summary="Tries to disprove an important finding, usually with a stronger model. It never overwrites a finding.",
        request=ChallengeRequest,
        run=ChallengeRun,
        tools=("read_clause", "search_kb", "get_doc_status", "read_page", "search_dossier", "read_reference", "verify_quote", "compute"),
        request_example=base_request(
            "case-1042-s2-qual-08-challenge-g1-a1",
            case=_CASE,
            target={
                "finding_id": "f-221", "kind": "finding",
                "question": "Is the Arabic leaflet consistent with the approved text?",
                "claimed_status": "met",
                "claim": "The Arabic leaflet matches the approved dose.",
                "evidence": [{"anchor": "d-22:v1:p3", "quote": "500 mg twice daily"}],
            },
            check_source={"doc_id": "APPROVED-SMPC", "clause": "4.2"},
        ),
        result_example={
            "verdict": "disputed",
            "reason": "The Arabic text on page 3 gives a different dose.",
            "evidence": [{"anchor": "d-22:v1:p3", "quote": "1000 mg twice daily"}],
            "searches_tried": ["read_page: d-22 p3"],
        },
        caller_rules=(
            "Treats a dispute as cannot_verify unless its quote is verified and differs from the claim's evidence.",
            "A dispute that stands sets the check to Person confirms.",
        ),
    ),
    RunAgent(
        name="coordinator",
        title="Coordinator",
        path="/briefs",
        summary="Writes the brief, drafts one list of questions, and chooses from the recommendation options it is given.",
        request=BriefRequest,
        run=BriefRun,
        tools=("get_findings", "get_conflicts", "get_previous_run", "read_clause", "lookup_term"),
        request_example=base_request(
            "case-1042-s2-brief-g1-a1",
            input_fingerprint="sha256:9c1e",
            case=_CASE,
            results=[{"check_id": "M1", "status": "look", "summary": "Shelf life differs", "challenge": "upheld"}],
            conflicts=[{"fact": "shelf_life_months", "values": ["36", "24"], "check_ids": ["QUAL-07", "M1"]}],
            open_comments=[],
            previous_recommendation=None,
            new_event=None,
            recommendation_options=["hold_ask_company", "hold_officer_review", "change_route"],
            output_languages=["en", "ar"],
        ),
        result_example={
            "input_fingerprint": "sha256:9c1e",
            "recommendation": {"choice": "hold_ask_company", "reason": "Shelf life differs from the reference approval.", "reason_evidence": ["M1"]},
            "summary": "One conflict needs a decision before this file can proceed.",
            "draft_questions": [{"check_ids": ["M1"], "texts": {"en": "Please explain the 36-month shelf life compared with the 24 months approved."}}],
        },
        caller_rules=(
            "Discards the result if the fingerprint is out of date.",
            "Refuses a choice that wasn't offered.",
            "Builds the item and conflict lists itself, and numbers recommendation versions.",
            "A person sends any question.",
        ),
    ),
    RunAgent(
        name="gap-analyst",
        title="Gap Analyst",
        path="/gap-analyses",
        summary="Groups signals counted by code into policy-gap candidates and explains them, for a person to review.",
        request=GapAnalysisRequest,
        run=GapAnalysisRun,
        tools=("get_gap_signals", "get_pack_rule", "search_kb", "read_clause", "verify_quote"),
        request_example=base_request(
            "tenant-gaps-g1-a1",
            priority="batch",
            signals=[
                {"id": "s-1", "kind": "empty_slot", "detail": "Executive regulations of the medicines law", "count": 1},
                {"id": "s-2", "kind": "no_source", "detail": "Re-review trigger after a foreign change", "count": 6},
            ],
            pipeline_nodes=["source", "collector", "preprocessor", "model", "policy", "distributor", "sink"],
            readiness_dimensions=["Human interface", "AI policy and policy for AI"],
        ),
        result_example={"candidates": [{
            "title": "No rule on when a foreign change triggers re-review",
            "kind": "policy",
            "signal_ids": ["s-2"],
            "pack_rule": None,
            "description": "No in-force source sets a trigger, a timeline or a responsible role, and there is no pack rule for it.",
            "global_examples": [{"anchor": "F-01:v1:c88", "quote": "the sameness of the product is judged against the reference approval"}],
            "pipeline_node": "policy",
            "readiness_dimension": "AI policy and policy for AI",
            "suggested_next_step": "Review with the authority",
        }]},
        caller_rules=("Requires each candidate to cite a signal it was given.", "Stores results as candidates only."),
    ),
)


def poll_example(agent: RunAgent) -> dict:
    return completed_response(agent.request_example["run_id"], agent.name, agent.result_example)

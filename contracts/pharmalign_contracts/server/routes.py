"""Every operation of the PharmAlign server API."""
from typing import Annotated, Literal, Union

from pydantic import Field, TypeAdapter

from ..agents.knowledge_assistant import DraftComment, ErrorEvent, NoSource, NotAvailable, TextDelta, TurnRequest, UsageEvent
from ..common import CONTRACT_VERSION, Evidence, Health, Identifier, Strict
from ..spec import ApiDoc, Q, R
from . import models as m

ADMIN = ("platform_admin",)
OWNER = ("process_owner",)
OFFICER = ("case_officer",)
SIGNER = ("decision_maker",)
AUDITOR = ("auditor",)
ADMIN_OWNER = ADMIN + OWNER
REVIEWERS = OFFICER + SIGNER
CASE_VIEWS = OFFICER + SIGNER + AUDITOR
ALL = ADMIN + OWNER + CASE_VIEWS

PAGING = (Q("cursor", "From `next_cursor`."), Q("limit", "Page size, up to 200.", "integer"))


class ServerMessage(Strict):
    """A finished answer. The server sets `basis` from the traces it read, and checks and labels each citation, never the model.
    A citation whose quote isn't in its source is left out and counted in `unverified`."""

    type: Literal["message"] = "message"
    text: str
    basis: Literal["saved", "new_reading", "knowledge"] = Field(
        description="`saved`: from a trace saved when the check ran. `new_reading`: made now, in a chat on a case. `knowledge`: a chat with no case, answered from the knowledge base."
    )
    citations: list[m.AssistantCitation] = Field(default_factory=list)
    unverified: int = Field(0, ge=0)
    suggestions: list[str] = Field(default_factory=list, max_length=3)


ServerAssistantEvent = Annotated[
    Union[TextDelta, ServerMessage, DraftComment, NotAvailable, NoSource, UsageEvent, ErrorEvent],
    Field(discriminator="type"),
]

SIGN_REQUEST_EXAMPLE = {
    "type": "grant",
    "fingerprint": "sha256:9c1e",
    "recommendation_version": 2,
    "accepts_recommendation": True,
    "reason": None,
    "conditions": [],
}
SIGN_REFUSED_EXAMPLE = {
    "type": "about:blank",
    "title": "This decision can't be signed yet",
    "status": 409,
    "code": "guard_failed",
    "failed": [
        {"guard": "fingerprint_current", "passed": False, "detail": "Foreign status changed after this package was opened"},
        {"guard": "blocking_checks_clear", "passed": False, "detail": "Foreign status check is Held", "check_id": "R3"},
    ],
    "current_fingerprint": "sha256:77ab",
}
CONFIRM_EXAMPLE = {"fingerprint": "sha256:9c1e", "note": "Read the study table on page 41."}

ROUTES: dict[str, list[R]] = {
    "Identity": [
        R("GET", "/api/v1/me", "Who is signed in, and in which view", m.Me, views=ALL),
        R("GET", "/api/v1/demo-people", "People available for View as", m.page_of(m.DemoPerson), query=PAGING, views=ALL, simulation_only=True),
        R("POST", "/api/v1/session/view-as", "Switch to a demo person", m.Me, body=m.ViewAsRequest, views=ALL, simulation_only=True,
          description="Refused outside simulation. Each view is a named demo person, so separation-of-duty rules still apply."),
        R("POST", "/api/v1/session/workspace", "Choose the active workspace", m.Me, body=m.SessionWorkspaceRequest, views=ADMIN),
        R("GET", "/api/v1/people", "List people", m.page_of(m.Person), query=PAGING, views=ADMIN),
        R("POST", "/api/v1/people", "Add a person", m.Person, 201, body=m.PersonCreate, views=ADMIN, idempotent=True),
        R("GET", "/api/v1/people/{person_id}", "Person and their grants", m.Person, views=ADMIN),
        R("POST", "/api/v1/role-grants", "Grant a role", m.RoleGrant, 201, body=m.RoleGrantCreate, views=ADMIN, idempotent=True),
        R("DELETE", "/api/v1/role-grants/{grant_id}", "Remove a role grant", None, 204, views=ADMIN),
        R("GET", "/api/v1/delegations", "List delegations", m.page_of(m.Delegation), query=PAGING, views=ADMIN + AUDITOR),
        R("POST", "/api/v1/delegations", "Delegate a role for a period", m.Delegation, 201, body=m.DelegationCreate, views=ADMIN, idempotent=True),
        R("GET", "/api/v1/conflicts", "List conflict-of-interest declarations", m.page_of(m.Conflict), query=PAGING, views=ADMIN + AUDITOR),
        R("POST", "/api/v1/conflicts", "Declare a conflict of interest. It blocks assignment", m.Conflict, 201, body=m.ConflictCreate, views=ALL, idempotent=True),
    ],
    "Setup": [
        R("POST", "/api/v1/workspaces", "Create a country workspace", m.Workspace, 201, body=m.WorkspaceCreate, views=ADMIN, idempotent=True),
        R("GET", "/api/v1/workspaces", "List workspaces", m.page_of(m.Workspace), query=PAGING, views=ADMIN),
        R("GET", "/api/v1/workspaces/{workspace_id}", "Workspace details", m.Workspace, views=ADMIN_OWNER),
        R("GET", "/api/v1/workspaces/{workspace_id}/setup", "Setup stages and their status", m.SetupView, views=ADMIN_OWNER),
        R("POST", "/api/v1/workspaces/{workspace_id}/setup/stages/{stage}/complete", "Mark a stage done, if its checks pass", m.SetupStageStatus,
          body=m.StageCompleteRequest, views=ADMIN, guarded=True),
        R("POST", "/api/v1/workspaces/{workspace_id}/readiness-runs", "Run all readiness checks", m.ReadinessRun, 202, views=ADMIN, idempotent=True),
        R("GET", "/api/v1/workspaces/{workspace_id}/readiness", "Latest readiness results, with proof", m.ReadinessRun, views=ADMIN_OWNER),
        R("POST", "/api/v1/workspaces/{workspace_id}/sign-off", "A named person approves a version and sets the mode", m.SignOff, 201,
          body=m.SignOffRequest, views=OWNER, guarded=True, idempotent=True),
    ],
    "Document library": [
        R("GET", "/api/v1/documents", "List documents", m.page_of(m.Document),
          query=(Q("slot_id"), Q("shelf", enum=("shared", "country")), Q("review_status"), Q("due_for_check", type="boolean"), *PAGING), views=ADMIN_OWNER),
        R("POST", "/api/v1/documents", "Register a document", m.Document, 201, body=m.DocumentCreate, views=ADMIN, idempotent=True),
        R("GET", "/api/v1/documents/{document_id}", "Document and its versions", m.DocumentDetail, views=ALL,
          description="Anyone signed in can read a document on the shared shelf or their own country's shelf, so a cited source can be opened."),
        R("POST", "/api/v1/documents/{document_id}/versions", "Add a version, by upload or URL", m.DocumentVersion, 201, body=m.VersionFetch,
          forms=(("multipart/form-data", m.VersionUpload),), views=ADMIN, idempotent=True,
          conflict="A version with this content already exists.",
          description="Versions are never edited or deleted. Duplicates are detected by content hash. Old chunks stay, so past decisions can still show what applied."),
        R("GET", "/api/v1/document-versions/{version_id}", "One version", m.DocumentVersion, views=ADMIN_OWNER + AUDITOR),
        R("GET", "/api/v1/ingest-presets", "Ingestion presets", m.page_of(m.IngestPreset), views=ADMIN),
        R("POST", "/api/v1/document-versions/{version_id}/ingest-jobs", "Start ingestion", m.IngestJob, 202, body=m.IngestJobCreate, views=ADMIN,
          guarded=True, idempotent=True,
          description="`trial` needs no approval and has no effect on search. `full` is refused until the version is approved and has passed the quality gate."),
        R("GET", "/api/v1/ingest-jobs/{job_id}", "Ingestion progress and warnings", m.IngestJob, views=ADMIN_OWNER),
        R("DELETE", "/api/v1/ingest-jobs/{job_id}", "Cancel ingestion", m.IngestJob, views=ADMIN),
        R("GET", "/api/v1/document-versions/{version_id}/chunks", "Preview chunks", m.page_of(m.ChunkView), query=PAGING, views=ALL),
        R("GET", "/api/v1/document-versions/{version_id}/quality", "Quality gate result and flagged pages", m.QualityReport, views=ADMIN_OWNER),
        R("POST", "/api/v1/document-versions/{version_id}/review", "Approve or reject a version", m.DocumentReview, 201, body=m.DocumentReviewRequest,
          views=OWNER, guarded=True, description="Refused for the person who added the version."),
        R("GET", "/api/v1/workspaces/{workspace_id}/adoptions", "This country's status for each document", m.page_of(m.Adoption), query=PAGING, views=ADMIN_OWNER + AUDITOR),
        R("PUT", "/api/v1/workspaces/{workspace_id}/adoptions/{version_id}", "Set this country's legal status and level", m.Adoption, body=m.AdoptionUpdate, views=OWNER),
        R("GET", "/api/v1/workspaces/{workspace_id}/slots", "Required document slots and their status", m.page_of(m.Slot), query=PAGING, views=ADMIN_OWNER),
        R("PATCH", "/api/v1/slots/{slot_id}", "Link a document, or mark a slot missing", m.Slot, body=m.SlotUpdate, views=ADMIN,
          description="A required slot marked missing creates a policy-gap candidate."),
        R("POST", "/api/v1/workspaces/{workspace_id}/kb-snapshots", "Freeze the approved versions", m.Snapshot, 201, body=m.SnapshotCreate, views=OWNER, idempotent=True),
        R("GET", "/api/v1/workspaces/{workspace_id}/kb-snapshots", "List snapshots", m.page_of(m.Snapshot), query=PAGING, views=ALL),
        R("GET", "/api/v1/kb-snapshots/{snapshot_id}", "Snapshot contents", m.SnapshotDetail, views=ALL),
        R("POST", "/api/v1/kb-search", "Test search, using the same filters as agents", m.KbSearchResponse, body=m.KbSearchRequest, views=ADMIN_OWNER + REVIEWERS),
        R("POST", "/api/v1/retrieval-tests", "Run a retrieval question set", m.RetrievalTestRun, 202, body=m.RetrievalTestCreate, views=ADMIN, idempotent=True),
        R("GET", "/api/v1/retrieval-tests/{test_id}", "Retrieval test results", m.RetrievalTestRun, views=ADMIN_OWNER),
    ],
    "Country packs": [
        R("GET", "/api/v1/workspaces/{workspace_id}/packs", "Pack versions and their state", m.page_of(m.PackVersionSummary), query=PAGING, views=ADMIN_OWNER + AUDITOR),
        R("POST", "/api/v1/workspaces/{workspace_id}/packs", "New draft, from a template or a version", m.PackVersion, 201, body=m.PackVersionCreate, views=OWNER, idempotent=True,
          description="Rollback is a new draft based on an older version."),
        R("GET", "/api/v1/pack-versions/{version_id}", "The fully resolved pack", m.PackVersion, views=ALL),
        R("PUT", "/api/v1/pack-versions/{version_id}", "Save a draft", m.PackVersion, body=m.PackDraftUpdate, views=OWNER, if_match=True,
          conflict="Only a draft can be changed."),
        R("POST", "/api/v1/pack-versions/{version_id}/validate", "Check locks, order and a source per rule", m.PackValidation, views=OWNER),
        R("POST", "/api/v1/pack-versions/{version_id}/simulate", "Test before approval", m.Simulation, 202, body=m.PackSimulateRequest, views=OWNER, simulation_only=True, idempotent=True),
        R("POST", "/api/v1/pack-versions/{version_id}/approve", "A second person approves", m.PackVersionSummary, body=m.PackApproveRequest, views=OWNER, guarded=True,
          description="Refused for the author."),
        R("POST", "/api/v1/pack-versions/{version_id}/activate", "Make active for new cases", m.PackVersionSummary, views=OWNER, guarded=True,
          description="Cases in progress stay on the version they started with."),
        R("GET", "/api/v1/pack-versions/{version_id}/diff", "Compare with another version", m.PackDiff, query=(Q("against", required=True),), views=ALL),
        R("POST", "/api/v1/cases/{case_id}/repin", "Move one case to a corrected pack", m.CaseDetail, body=m.RepinRequest, views=OWNER, guarded=True,
          description="Needs a second approver, and is logged."),
    ],
    "Medicines": [
        R("GET", "/api/v1/medicines", "List medicines", m.page_of(m.Medicine), query=(Q("search"), *PAGING), views=CASE_VIEWS),
        R("GET", "/api/v1/medicines/{medicine_id}", "One medicine", m.Medicine, views=CASE_VIEWS),
        R("GET", "/api/v1/parties", "List companies", m.page_of(m.Party), query=(Q("search"), *PAGING), views=CASE_VIEWS),
        R("GET", "/api/v1/licences", "List licences", m.page_of(m.Licence), query=(Q("medicine_id"), Q("gov_status"), *PAGING), views=CASE_VIEWS),
        R("GET", "/api/v1/licences/{licence_id}", "A licence, mirrored from the government record", m.Licence, views=CASE_VIEWS),
        R("GET", "/api/v1/licences/{licence_id}/reliance-basis", "What this licence relied on", m.page_of(m.RelianceBasis), query=PAGING, views=CASE_VIEWS),
    ],
    "Cases": [
        R("GET", "/api/v1/cases", "Queue of cases", m.page_of(m.CaseSummary),
          query=(Q("status"), Q("assigned_to"), Q("case_type"), Q("environment", enum=("simulation", "live")), *PAGING), views=CASE_VIEWS),
        R("POST", "/api/v1/cases", "Open a case", m.CaseDetail, 201, body=m.CaseCreate, idempotent=True, actor="connector or simulation",
          description="Pins the case to the active pack version and knowledge snapshot."),
        R("GET", "/api/v1/cases/{case_id}", "Case summary", m.CaseDetail, views=CASE_VIEWS),
        R("POST", "/api/v1/cases/{case_id}/assign", "Assign a person", m.CaseDetail, body=m.AssignRequest, views=OFFICER, guarded=True,
          description="Refused for a declared conflict of interest."),
        R("GET", "/api/v1/cases/{case_id}/timeline", "Runs, comments, questions and decisions", m.page_of(m.TimelineEvent), query=PAGING, views=CASE_VIEWS),
        R("POST", "/api/v1/cases/{case_id}/sequences", "New documents from the company", m.Sequence, 201, body=m.SequenceCreate, idempotent=True, actor="connector or simulation",
          description="Re-runs only the checks that read changed documents or facts taken from them. Evidence changes void waivers and confirmations on those checks."),
        R("GET", "/api/v1/cases/{case_id}/documents", "The dossier's documents", m.page_of(m.DossierDocument), query=PAGING, views=CASE_VIEWS),
        R("GET", "/api/v1/dossier-documents/{document_id}/pages/{page}", "One dossier page", m.DossierPage, views=CASE_VIEWS),
        R("GET", "/api/v1/cases/{case_id}/reliance-basis", "What this case relies on", m.page_of(m.RelianceBasis), query=PAGING, views=CASE_VIEWS),
        R("POST", "/api/v1/cases/{case_id}/reliance-basis", "Link this case to a foreign approval", m.RelianceBasis, 201, body=m.RelianceBasisCreate, views=OFFICER, idempotent=True,
          description="Foreign notices find open cases through this link."),
    ],
    "Pipeline and checks": [
        R("POST", "/api/v1/cases/{case_id}/pipeline-runs", "Start a pipeline run", m.PipelineRun, 202, body=m.PipelineRunCreate, views=OFFICER, idempotent=True, conflict="A newer run is in progress."),
        R("GET", "/api/v1/pipeline-runs/{run_id}", "Stages and checks", m.PipelineRun, views=CASE_VIEWS),
        R("GET", "/api/v1/cases/{case_id}/checks", "Current check results", m.page_of(m.CheckResult), query=(Q("status", "One or more check statuses, comma separated."), Q("blocking", type="boolean"),
                 Q("history", "`current` (default) lists the results that count. `superseded` lists earlier generations and attempts, newest first. They are read-only, `is_current` is false, and each names its replacement in `current_result_id`.", enum=("current", "superseded")), *PAGING),
          views=CASE_VIEWS),
        R("GET", "/api/v1/check-results/{check_result_id}", "Result, finding, evidence and challenge", m.CheckDetail, views=CASE_VIEWS,
          description="Also opens a superseded result by its id. Its `is_current` is false."),
        R("GET", "/api/v1/check-results/{check_result_id}/trace", "Saved reasoning or rule trace", m.CheckTrace, views=CASE_VIEWS,
          description="Comes from what was saved when the check ran. If nothing was saved, `basis` is `not_saved`, and nothing is generated to fill the space."),
        R("POST", "/api/v1/check-results/{check_result_id}/evidence-opened", "Record that the evidence was opened", m.CheckDetail, views=REVIEWERS),
        R("POST", "/api/v1/check-results/{check_result_id}/confirm", "A person confirms", m.CheckDetail, body=m.ConfirmRequest, views=OFFICER, guarded=True, idempotent=True,
          request_example=CONFIRM_EXAMPLE, description="Refused if the evidence changed since this fingerprint was read. A later evidence change voids the confirmation."),
        R("POST", "/api/v1/check-results/{check_result_id}/waive", "Waive a held check", m.CheckDetail, body=m.WaiveRequest, views=REVIEWERS, guarded=True, idempotent=True,
          description="Needs a reason. Binding law needs a Decision Maker. A later evidence change voids the waiver."),
        R("POST", "/api/v1/check-results/{check_result_id}/recheck", "Re-run with a reviewer's comment as context", m.CheckResult, 202, body=m.RecheckRequest, views=REVIEWERS, idempotent=True,
          description="A new generation of the check. Results from older generations are stored and ignored."),
        R("GET", "/api/v1/cases/{case_id}/facts", "Extracted facts, with their evidence", m.page_of(m.FactValue), query=PAGING, views=CASE_VIEWS),
        R("GET", "/api/v1/cases/{case_id}/recommendations", "Every recommendation version", m.page_of(m.RecommendationVersion), query=PAGING, views=CASE_VIEWS),
        R("GET", "/api/v1/spot-checks", "Spot checks assigned to me", m.page_of(m.SpotCheck), query=(Q("status"), *PAGING), views=REVIEWERS + AUDITOR),
        R("POST", "/api/v1/spot-checks/{spot_check_id}/complete", "Agree or disagree with a passed check", m.SpotCheck, body=m.SpotCheckComplete, views=REVIEWERS),
    ],
    "Review and questions": [
        R("GET", "/api/v1/cases/{case_id}/threads", "Comment threads", m.page_of(m.Thread), query=(Q("status", enum=("open", "resolved")), *PAGING), views=CASE_VIEWS),
        R("POST", "/api/v1/cases/{case_id}/threads", "Comment on a check, page or clause", m.Thread, 201, body=m.ThreadCreate, views=REVIEWERS, idempotent=True,
          description="An open thread blocks promotion and signing."),
        R("POST", "/api/v1/threads/{thread_id}/comments", "Reply", m.Thread, 201, body=m.CommentCreate, views=REVIEWERS, idempotent=True),
        R("POST", "/api/v1/threads/{thread_id}/resolve", "Resolve", m.Thread, body=m.ThreadResolve, views=REVIEWERS),
        R("GET", "/api/v1/cases/{case_id}/questions", "Drafted and sent questions", m.page_of(m.Question), query=PAGING, views=CASE_VIEWS),
        R("POST", "/api/v1/cases/{case_id}/questions", "Write a question", m.Question, 201, body=m.QuestionCreate, views=OFFICER, idempotent=True),
        R("PATCH", "/api/v1/questions/{question_id}", "Edit a draft", m.Question, body=m.QuestionUpdate, views=OFFICER, conflict="Only a draft can be edited."),
        R("POST", "/api/v1/questions/{question_id}/send", "A person sends the question", m.Question, body=m.QuestionSend, views=OFFICER, idempotent=True, conflict="Already sent."),
        R("POST", "/api/v1/questions/{question_id}/close", "Close, including when a company stays silent", m.Question, body=m.QuestionClose, views=OFFICER),
    ],
    "Promote and decide": [
        R("GET", "/api/v1/cases/{case_id}/promotion-check", "What would block promotion now", m.PromotionCheck, views=OFFICER),
        R("POST", "/api/v1/cases/{case_id}/promote", "Promote to the Decision Maker", m.Promotion, 201, body=m.PromoteRequest, views=OFFICER, guarded=True, idempotent=True,
          description="Needs every blocking check clear, a current recommendation, and no open comments or company questions."),
        R("GET", "/api/v1/cases/{case_id}/signing-package", "What will be signed, with its fingerprint", m.SigningPackage, views=SIGNER),
        R("POST", "/api/v1/cases/{case_id}/decisions", "Sign a decision", m.Decision, 201, body=m.DecisionCreate, views=SIGNER, guarded=True, idempotent=True,
          request_example=SIGN_REQUEST_EXAMPLE,
          description="Every guard is re-tested in one transaction. The decision and its government proposal are written together. A person can always refuse or close a held case. An agent can never create a decision."),
        R("GET", "/api/v1/decisions", "List decisions", m.page_of(m.Decision),
          query=(Q("case_id"), Q("type", enum=("grant", "refusal", "close", "licence_action")), Q("state", enum=("signed", "on_hold", "reconfirmed", "revoked")),
                 Q("record_status", enum=("not_needed", "queued", "sent", "acknowledged", "unknown", "rejected")), *PAGING), views=CASE_VIEWS,
          description="Newest signature first."),
        R("GET", "/api/v1/decisions/{decision_id}", "Decision, signers, fingerprint and record status", m.Decision, views=CASE_VIEWS),
        R("POST", "/api/v1/decisions/{decision_id}/reconfirm", "Reconfirm after a suspension became known post-signing", m.Decision, body=m.DecisionHoldAction, views=SIGNER, guarded=True),
        R("POST", "/api/v1/decisions/{decision_id}/revoke", "Revoke after a suspension became known post-signing", m.Decision, body=m.DecisionHoldAction, views=SIGNER, guarded=True),
    ],
    "Government proposals": [
        R("GET", "/api/v1/proposals", "Proposals, by state", m.page_of(m.Proposal), query=(Q("state", enum=("queued", "sent", "acknowledged", "unknown", "rejected")), *PAGING), views=ADMIN + AUDITOR),
        R("GET", "/api/v1/proposals/{proposal_id}", "State, attempts and government reference", m.Proposal, views=ADMIN + AUDITOR + REVIEWERS),
        R("POST", "/api/v1/proposals/{proposal_id}/manual-ack", "Record an outcome by hand, for a portal with no API", m.Proposal, body=m.ManualAck, views=OFFICER, conflict="This proposal is already settled."),
        R("GET", "/api/v1/connections", "Government connections and their checks", m.page_of(m.Connection), query=PAGING, views=ADMIN + OWNER),
        R("POST", "/api/v1/connections", "Register a connection", m.Connection, 201, body=m.ConnectionCreate, views=ADMIN, idempotent=True),
        R("POST", "/api/v1/connections/{connection_id}/check", "Run conformance checks", m.Connection, views=ADMIN),
        R("POST", "/api/v1/connections/{connection_id}/approve", "Approve use", m.Connection, body=m.ConnectionApprove, views=OWNER, guarded=True),
    ],
    "Foreign watch": [
        R("GET", "/api/v1/watch/sources", "Sources and their health", m.page_of(m.WatchSource), query=PAGING, views=ADMIN + OFFICER),
        R("POST", "/api/v1/watch/sources", "Add a source", m.WatchSource, 201, body=m.WatchSourceCreate, views=ADMIN, idempotent=True),
        R("POST", "/api/v1/watch/sources/{source_id}/fetch", "Fetch now", m.FetchResult, views=ADMIN),
        R("GET", "/api/v1/watch/notices", "All notices, including unmatched ones", m.page_of(m.NoticeSummary), query=(Q("unmatched", type="boolean"), Q("severity"), *PAGING), views=CASE_VIEWS),
        R("GET", "/api/v1/watch/notices/{notice_id}", "Revisions, matches and explanation", m.NoticeDetail, views=CASE_VIEWS),
        R("GET", "/api/v1/watch/matches", "Matches waiting for a person", m.page_of(m.WatchMatch), query=(Q("status", enum=("open", "dismissed", "rereview_opened")), *PAGING), views=CASE_VIEWS),
        R("POST", "/api/v1/watch/matches/{match_id}/dismiss", "Dismiss, with a reason", m.WatchMatch, body=m.MatchDismiss, views=OFFICER),
        R("POST", "/api/v1/watch/matches/{match_id}/open-rereview", "Open a reliance re-review", m.CaseDetail, 201, body=m.OpenRereview, views=OFFICER, idempotent=True),
        R("GET", "/api/v1/watch/incidents", "Bursts grouped into one incident", m.page_of(m.WatchIncident), query=PAGING, views=CASE_VIEWS),
        R("GET", "/api/v1/watch/coverage", "Licences with no reliance link, and unhealthy sources", m.WatchCoverage, views=ADMIN + AUDITOR + OFFICER),
    ],
    "Notifications": [
        R("GET", "/api/v1/notifications", "My notifications", m.page_of(m.Notification), query=(Q("state"), *PAGING), views=ALL),
        R("POST", "/api/v1/notifications/{notification_id}/ack", "Acknowledge", m.Notification, views=ALL),
    ],
    "Knowledge Assistant": [
        R("POST", "/api/v1/assistant/sessions", "Start a chat, scoped to you, and to a case if you name one", m.AssistantSession, 201, body=m.AssistantSessionCreate, views=ALL,
          description="Without `case_id` the chat reads only the knowledge base, and any signed-in person may open it. With one, only a view that may see that case can."),
        R("POST", "/api/v1/assistant/sessions/{session_id}/turns", "One turn, streamed", None, body=TurnRequest, views=ALL, stream_schema="ServerAssistantEvent",
          extra_errors=((503, "The assistant isn't available. Saved traces still work."),),
          description="Only the person who started the chat can continue it. A draft comment is posted only when a person confirms it, through the threads API. Questions and answers are saved."),
    ],
    "Gaps": [
        R("GET", "/api/v1/workspaces/{workspace_id}/gap-signals", "Signals counted by code", m.page_of(m.GapSignal), query=PAGING, views=OWNER + AUDITOR),
        R("POST", "/api/v1/workspaces/{workspace_id}/gap-analyses", "Run the Gap Analyst", m.GapAnalysis, 202, body=m.GapAnalysisCreate, views=OWNER, idempotent=True),
        R("GET", "/api/v1/gap-analyses/{analysis_id}", "Analysis status", m.GapAnalysis, views=OWNER + AUDITOR),
        R("GET", "/api/v1/workspaces/{workspace_id}/gaps", "Gap candidates", m.page_of(m.Gap), query=(Q("status"), Q("kind", enum=("policy", "standards")), *PAGING), views=OWNER + AUDITOR),
        R("PATCH", "/api/v1/gaps/{gap_id}", "Review a candidate", m.Gap, body=m.GapReview, views=OWNER),
    ],
    "Platform Admin": [
        R("GET", "/api/v1/admin/agents/health", "Each agent's health", m.page_of(m.AgentHealthView), views=ADMIN),
        R("GET", "/api/v1/admin/agent-runs", "Runs, failures and retries. No dossier content", m.page_of(m.AgentRunView),
          query=(Q("agent"), Q("status"), Q("case_id"), *PAGING), views=ADMIN),
        R("GET", "/api/v1/admin/usage", "Tokens, gate blocks and failures, as totals", m.UsageReport, query=(Q("from", "Start of the period."), Q("to", "End of the period.")), views=ADMIN),
        R("GET", "/api/v1/demo", "Demo data status", m.DemoStatus, views=ADMIN, simulation_only=True),
        R("POST", "/api/v1/demo/reset", "Replace all data with the demo, in simulation only", m.DemoStatus, 202, body=m.DemoResetRequest, views=ADMIN, simulation_only=True),
    ],
    "Simulation": [
        R("GET", "/api/v1/scenarios", "Available scenarios", m.page_of(m.Scenario), query=PAGING, views=ADMIN_OWNER, simulation_only=True),
        R("GET", "/api/v1/simulations", "List simulation runs", m.page_of(m.Simulation),
          query=(Q("status", enum=("queued", "running", "waiting_for_event", "completed", "failed")), Q("scenario_id"), *PAGING), views=ADMIN_OWNER, simulation_only=True,
          description="Newest run first."),
        R("POST", "/api/v1/simulations", "Run a scenario", m.Simulation, 202, body=m.SimulationCreate, views=ADMIN_OWNER, simulation_only=True, idempotent=True,
          description="`gate: off` is allowed here only, for comparison runs. It's refused for live and shadow work."),
        R("GET", "/api/v1/simulations/{simulation_id}", "Status and outcome", m.Simulation, views=ADMIN_OWNER, simulation_only=True),
        R("POST", "/api/v1/simulations/{simulation_id}/events", "Deliver a staged event, such as a foreign notice", m.Simulation, 202, body=m.SimulationEvent, views=ADMIN_OWNER, simulation_only=True, idempotent=True),
        R("GET", "/api/v1/simulations/compare", "Compare a governed run with an ungoverned run", m.SimulationComparison, query=(Q("governed", required=True), Q("ungoverned", required=True)), views=ADMIN_OWNER, simulation_only=True),
    ],
    "Audit": [
        R("GET", "/api/v1/audit-events", "Filtered log", m.page_of(m.AuditEvent), query=(Q("case_id"), Q("actor_id"), Q("action"), Q("object_id"),
                                                                              Q("from", "Only events at or after this time (ISO 8601)."), Q("to", "Only events before this time (ISO 8601)."), *PAGING), views=AUDITOR),
        R("GET", "/api/v1/cases/{case_id}/reconstruct", "Everything that happened, from saved records", m.CaseReconstruction, views=AUDITOR),
        R("GET", "/api/v1/audit/verify", "Check that the log hasn't been altered", m.AuditVerification, views=AUDITOR),
    ],
    "Operations": [
        R("GET", "/health", "Liveness", Health, public=True),
        R("GET", "/health/details", "Database, agents, scheduler and worker", m.ServerHealthDetails, views=ADMIN),
        R("POST", "/internal/ticks", "Run scheduled work that is due", m.TickResult, body=m.TickRequest, actor="scheduler",
          description="Watch fetches, reminders and escalations, and re-attaching to unfinished runs. Name tasks to run only those. Sent with the service token."),
    ],
}

DESCRIPTION = """The API behind the PharmAlign web UI. The server owns all state. Agents read and reason, and a named person makes every decision.

**Conventions**

- **Tenant** comes from the session, never from a request.
- **Errors** are RFC 9457 problem JSON, with a stable `code`.
- **Guards.** An action that could be unsafe returns `409` listing every failed guard, not only the first.
- **Fingerprints.** Actions on evidence send back the `fingerprint` the person read. If anything changed, the action is refused.
- **Idempotency.** State-changing `POST`s accept `Idempotency-Key`.
- **Lists** use `cursor` and `limit`.
- **Views.** `x-allowed-views` lists the views that may call an operation. Separation-of-duty rules also check the person.
- **Simulation.** `x-simulation-only` operations are refused outside simulation.
- **Audit.** Every state change is logged. No operation can skip this.
"""

SERVER_API = ApiDoc(
    title="PharmAlign Server API",
    version=CONTRACT_VERSION,
    description=DESCRIPTION,
    routes=ROUTES,
    guard_model=m.GuardProblem,
    security_description="Session token. In simulation, View as switches the demo person. Operations that list no views take the service token only.",
    cookie_name="pharmalign_session",
    key_reuse_422=True,
    streams={"ServerAssistantEvent": TypeAdapter(ServerAssistantEvent)},
)

__all__ = ["CONFIRM_EXAMPLE", "SERVER_API", "ROUTES", "SIGN_REFUSED_EXAMPLE", "SIGN_REQUEST_EXAMPLE", "Identifier"]

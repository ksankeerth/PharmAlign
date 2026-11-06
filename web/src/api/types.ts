// Friendly names for the contract's schemas, plus generic helpers that read an
// operation's parameters, body and success response straight from `schema.ts`.
// Import model types from here, not from schema.ts, so screens stay readable.
import type { components, operations } from "./schema";
import type { OperationId } from "./operations";

/**
 * Response models with every field present. The server always serialises defaults, so
 * a list the contract marks optional (because it has a default) is always there.
 * Request bodies keep the contract's optional fields (see RequestBody).
 */
export type Complete<T> = T extends (infer U)[]
  ? Complete<U>[]
  : T extends object
    ? { [K in keyof T]-?: Complete<T[K]> }
    : T;
export type Schemas = { [K in keyof components["schemas"]]: Complete<components["schemas"][K]> };
export type { OperationId, View } from "./operations";

// ---------------------------------------------------------------- operation helpers

type Op<K extends OperationId> = operations[K];
type Clean<T> = [NonNullable<T>] extends [never] ? Record<never, never> : NonNullable<T>;

/** Path parameters of an operation, such as `{ case_id: string }`. */
export type PathParams<K extends OperationId> = Clean<Op<K>["parameters"]["path"]>;
/** Query parameters of an operation (all optional on list calls). */
export type QueryParams<K extends OperationId> = Clean<Op<K>["parameters"]["query"]>;
/** JSON request body of an operation, or `never` when it takes none. */
export type RequestBody<K extends OperationId> =
  NonNullable<Op<K>["requestBody"]> extends { content: { "application/json": infer B } } ? B : never;

type Responses<K extends OperationId> = Op<K>["responses"];
type SuccessCode<K extends OperationId> = Extract<keyof Responses<K>, 200 | 201 | 202 | 204>;
type BodyOf<R> = R extends { content: { "application/json": infer B } }
  ? B
  : R extends { content: { "text/event-stream": infer E } }
    ? E
    : void;
/** The success body of an operation (200, 201, 202 or 204). */
export type Ok<K extends OperationId> = Complete<BodyOf<Responses<K>[SuccessCode<K>]>>;

/** Everything a transport needs to make one call. */
export interface CallRequest<K extends OperationId = OperationId> {
  path?: PathParams<K>;
  query?: QueryParams<K>;
  /** JSON, or FormData for the one multipart route (a file upload). */
  body?: RequestBody<K> | FormData;
  idempotencyKey?: string;
  /** Sent as If-Match: the content hash the caller last read, for optimistic saves. */
  ifMatch?: string;
  /** Signs the call as this demo person with a bearer token, whatever the session cookie says. */
  asPerson?: string;
}

// ---------------------------------------------------------------- shared shapes

export type Problem = Schemas["Problem"];
export type GuardProblem = Schemas["GuardProblem"];
export type GuardResult = Schemas["GuardResult"];
export type Evidence = Schemas["Evidence"];
export type FactValue = Schemas["FactValue"];
export type Usage = Schemas["Usage"];
export type ModelCall = Schemas["ModelCall"];

// ---------------------------------------------------------------- identity
export type Me = Schemas["Me"];
export type DemoPerson = Schemas["DemoPerson"];
export type DemoStatus = Schemas["DemoStatus"];
export type Person = Schemas["Person"];
export type RoleGrant = Schemas["RoleGrant"];
export type RoleType = RoleGrant["role"];
export type Delegation = Schemas["Delegation"];
export type Conflict = Schemas["Conflict"];

// ---------------------------------------------------------------- setup
export type Workspace = Schemas["Workspace"];
export type WorkspaceSummary = Schemas["WorkspaceSummary"];
export type WorkspaceMode = Workspace["mode"];
export type SetupView = Schemas["SetupView"];
export type SetupStageStatus = Schemas["SetupStageStatus"];
export type SetupStage = SetupStageStatus["stage"];
export type ReadinessRun = Schemas["ReadinessRun"];
export type ReadinessCheck = Schemas["ReadinessCheck"];
export type SignOff = Schemas["SignOff"];

// ---------------------------------------------------------------- library
export type Document = Schemas["Document"];
export type DocumentDetail = Schemas["DocumentDetail"];
export type DocumentVersion = Schemas["DocumentVersion"];
export type LegalStatus = Schemas["Adoption"]["legal_status"];
export type IngestPreset = Schemas["IngestPreset"];
export type IngestProfile = Schemas["IngestProfile"];
export type IngestJob = Schemas["IngestJob"];
export type ChunkView = Schemas["ChunkView"];
export type QualityReport = Schemas["QualityReport"];
export type DocumentReview = Schemas["DocumentReview"];
export type Adoption = Schemas["Adoption"];
export type Slot = Schemas["Slot"];
export type Snapshot = Schemas["Snapshot"];
export type SnapshotDetail = Schemas["SnapshotDetail"];
export type KbHit = Schemas["KbHit"];
export type KbSearchResponse = Schemas["KbSearchResponse"];
export type RetrievalTestRun = Schemas["RetrievalTestRun"];

// ---------------------------------------------------------------- packs
export type PackDocument = Schemas["PackDocument"];
export type PackVersion = Schemas["PackVersion"];
export type PackVersionSummary = Schemas["PackVersionSummary"];
export type PackState = PackVersionSummary["state"];
export type PackValidation = Schemas["PackValidation"];
export type PackIssue = Schemas["PackIssue"];
export type PackDiff = Schemas["PackDiff"];
export type PackChange = Schemas["PackChange"];
export type CheckDefinition = Schemas["CheckDefinition"];
export type CaseTypeDefinition = Schemas["CaseType"];
export type RouteDefinition = Schemas["Route"];
export type WorkflowStep = Schemas["WorkflowStep"];
export type StepKind = WorkflowStep["kind"];
export type AgentAutonomy = WorkflowStep["agent_autonomy"];
export type RelianceRule = Schemas["RelianceRule"];
export type SourceRef = Schemas["SourceRef"];
export type FactKey = Schemas["FactKey"];

// ---------------------------------------------------------------- medicines and cases
export type Party = Schemas["Party"];
export type Medicine = Schemas["Medicine"];
export type Licence = Schemas["Licence"];
export type CaseSummary = Schemas["CaseSummary"];
export type CaseDetail = Schemas["CaseDetail"];
export type CaseStatus = CaseSummary["status"];
export type TimelineEvent = Schemas["TimelineEvent"];
export type Sequence = Schemas["Sequence"];
export type DossierDocument = Schemas["DossierDocument"];
export type DossierPage = Schemas["DossierPage"];
export type RelianceBasis = Schemas["RelianceBasis"];

// ---------------------------------------------------------------- pipeline and checks
export type PipelineRun = Schemas["PipelineRun"];
export type StageStatus = Schemas["StageStatus"];
export type CheckResult = Schemas["CheckResult"];
export type CheckStatus = CheckResult["status"];
export type CheckMethod = CheckResult["method"];
export type CheckDetail = Schemas["CheckDetail"];
export type AgentStatus = NonNullable<CheckDetail["agent_status"]>;
export type CheckTrace = Schemas["CheckTrace"];
export type GateDecisionView = Schemas["GateDecisionView"];
export type ToolCallView = Schemas["ToolCallView"];
export type RuleOutcome = Schemas["RuleOutcome"];
export type ChallengeSummary = Schemas["ChallengeSummary"];
export type WaiverView = Schemas["WaiverView"];
export type ConfirmationView = Schemas["ConfirmationView"];
export type RecommendationVersion = Schemas["RecommendationVersion"];
export type ConflictView = Schemas["ConflictView"];
export type SpotCheck = Schemas["SpotCheck"];

// ---------------------------------------------------------------- review
export type Thread = Schemas["Thread"];
export type ThreadTarget = Schemas["ThreadTarget"];
export type Comment = Schemas["Comment"];
export type Question = Schemas["Question"];

// ---------------------------------------------------------------- promote and decide
export type PromotionCheck = Schemas["PromotionCheck"];
export type Promotion = Schemas["Promotion"];
export type SigningPackage = Schemas["SigningPackage"];
export type ForeignStatus = Schemas["ForeignStatus"];
export type Decision = Schemas["Decision"];
export type DecisionType = Decision["type"];
export type Signer = Schemas["Signer"];
export type Proposal = Schemas["Proposal"];
export type Connection = Schemas["Connection"];

// ---------------------------------------------------------------- watch
export type WatchSource = Schemas["WatchSource"];
export type SourceHealth = WatchSource["health"];
export type FetchResult = Schemas["FetchResult"];
export type NoticeSummary = Schemas["NoticeSummary"];
export type NoticeDetail = Schemas["NoticeDetail"];
export type NoticeRevision = Schemas["NoticeRevision"];
export type WatchMatch = Schemas["WatchMatch"];
export type WatchIncident = Schemas["WatchIncident"];
export type WatchCoverage = Schemas["WatchCoverage"];
export type StagedNotice = Schemas["StagedNotice"];

// ---------------------------------------------------------------- notifications and assistant
export type Notification = Schemas["Notification"];
export type AssistantSession = Schemas["AssistantSession"];
export type AssistantEvent = Schemas["ServerAssistantEvent"];
export type AssistantCitation = Schemas["AssistantCitation"];
export type TurnRequest = Schemas["TurnRequest"];

// ---------------------------------------------------------------- gaps, admin, simulation, audit
export type GapSignal = Schemas["GapSignal"];
export type GapAnalysis = Schemas["GapAnalysis"];
export type Gap = Schemas["Gap"];
export type AgentHealthView = Schemas["AgentHealthView"];
export type AgentRunView = Schemas["AgentRunView"];
export type UsageReport = Schemas["UsageReport"];
export type Scenario = Schemas["Scenario"];
export type Simulation = Schemas["Simulation"];
export type SimulationEvent = Schemas["SimulationEvent"];
export type SimulationComparison = Schemas["SimulationComparison"];
export type AuditEvent = Schemas["AuditEvent"];
export type CaseReconstruction = Schemas["CaseReconstruction"];
export type AuditVerification = Schemas["AuditVerification"];
export type ServerHealthDetails = Schemas["ServerHealthDetails"];

/** A page of any list call. */
export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

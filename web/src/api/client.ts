// Typed client: one function per server operation, grouped as in the contract.
// The comment on each line is the contract's operationId. Types come from schema.ts,
// so a contract change shows up here as a type error after `npm run gen:api`.
//
// Usage:  import { api } from "../api";   const c = await api.getCase("CASE-1042");
// Errors: every function throws ApiError (see errors.ts). A refused action is a 409
//         whose `failed` lists every guard that failed.
import { call, stream } from "./transport";
import type { OperationId, PathParams, QueryParams, RequestBody } from "./types";

type Q<K extends OperationId> = QueryParams<K>;
type B<K extends OperationId> = RequestBody<K>;
type P<K extends OperationId> = PathParams<K>;
/** Optional Idempotency-Key for state-changing calls. */
export interface CallOptions {
  idempotencyKey?: string;
}

// ---------------------------------------------------------------- Identity
export const getMe = () => call("get_api_v1_me"); // get_api_v1_me
export const listDemoPeople = (query?: Q<"get_api_v1_demo_people">) => call("get_api_v1_demo_people", { query });
export const viewAs = (body: B<"post_api_v1_session_view_as">) => call("post_api_v1_session_view_as", { body });
export const setSessionWorkspace = (body: B<"post_api_v1_session_workspace">) => call("post_api_v1_session_workspace", { body });
export const listPeople = (query?: Q<"get_api_v1_people">) => call("get_api_v1_people", { query });
export const createPerson = (body: B<"post_api_v1_people">) => call("post_api_v1_people", { body });
export const getPerson = (person_id: string) => call("get_api_v1_people_person_id", { path: { person_id } });
export const createRoleGrant = (body: B<"post_api_v1_role_grants">) => call("post_api_v1_role_grants", { body });
export const deleteRoleGrant = (grant_id: string) => call("delete_api_v1_role_grants_grant_id", { path: { grant_id } });
export const listDelegations = (query?: Q<"get_api_v1_delegations">) => call("get_api_v1_delegations", { query });
export const createDelegation = (body: B<"post_api_v1_delegations">) => call("post_api_v1_delegations", { body });
export const listConflicts = (query?: Q<"get_api_v1_conflicts">) => call("get_api_v1_conflicts", { query });
export const declareConflict = (body: B<"post_api_v1_conflicts">) => call("post_api_v1_conflicts", { body });

// ---------------------------------------------------------------- Setup
export const createWorkspace = (body: B<"post_api_v1_workspaces">) => call("post_api_v1_workspaces", { body });
export const listWorkspaces = (query?: Q<"get_api_v1_workspaces">) => call("get_api_v1_workspaces", { query });
export const getWorkspace = (workspace_id: string) => call("get_api_v1_workspaces_workspace_id", { path: { workspace_id } });
export const getSetup = (workspace_id: string) => call("get_api_v1_workspaces_workspace_id_setup", { path: { workspace_id } });
export const completeSetupStage = (
  path: P<"post_api_v1_workspaces_workspace_id_setup_stages_stage_complete">,
  body: B<"post_api_v1_workspaces_workspace_id_setup_stages_stage_complete">,
) => call("post_api_v1_workspaces_workspace_id_setup_stages_stage_complete", { path, body });
export const startReadinessRun = (workspace_id: string) =>
  call("post_api_v1_workspaces_workspace_id_readiness_runs", { path: { workspace_id } });
export const getReadiness = (workspace_id: string) =>
  call("get_api_v1_workspaces_workspace_id_readiness", { path: { workspace_id } });
export const signOffWorkspace = (workspace_id: string, body: B<"post_api_v1_workspaces_workspace_id_sign_off">) =>
  call("post_api_v1_workspaces_workspace_id_sign_off", { path: { workspace_id }, body });

// ---------------------------------------------------------------- Document library
export const listDocuments = (query?: Q<"get_api_v1_documents">) => call("get_api_v1_documents", { query });
export const createDocument = (body: B<"post_api_v1_documents">) => call("post_api_v1_documents", { body });
export const getDocument = (document_id: string) => call("get_api_v1_documents_document_id", { path: { document_id } });
export const addDocumentVersion = (document_id: string, body: B<"post_api_v1_documents_document_id_versions"> | FormData) =>
  call("post_api_v1_documents_document_id_versions", { path: { document_id }, body });
export const getDocumentVersion = (version_id: string) =>
  call("get_api_v1_document_versions_version_id", { path: { version_id } });
export const listIngestPresets = () => call("get_api_v1_ingest_presets");
export const startIngestJob = (version_id: string, body: B<"post_api_v1_document_versions_version_id_ingest_jobs">) =>
  call("post_api_v1_document_versions_version_id_ingest_jobs", { path: { version_id }, body });
export const getIngestJob = (job_id: string) => call("get_api_v1_ingest_jobs_job_id", { path: { job_id } });
export const cancelIngestJob = (job_id: string) => call("delete_api_v1_ingest_jobs_job_id", { path: { job_id } });
export const listChunks = (version_id: string, query?: Q<"get_api_v1_document_versions_version_id_chunks">) =>
  call("get_api_v1_document_versions_version_id_chunks", { path: { version_id }, query });
export const getQualityReport = (version_id: string) =>
  call("get_api_v1_document_versions_version_id_quality", { path: { version_id } });
export const reviewDocumentVersion = (version_id: string, body: B<"post_api_v1_document_versions_version_id_review">) =>
  call("post_api_v1_document_versions_version_id_review", { path: { version_id }, body });
export const listAdoptions = (workspace_id: string, query?: Q<"get_api_v1_workspaces_workspace_id_adoptions">) =>
  call("get_api_v1_workspaces_workspace_id_adoptions", { path: { workspace_id }, query });
export const setAdoption = (
  path: P<"put_api_v1_workspaces_workspace_id_adoptions_version_id">,
  body: B<"put_api_v1_workspaces_workspace_id_adoptions_version_id">,
) => call("put_api_v1_workspaces_workspace_id_adoptions_version_id", { path, body });
export const listSlots = (workspace_id: string, query?: Q<"get_api_v1_workspaces_workspace_id_slots">) =>
  call("get_api_v1_workspaces_workspace_id_slots", { path: { workspace_id }, query });
export const updateSlot = (slot_id: string, body: B<"patch_api_v1_slots_slot_id">) =>
  call("patch_api_v1_slots_slot_id", { path: { slot_id }, body });
export const createSnapshot = (workspace_id: string, body: B<"post_api_v1_workspaces_workspace_id_kb_snapshots">) =>
  call("post_api_v1_workspaces_workspace_id_kb_snapshots", { path: { workspace_id }, body });
export const listSnapshots = (workspace_id: string, query?: Q<"get_api_v1_workspaces_workspace_id_kb_snapshots">) =>
  call("get_api_v1_workspaces_workspace_id_kb_snapshots", { path: { workspace_id }, query });
export const getSnapshot = (snapshot_id: string) => call("get_api_v1_kb_snapshots_snapshot_id", { path: { snapshot_id } });
export const searchKb = (body: B<"post_api_v1_kb_search">) => call("post_api_v1_kb_search", { body });
export const startRetrievalTest = (body: B<"post_api_v1_retrieval_tests">) => call("post_api_v1_retrieval_tests", { body });
export const getRetrievalTest = (test_id: string) => call("get_api_v1_retrieval_tests_test_id", { path: { test_id } });

// ---------------------------------------------------------------- Country packs
export const listPackVersions = (workspace_id: string, query?: Q<"get_api_v1_workspaces_workspace_id_packs">) =>
  call("get_api_v1_workspaces_workspace_id_packs", { path: { workspace_id }, query });
export const createPackDraft = (workspace_id: string, body: B<"post_api_v1_workspaces_workspace_id_packs">) =>
  call("post_api_v1_workspaces_workspace_id_packs", { path: { workspace_id }, body });
export const getPackVersion = (version_id: string) => call("get_api_v1_pack_versions_version_id", { path: { version_id } });
/** `ifMatch` is the content hash you last read; a mismatch is refused with 412. */
export const savePackDraft = (version_id: string, body: B<"put_api_v1_pack_versions_version_id">, ifMatch?: string) =>
  call("put_api_v1_pack_versions_version_id", { path: { version_id }, body, ifMatch });
export const validatePack = (version_id: string) =>
  call("post_api_v1_pack_versions_version_id_validate", { path: { version_id } });
export const simulatePack = (version_id: string, body: B<"post_api_v1_pack_versions_version_id_simulate">) =>
  call("post_api_v1_pack_versions_version_id_simulate", { path: { version_id }, body });
export const approvePack = (version_id: string, body: B<"post_api_v1_pack_versions_version_id_approve">) =>
  call("post_api_v1_pack_versions_version_id_approve", { path: { version_id }, body });
export const activatePack = (version_id: string) =>
  call("post_api_v1_pack_versions_version_id_activate", { path: { version_id } });
export const getPackDiff = (version_id: string, query?: Q<"get_api_v1_pack_versions_version_id_diff">) =>
  call("get_api_v1_pack_versions_version_id_diff", { path: { version_id }, query });
export const repinCase = (case_id: string, body: B<"post_api_v1_cases_case_id_repin">) =>
  call("post_api_v1_cases_case_id_repin", { path: { case_id }, body });

// ---------------------------------------------------------------- Medicines
export const listMedicines = (query?: Q<"get_api_v1_medicines">) => call("get_api_v1_medicines", { query });
export const getMedicine = (medicine_id: string) => call("get_api_v1_medicines_medicine_id", { path: { medicine_id } });
export const listParties = (query?: Q<"get_api_v1_parties">) => call("get_api_v1_parties", { query });
export const listLicences = (query?: Q<"get_api_v1_licences">) => call("get_api_v1_licences", { query });
export const getLicence = (licence_id: string) => call("get_api_v1_licences_licence_id", { path: { licence_id } });
export const listLicenceRelianceBasis = (licence_id: string) =>
  call("get_api_v1_licences_licence_id_reliance_basis", { path: { licence_id } });

// ---------------------------------------------------------------- Cases
export const listCases = (query?: Q<"get_api_v1_cases">) => call("get_api_v1_cases", { query });
export const createCase = (body: B<"post_api_v1_cases">) => call("post_api_v1_cases", { body });
export const getCase = (case_id: string) => call("get_api_v1_cases_case_id", { path: { case_id } });
export const assignCase = (case_id: string, body: B<"post_api_v1_cases_case_id_assign">) =>
  call("post_api_v1_cases_case_id_assign", { path: { case_id }, body });
export const getCaseTimeline = (case_id: string, query?: Q<"get_api_v1_cases_case_id_timeline">) =>
  call("get_api_v1_cases_case_id_timeline", { path: { case_id }, query });
export const addSequence = (case_id: string, body: B<"post_api_v1_cases_case_id_sequences">) =>
  call("post_api_v1_cases_case_id_sequences", { path: { case_id }, body });
export const listCaseDocuments = (case_id: string, query?: Q<"get_api_v1_cases_case_id_documents">) =>
  call("get_api_v1_cases_case_id_documents", { path: { case_id }, query });
export const getDossierPage = (document_id: string, page: number | string) =>
  call("get_api_v1_dossier_documents_document_id_pages_page", { path: { document_id, page: String(page) } });
export const listCaseRelianceBasis = (case_id: string) =>
  call("get_api_v1_cases_case_id_reliance_basis", { path: { case_id } });
export const addCaseRelianceBasis = (case_id: string, body: B<"post_api_v1_cases_case_id_reliance_basis">) =>
  call("post_api_v1_cases_case_id_reliance_basis", { path: { case_id }, body });

// ---------------------------------------------------------------- Pipeline and checks
export const startPipelineRun = (case_id: string, body: B<"post_api_v1_cases_case_id_pipeline_runs">) =>
  call("post_api_v1_cases_case_id_pipeline_runs", { path: { case_id }, body });
export const getPipelineRun = (run_id: string) => call("get_api_v1_pipeline_runs_run_id", { path: { run_id } });
export const listCaseChecks = (case_id: string, query?: Q<"get_api_v1_cases_case_id_checks">) =>
  call("get_api_v1_cases_case_id_checks", { path: { case_id }, query });
export const getCheck = (check_result_id: string) =>
  call("get_api_v1_check_results_check_result_id", { path: { check_result_id } });
export const getCheckTrace = (check_result_id: string) =>
  call("get_api_v1_check_results_check_result_id_trace", { path: { check_result_id } });
export const recordEvidenceOpened = (check_result_id: string) =>
  call("post_api_v1_check_results_check_result_id_evidence_opened", { path: { check_result_id } });
export const confirmCheck = (check_result_id: string, body: B<"post_api_v1_check_results_check_result_id_confirm">, o?: CallOptions) =>
  call("post_api_v1_check_results_check_result_id_confirm", { path: { check_result_id }, body, ...o });
export const waiveCheck = (check_result_id: string, body: B<"post_api_v1_check_results_check_result_id_waive">, o?: CallOptions) =>
  call("post_api_v1_check_results_check_result_id_waive", { path: { check_result_id }, body, ...o });
export const recheckCheck = (check_result_id: string, body: B<"post_api_v1_check_results_check_result_id_recheck">) =>
  call("post_api_v1_check_results_check_result_id_recheck", { path: { check_result_id }, body });
export const listCaseFacts = (case_id: string, query?: Q<"get_api_v1_cases_case_id_facts">) =>
  call("get_api_v1_cases_case_id_facts", { path: { case_id }, query });
export const listRecommendations = (case_id: string, query?: Q<"get_api_v1_cases_case_id_recommendations">) =>
  call("get_api_v1_cases_case_id_recommendations", { path: { case_id }, query });
export const listSpotChecks = (query?: Q<"get_api_v1_spot_checks">) => call("get_api_v1_spot_checks", { query });
export const completeSpotCheck = (spot_check_id: string, body: B<"post_api_v1_spot_checks_spot_check_id_complete">) =>
  call("post_api_v1_spot_checks_spot_check_id_complete", { path: { spot_check_id }, body });

// ---------------------------------------------------------------- Review and questions
export const listThreads = (case_id: string, query?: Q<"get_api_v1_cases_case_id_threads">) =>
  call("get_api_v1_cases_case_id_threads", { path: { case_id }, query });
export const createThread = (case_id: string, body: B<"post_api_v1_cases_case_id_threads">) =>
  call("post_api_v1_cases_case_id_threads", { path: { case_id }, body });
export const addComment = (thread_id: string, body: B<"post_api_v1_threads_thread_id_comments">) =>
  call("post_api_v1_threads_thread_id_comments", { path: { thread_id }, body });
export const resolveThread = (thread_id: string, body: B<"post_api_v1_threads_thread_id_resolve">) =>
  call("post_api_v1_threads_thread_id_resolve", { path: { thread_id }, body });
export const listQuestions = (case_id: string, query?: Q<"get_api_v1_cases_case_id_questions">) =>
  call("get_api_v1_cases_case_id_questions", { path: { case_id }, query });
export const createQuestion = (case_id: string, body: B<"post_api_v1_cases_case_id_questions">) =>
  call("post_api_v1_cases_case_id_questions", { path: { case_id }, body });
export const updateQuestion = (question_id: string, body: B<"patch_api_v1_questions_question_id">) =>
  call("patch_api_v1_questions_question_id", { path: { question_id }, body });
export const sendQuestion = (question_id: string, body: B<"post_api_v1_questions_question_id_send">) =>
  call("post_api_v1_questions_question_id_send", { path: { question_id }, body });
export const closeQuestion = (question_id: string, body: B<"post_api_v1_questions_question_id_close">) =>
  call("post_api_v1_questions_question_id_close", { path: { question_id }, body });

// ---------------------------------------------------------------- Promote and decide
export const getPromotionCheck = (case_id: string) =>
  call("get_api_v1_cases_case_id_promotion_check", { path: { case_id } });
export const promoteCase = (case_id: string, body: B<"post_api_v1_cases_case_id_promote">, o?: CallOptions) =>
  call("post_api_v1_cases_case_id_promote", { path: { case_id }, body, ...o });
export const getSigningPackage = (case_id: string) =>
  call("get_api_v1_cases_case_id_signing_package", { path: { case_id } });
export const signDecision = (case_id: string, body: B<"post_api_v1_cases_case_id_decisions">, o?: CallOptions) =>
  call("post_api_v1_cases_case_id_decisions", { path: { case_id }, body, ...o });
export const listDecisions = (query?: Q<"get_api_v1_decisions">) => call("get_api_v1_decisions", { query });
export const getDecision = (decision_id: string) => call("get_api_v1_decisions_decision_id", { path: { decision_id } });
export const reconfirmDecision = (decision_id: string, body: B<"post_api_v1_decisions_decision_id_reconfirm">) =>
  call("post_api_v1_decisions_decision_id_reconfirm", { path: { decision_id }, body });
export const revokeDecision = (decision_id: string, body: B<"post_api_v1_decisions_decision_id_revoke">) =>
  call("post_api_v1_decisions_decision_id_revoke", { path: { decision_id }, body });

// ---------------------------------------------------------------- Government proposals and connections
export const listProposals = (query?: Q<"get_api_v1_proposals">) => call("get_api_v1_proposals", { query });
export const getProposal = (proposal_id: string) => call("get_api_v1_proposals_proposal_id", { path: { proposal_id } });
export const recordManualAck = (proposal_id: string, body: B<"post_api_v1_proposals_proposal_id_manual_ack">) =>
  call("post_api_v1_proposals_proposal_id_manual_ack", { path: { proposal_id }, body });
export const listConnections = (query?: Q<"get_api_v1_connections">) => call("get_api_v1_connections", { query });
export const createConnection = (body: B<"post_api_v1_connections">) => call("post_api_v1_connections", { body });
export const checkConnection = (connection_id: string) =>
  call("post_api_v1_connections_connection_id_check", { path: { connection_id } });
export const approveConnection = (connection_id: string, body: B<"post_api_v1_connections_connection_id_approve">) =>
  call("post_api_v1_connections_connection_id_approve", { path: { connection_id }, body });

// ---------------------------------------------------------------- Foreign watch
export const listWatchSources = (query?: Q<"get_api_v1_watch_sources">) => call("get_api_v1_watch_sources", { query });
export const createWatchSource = (body: B<"post_api_v1_watch_sources">) => call("post_api_v1_watch_sources", { body });
export const fetchWatchSource = (source_id: string) =>
  call("post_api_v1_watch_sources_source_id_fetch", { path: { source_id } });
export const listNotices = (query?: Q<"get_api_v1_watch_notices">) => call("get_api_v1_watch_notices", { query });
export const getNotice = (notice_id: string) => call("get_api_v1_watch_notices_notice_id", { path: { notice_id } });
export const listMatches = (query?: Q<"get_api_v1_watch_matches">) => call("get_api_v1_watch_matches", { query });
export const dismissMatch = (match_id: string, body: B<"post_api_v1_watch_matches_match_id_dismiss">) =>
  call("post_api_v1_watch_matches_match_id_dismiss", { path: { match_id }, body });
export const openRereview = (match_id: string, body: B<"post_api_v1_watch_matches_match_id_open_rereview">) =>
  call("post_api_v1_watch_matches_match_id_open_rereview", { path: { match_id }, body });
export const listIncidents = (query?: Q<"get_api_v1_watch_incidents">) => call("get_api_v1_watch_incidents", { query });
export const getWatchCoverage = () => call("get_api_v1_watch_coverage");

// ---------------------------------------------------------------- Notifications
export const listNotifications = (query?: Q<"get_api_v1_notifications">) => call("get_api_v1_notifications", { query });
export const ackNotification = (notification_id: string) =>
  call("post_api_v1_notifications_notification_id_ack", { path: { notification_id } });

// ---------------------------------------------------------------- Knowledge Assistant
/** Without a case the chat reads only the knowledge base. */
export const startAssistantSession = (body: B<"post_api_v1_assistant_sessions">) => call("post_api_v1_assistant_sessions", { body });
/** One turn, streamed. Iterate with `for await (const ev of api.sendAssistantTurn(...))`. */
export const sendAssistantTurn = (session_id: string, body: B<"post_api_v1_assistant_sessions_session_id_turns">) =>
  stream("post_api_v1_assistant_sessions_session_id_turns", { path: { session_id }, body });

// ---------------------------------------------------------------- Gaps
export const listGapSignals = (workspace_id: string, query?: Q<"get_api_v1_workspaces_workspace_id_gap_signals">) =>
  call("get_api_v1_workspaces_workspace_id_gap_signals", { path: { workspace_id }, query });
export const startGapAnalysis = (workspace_id: string, body: B<"post_api_v1_workspaces_workspace_id_gap_analyses">) =>
  call("post_api_v1_workspaces_workspace_id_gap_analyses", { path: { workspace_id }, body });
export const getGapAnalysis = (analysis_id: string) => call("get_api_v1_gap_analyses_analysis_id", { path: { analysis_id } });
export const listGaps = (workspace_id: string, query?: Q<"get_api_v1_workspaces_workspace_id_gaps">) =>
  call("get_api_v1_workspaces_workspace_id_gaps", { path: { workspace_id }, query });
export const reviewGap = (gap_id: string, body: B<"patch_api_v1_gaps_gap_id">) =>
  call("patch_api_v1_gaps_gap_id", { path: { gap_id }, body });

// ---------------------------------------------------------------- Platform Admin
export const getAgentHealth = () => call("get_api_v1_admin_agents_health");
export const listAgentRuns = (query?: Q<"get_api_v1_admin_agent_runs">) => call("get_api_v1_admin_agent_runs", { query });
export const getUsage = (query?: Q<"get_api_v1_admin_usage">) => call("get_api_v1_admin_usage", { query });
/** `asPerson` keeps the status readable while a reset has cleared the session cookie. */
export const getDemo = (asPerson?: string) => call("get_api_v1_demo", { asPerson });
export const resetDemo = (body: B<"post_api_v1_demo_reset">) => call("post_api_v1_demo_reset", { body });

// ---------------------------------------------------------------- Simulation (simulation-only)
export const listScenarios = (query?: Q<"get_api_v1_scenarios">) => call("get_api_v1_scenarios", { query });
export const listSimulations = (query?: Q<"get_api_v1_simulations">) => call("get_api_v1_simulations", { query });
export const startSimulation = (body: B<"post_api_v1_simulations">) => call("post_api_v1_simulations", { body });
export const getSimulation = (simulation_id: string) =>
  call("get_api_v1_simulations_simulation_id", { path: { simulation_id } });
export const deliverSimulationEvent = (simulation_id: string, body: B<"post_api_v1_simulations_simulation_id_events">) =>
  call("post_api_v1_simulations_simulation_id_events", { path: { simulation_id }, body });
export const compareSimulations = (query?: Q<"get_api_v1_simulations_compare">) =>
  call("get_api_v1_simulations_compare", { query });

// ---------------------------------------------------------------- Audit
export const listAuditEvents = (query?: Q<"get_api_v1_audit_events">) => call("get_api_v1_audit_events", { query });
export const reconstructCase = (case_id: string) => call("get_api_v1_cases_case_id_reconstruct", { path: { case_id } });
export const verifyAuditLog = () => call("get_api_v1_audit_verify");

// ---------------------------------------------------------------- Operations
export const getHealth = () => call("get_health");
export const getHealthDetails = () => call("get_health_details");
export const runTicks = (body: B<"post_internal_ticks">) => call("post_internal_ticks", { body });

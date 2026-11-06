// The route table: every screen, in one place. Each page is lazy-loaded from its
// area's folder, so screens can be built in parallel without touching each other.
//
// To add a screen: create the page in its area folder (default export), add one row
// here, and add a nav item in src/app/navigation.ts if it should appear in a view.
// `sample` is a concrete URL with demo ids, used by the smoke test; `as` is a demo
// person who can open it.
import { type ComponentType, lazy, type LazyExoticComponent } from "react";
import type { View } from "./api/types";

export type Area = "cases" | "owner" | "admin" | "audit" | "watch" | "notifications" | "common";

export interface RouteSpec {
  /** Absolute path. Case-page tabs are relative to /cases/:caseId (see CASE_TABS). */
  path: string;
  title: string;
  area: Area;
  views: View[];
  page: LazyExoticComponent<ComponentType>;
  sample?: string;
  as?: string;
}

const CO_DM_AU: View[] = ["case_officer", "decision_maker", "auditor"];

export const ROUTES: RouteSpec[] = [
  // ---------------------------------------------------------------- Case Officer and Decision Maker
  { path: "/cases", title: "Case queue", area: "cases", views: CO_DM_AU, page: lazy(() => import("./pages/cases/CaseQueuePage")), as: "p-alex" },
  { path: "/cases/spot-checks", title: "Spot checks", area: "cases", views: CO_DM_AU, page: lazy(() => import("./pages/cases/SpotChecksPage")), as: "p-alex" },
  { path: "/cases/decisions", title: "Decisions", area: "cases", views: CO_DM_AU, page: lazy(() => import("./pages/cases/DecisionsPage")), as: "p-dana" },
  { path: "/cases/decisions/:decisionId", title: "Decision", area: "cases", views: CO_DM_AU, page: lazy(() => import("./pages/cases/DecisionDetailPage")), sample: "/cases/decisions/dec-1037", as: "p-dana" },
  { path: "/cases/:caseId/promote", title: "Promotion", area: "cases", views: ["case_officer"], page: lazy(() => import("./pages/cases/PromotionPage")), sample: "/cases/CASE-1043/promote", as: "p-alex" },
  { path: "/cases/:caseId/signing", title: "Signing package", area: "cases", views: ["decision_maker"], page: lazy(() => import("./pages/cases/SigningPackagePage")), sample: "/cases/CASE-1044/signing", as: "p-dana" },

  // ---------------------------------------------------------------- Process Owner
  { path: "/owner/packs", title: "Packs and versions", area: "owner", views: ["process_owner"], page: lazy(() => import("./pages/owner/PacksPage")), as: "p-sam" },
  { path: "/owner/packs/:versionId", title: "Pack editor", area: "owner", views: ["process_owner"], page: lazy(() => import("./pages/owner/PackEditorPage")), sample: "/owner/packs/pv-ae-4", as: "p-sam" },
  { path: "/owner/packs/:versionId/validate", title: "Pack validation", area: "owner", views: ["process_owner"], page: lazy(() => import("./pages/owner/PackValidationPage")), sample: "/owner/packs/pv-ae-4/validate", as: "p-sam" },
  { path: "/owner/packs/:versionId/approve", title: "Approve and activate", area: "owner", views: ["process_owner"], page: lazy(() => import("./pages/owner/PackApprovalPage")), sample: "/owner/packs/pv-ae-4/approve", as: "p-priya" },
  { path: "/owner/packs/:versionId/diff", title: "Pack diff", area: "owner", views: ["process_owner"], page: lazy(() => import("./pages/owner/PackDiffPage")), sample: "/owner/packs/pv-ae-4/diff", as: "p-priya" },
  { path: "/owner/documents", title: "Document reviews", area: "owner", views: ["process_owner"], page: lazy(() => import("./pages/owner/DocumentReviewsPage")), as: "p-sam" },
  { path: "/owner/adoptions", title: "Adoptions", area: "owner", views: ["process_owner"], page: lazy(() => import("./pages/owner/AdoptionsPage")), as: "p-sam" },
  { path: "/owner/slots", title: "Required slots", area: "owner", views: ["process_owner", "platform_admin"], page: lazy(() => import("./pages/owner/SlotsPage")), as: "p-sam" },
  { path: "/owner/gaps", title: "Gaps", area: "owner", views: ["process_owner"], page: lazy(() => import("./pages/owner/GapsPage")), as: "p-sam" },
  { path: "/owner/sign-off", title: "Sign-off", area: "owner", views: ["process_owner"], page: lazy(() => import("./pages/owner/SignOffPage")), as: "p-sam" },

  // ---------------------------------------------------------------- Platform Admin
  { path: "/admin/setup", title: "Setup tracker", area: "admin", views: ["platform_admin"], page: lazy(() => import("./pages/admin/SetupPage")), as: "p-jordan" },
  { path: "/admin/library", title: "Document library", area: "admin", views: ["platform_admin"], page: lazy(() => import("./pages/admin/LibraryPage")), as: "p-jordan" },
  { path: "/admin/library/:documentId", title: "Document", area: "admin", views: ["platform_admin"], page: lazy(() => import("./pages/admin/DocumentDetailPage")), sample: "/admin/library/E-S25", as: "p-jordan" },
  { path: "/admin/library/versions/:versionId/ingest", title: "Ingestion", area: "admin", views: ["platform_admin"], page: lazy(() => import("./pages/admin/IngestionPage")), sample: "/admin/library/versions/B-S01-v1/ingest", as: "p-jordan" },
  { path: "/admin/library/versions/:versionId/quality", title: "Quality and chunks", area: "admin", views: ["platform_admin"], page: lazy(() => import("./pages/admin/QualityPage")), sample: "/admin/library/versions/E-S25-v1/quality", as: "p-jordan" },
  { path: "/admin/kb-search", title: "Knowledge search", area: "admin", views: ["platform_admin"], page: lazy(() => import("./pages/admin/KbSearchPage")), as: "p-jordan" },
  { path: "/admin/agents", title: "Agent health", area: "admin", views: ["platform_admin"], page: lazy(() => import("./pages/admin/AgentHealthPage")), as: "p-jordan" },
  { path: "/admin/agent-runs", title: "Agent runs", area: "admin", views: ["platform_admin"], page: lazy(() => import("./pages/admin/AgentRunsPage")), as: "p-jordan" },
  { path: "/admin/connections", title: "Connections", area: "admin", views: ["platform_admin"], page: lazy(() => import("./pages/admin/ConnectionsPage")), as: "p-jordan" },
  { path: "/admin/watch-sources", title: "Watch sources", area: "admin", views: ["platform_admin"], page: lazy(() => import("./pages/admin/WatchSourcesPage")), as: "p-jordan" },
  { path: "/admin/simulations", title: "Simulations", area: "admin", views: ["platform_admin", "process_owner"], page: lazy(() => import("./pages/admin/SimulationsPage")), as: "p-jordan" },
  { path: "/admin/simulations/compare", title: "Governed vs ungoverned", area: "admin", views: ["platform_admin", "process_owner"], page: lazy(() => import("./pages/admin/ComparisonPage")), as: "p-jordan" },
  { path: "/admin/people", title: "People and roles", area: "admin", views: ["platform_admin"], page: lazy(() => import("./pages/admin/PeoplePage")), as: "p-jordan" },
  { path: "/admin/demo", title: "Demo", area: "admin", views: ["platform_admin"], page: lazy(() => import("./pages/admin/DemoPage")), as: "p-jordan" },

  // ---------------------------------------------------------------- Auditor
  { path: "/audit", title: "Audit log", area: "audit", views: ["auditor"], page: lazy(() => import("./pages/audit/AuditLogPage")), as: "p-robin" },
  { path: "/audit/cases", title: "Case reconstruction", area: "audit", views: ["auditor"], page: lazy(() => import("./pages/audit/ReconstructionIndexPage")), as: "p-robin" },
  { path: "/audit/cases/:caseId", title: "Reconstruction", area: "audit", views: ["auditor"], page: lazy(() => import("./pages/audit/ReconstructionPage")), sample: "/audit/cases/CASE-1046", as: "p-robin" },
  { path: "/audit/verify", title: "Log verification", area: "audit", views: ["auditor"], page: lazy(() => import("./pages/audit/VerifyPage")), as: "p-robin" },

  // ---------------------------------------------------------------- Foreign watch
  { path: "/watch/notices", title: "Foreign notices", area: "watch", views: CO_DM_AU, page: lazy(() => import("./pages/watch/NoticesPage")), as: "p-alex" },
  { path: "/watch/notices/:noticeId", title: "Notice", area: "watch", views: CO_DM_AU, page: lazy(() => import("./pages/watch/NoticeDetailPage")), sample: "/watch/notices/n-501", as: "p-alex" },
  { path: "/watch/matches", title: "Matches", area: "watch", views: CO_DM_AU, page: lazy(() => import("./pages/watch/MatchesPage")), as: "p-alex" },
  { path: "/watch/incidents", title: "Incidents", area: "watch", views: CO_DM_AU, page: lazy(() => import("./pages/watch/IncidentsPage")), as: "p-alex" },
  { path: "/watch/coverage", title: "Coverage", area: "watch", views: ["case_officer", "auditor", "platform_admin"], page: lazy(() => import("./pages/watch/CoveragePage")), as: "p-alex" },

  // ---------------------------------------------------------------- Everyone
  { path: "/notifications", title: "Notifications", area: "notifications", views: ["platform_admin", "process_owner", "case_officer", "decision_maker", "auditor"], page: lazy(() => import("./pages/notifications/NotificationsPage")), as: "p-alex" },
  { path: "/knowledge/:documentId", title: "Cited source", area: "common", views: ["platform_admin", "process_owner", "case_officer", "decision_maker", "auditor"], page: lazy(() => import("./pages/common/KnowledgeSourcePage")), sample: "/knowledge/E-S25?version=E-S25-v1&anchor=E-S25:v1:c0412", as: "p-alex" },
  { path: "/ui-kit", title: "UI kit", area: "common", views: ["platform_admin", "process_owner", "case_officer", "decision_maker", "auditor"], page: lazy(() => import("./pages/common/UiKitPage")), as: "p-alex" },
];

/** The case page: a layout with tabs. Paths are relative to /cases/:caseId. */
export const CASE_LAYOUT = lazy(() => import("./pages/cases/CaseLayout"));

export const CASE_TABS: (Omit<RouteSpec, "path"> & { path: string; index?: boolean })[] = [
  { path: "", index: true, title: "Overview", area: "cases", views: CO_DM_AU, page: lazy(() => import("./pages/cases/CaseOverviewPage")), sample: "/cases/CASE-1042", as: "p-alex" },
  { path: "checks", title: "Checks", area: "cases", views: CO_DM_AU, page: lazy(() => import("./pages/cases/CaseChecksPage")), sample: "/cases/CASE-1042/checks", as: "p-alex" },
  { path: "checks/:checkResultId", title: "Check", area: "cases", views: CO_DM_AU, page: lazy(() => import("./pages/cases/CheckDetailPage")), sample: "/cases/CASE-1042/checks/cr-1042-qual-07", as: "p-alex" },
  { path: "evidence", title: "Evidence", area: "cases", views: CO_DM_AU, page: lazy(() => import("./pages/cases/CaseEvidencePage")), sample: "/cases/CASE-1047/evidence", as: "p-alex" },
  { path: "threads", title: "Review threads", area: "cases", views: CO_DM_AU, page: lazy(() => import("./pages/cases/CaseThreadsPage")), sample: "/cases/CASE-1042/threads", as: "p-alex" },
  { path: "questions", title: "Questions", area: "cases", views: CO_DM_AU, page: lazy(() => import("./pages/cases/CaseQuestionsPage")), sample: "/cases/CASE-1042/questions", as: "p-alex" },
  { path: "timeline", title: "Timeline", area: "cases", views: CO_DM_AU, page: lazy(() => import("./pages/cases/CaseTimelinePage")), sample: "/cases/CASE-1048/timeline", as: "p-alex" },
];

export const NOT_FOUND = lazy(() => import("./pages/common/NotFoundPage"));

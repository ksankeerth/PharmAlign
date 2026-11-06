// Human labels for contract enums. Screens show these, never raw slugs.
import type { CaseStatus, CheckStatus, RoleType, View } from "../api/types";

export const CHECK_STATUS_LABEL: Record<CheckStatus, string> = {
  pending: "Pending",
  running: "Running",
  pass: "Pass",
  look: "Look",
  person_confirms: "Person confirms",
  confirmed: "Confirmed",
  held: "Held",
  waived: "Waived",
  error: "Error",
  stale: "Stale",
  skipped: "Skipped",
};

export const CHECK_STATUS_HINT: Record<CheckStatus, string> = {
  pending: "Waiting to run.",
  running: "Running now.",
  pass: "Clear. Passed by a rule, or by an agent allowed to pass it alone.",
  look: "Something differs. A person should look.",
  person_confirms: "An agent may not pass this alone. A person confirms.",
  confirmed: "Clear. A named person confirmed it.",
  held: "Blocks the file. Held for an officer, never rejected.",
  waived: "Clear while the waiver is valid. A named person waived it with a reason.",
  error: "The check could not run. Not a finding about the file.",
  stale: "The evidence changed. It will re-run.",
  skipped: "Not run, because an earlier stage is held or it does not apply.",
};

export const CASE_STATUS_LABEL: Record<CaseStatus, string> = {
  intake: "Intake",
  in_pipeline: "Checks running",
  held: "Held",
  with_company: "With company",
  in_review: "In review",
  promoted: "Awaiting signature",
  signed: "Signed",
  recording: "Recording",
  closed: "Closed",
};

export const VIEW_LABEL: Record<View, string> = {
  platform_admin: "Platform Admin",
  process_owner: "Process Owner",
  case_officer: "Case Officer",
  decision_maker: "Decision Maker",
  auditor: "Auditor",
};

export const ROLE_LABEL: Record<RoleType, string> = {
  platform_admin: "Platform Admin",
  authority_admin: "Authority Admin",
  process_owner: "Process Owner",
  knowledge_steward: "Knowledge Steward",
  intake_officer: "Intake Officer",
  case_manager: "Case Manager",
  assessor: "Assessor",
  inspector: "Inspector",
  lab_analyst: "Lab Analyst",
  committee_secretary: "Committee Secretary",
  decision_maker: "Decision Maker",
  watch_officer: "Watch Officer",
  auditor: "Auditor",
  executive_viewer: "Executive Viewer",
  applicant_contact: "Applicant Contact",
  peer_regulator: "Peer Regulator",
};

export const RECOMMENDATION_LABEL: Record<string, string> = {
  proceed: "Proceed",
  hold_ask_company: "Hold and ask the company",
  hold_officer_review: "Hold for officer review",
  change_route: "Change route",
  return_incomplete: "Return as incomplete",
};

export const GUARD_LABEL: Record<string, string> = {
  case_ready_for_promotion: "Case is in review",
  blocking_checks_clear: "Blocking checks clear",
  current_recommendation: "Recommendation is current",
  no_open_threads: "No open review comments",
  no_open_questions: "No open company questions",
  fingerprint_matches: "Nothing changed since you read it",
  case_promoted: "Promoted by a case officer",
  signer_did_not_assess: "Signer did not assess the case",
  signer_did_not_promote: "Signer did not promote the case",
  no_declared_conflict: "No declared conflict of interest",
  foreign_status_current: "Foreign status is current",
  recommendation_version_current: "Signing the current recommendation",
  case_not_frozen: "Case is not frozen",
  check_waivable: "Check can be waived",
  waiver_permitted: "You may waive this check",
  check_needs_person: "Check waits for a person",
  not_own_change: "Approved by a second person",
  pack_simulated: "Simulated before approval",
  pack_valid: "Validation passes",
  pack_approved: "Approved by a second person",
  not_own_upload: "Reviewed by someone other than the uploader",
  quality_gate_passed: "Quality gate allows this",
  full_needs_approved_version: "Version is approved",
  no_blockers: "No open blockers",
  conformance_passing: "Conformance tests pass",
  not_already_done: "Stage is not already done",
  no_run_in_progress: "No run in progress",
  readiness_current: "Readiness checks are current",
  readiness_passed: "Readiness checks pass",
};

/** Turns a slug into words: `hold_ask_company` -> `Hold ask company`. */
export function humanize(slug: string | null | undefined): string {
  if (!slug) return "";
  const s = slug.replace(/[_-]+/g, " ").trim();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** Names for dossier facts whose keys do not read well as words. */
const FACT_LABEL: Record<string, string> = {
  long_term_temp_c: "Long-term temperature (C)",
  long_term_rh_pct: "Long-term humidity (% RH)",
  long_term_months: "Long-term data (months)",
  leaflet_dose_en: "Leaflet dose (English)",
  leaflet_dose_ar: "Leaflet dose (Arabic, English rendering)",
};

export function factLabel(key: string): string {
  return FACT_LABEL[key] ?? humanize(key);
}

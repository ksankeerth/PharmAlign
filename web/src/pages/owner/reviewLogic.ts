// What stands between a person and a document review decision. Same rules the server
// enforces, so the screen can show them before anyone presses a button.
import type { DocumentReview, DocumentVersion } from "../../api/types";

export type ReviewDecision = DocumentReview["decision"];

export const DECISION_LABEL: Record<ReviewDecision, string> = {
  approved: "Approve",
  approved_with_notes: "Approve with notes",
  needs_update: "Needs an update",
  rejected: "Reject",
};

/** What to tell the person after the decision is saved. */
export const DECISION_DONE: Record<ReviewDecision, string> = {
  approved: "approved",
  approved_with_notes: "approved with notes",
  needs_update: "sent back for an update",
  rejected: "rejected",
};

export const REVIEW_STATUS_LABEL: Record<DocumentVersion["review_status"], string> = {
  unreviewed: "Waiting for review",
  approved: "Approved",
  approved_with_notes: "Approved with notes",
  rejected: "Rejected",
  needs_update: "Needs an update",
};

export const needsNotes = (d: ReviewDecision) => d !== "approved";
export const isApproving = (d: ReviewDecision) => d === "approved" || d === "approved_with_notes";

export interface ReviewGuard {
  guard: string;
  passed: boolean;
  detail: string;
}

export function reviewReadiness(version: Pick<DocumentVersion, "added_by" | "quality_status"> & { held?: boolean }, personId: string | null | undefined, decision: ReviewDecision, notes: string): ReviewGuard[] {
  const own = version.added_by === personId;
  const qualityOk = !isApproving(decision) || version.quality_status !== "fail";
  const notesOk = !needsNotes(decision) || notes.trim().length > 0;
  return [
    { guard: "not_own_upload", passed: !own, detail: own ? "You added this version. Another person must review it." : "Reviewed by someone other than the uploader." },
    { guard: "quality_gate_passed", passed: qualityOk, detail: qualityOk ? "Quality gate allows this decision." : "The quality gate failed. Re-ingest before approving." },
    { guard: "notes_given", passed: notesOk, detail: notesOk ? (needsNotes(decision) ? "Notes given." : "No notes needed to approve.") : "This decision needs notes, so the next person knows why." },
    ...(version.held ? [{ guard: "hold_needs_notes", passed: decision !== "approved", detail: decision !== "approved" ? "Held for a person, who is looking now." : "Hidden text with an instruction was found. Approve with notes that say what you checked." }] : []),
  ];
}

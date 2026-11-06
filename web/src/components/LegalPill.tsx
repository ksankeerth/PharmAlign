// LegalPill: a knowledge document's legal status as icon + colour + text. A status is shown wherever a source is
// cited, so nobody reads a superseded or draft text as a requirement.
import type { LegalStatus } from "../api/types";
import { Pill, type Tone } from "./StatusPill";

export const LEGAL_LABEL: Record<LegalStatus, string> = {
  in_force: "In force",
  draft: "Draft",
  superseded: "Superseded",
  review_needed: "Review needed",
  not_applicable: "Not applicable",
};
const LEGAL_TONE: Record<LegalStatus, Tone> = { in_force: "pass", draft: "look", superseded: "held", review_needed: "look", not_applicable: "neutral" };

export function LegalPill({ status, size = "sm" }: { status: LegalStatus | null | undefined; size?: "sm" | "md" }) {
  if (!status) return <span className="muted small">Not adopted</span>;
  return (
    <Pill tone={LEGAL_TONE[status]} icon={status === "in_force" ? "check" : status === "superseded" ? "ban" : "eye"} size={size}>
      {LEGAL_LABEL[status]}
    </Pill>
  );
}

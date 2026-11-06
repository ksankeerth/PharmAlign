// Shared bits for the Platform Admin screens: stat tiles, label maps, pills.
import "./admin.css";
import type { ReactNode } from "react";
import type { SourceHealth } from "../../api/types";
import { Pill, type Tone } from "../../components";
import { useApi } from "../../hooks/useApi";
import { api } from "../../api";
import { useSession } from "../../app/session";

export function Metric({ label, value, note, tone }: { label: string; value: ReactNode; note?: ReactNode; tone?: Tone }) {
  return (
    <div className="adm-metric" style={tone && tone !== "neutral" ? { borderColor: `var(--${tone === "accent" ? "accent-line" : `${tone}-line`})` } : undefined}>
      <div className="adm-metric-label">{label}</div>
      <div className="adm-metric-value">{value}</div>
      {note ? <div className="adm-metric-note">{note}</div> : null}
    </div>
  );
}

export { LEGAL_LABEL, LegalPill } from "../../components";

export const REVIEW_LABEL: Record<string, string> = {
  unreviewed: "Unreviewed",
  approved: "Approved",
  approved_with_notes: "Approved with notes",
  rejected: "Rejected",
  needs_update: "Needs update",
};

export function ReviewPill({ status }: { status: string }) {
  const tone: Tone = status === "approved" ? "pass" : status === "approved_with_notes" ? "look" : status === "rejected" ? "held" : status === "needs_update" ? "look" : "neutral";
  return (
    <Pill tone={tone} icon={status.startsWith("approved") ? "check" : status === "rejected" ? "x" : "circle-dashed"} size="sm">
      {REVIEW_LABEL[status] ?? status}
    </Pill>
  );
}

export function QualityPill({ status }: { status: string | null | undefined }) {
  if (!status) return <span className="muted small">Not run</span>;
  const tone: Tone = status === "pass" ? "pass" : status === "attention" ? "look" : "held";
  return (
    <Pill tone={tone} icon={status === "pass" ? "check" : status === "attention" ? "eye" : "x"} size="sm">
      {status === "pass" ? "Passed" : status === "attention" ? "Needs attention" : "Failed"}
    </Pill>
  );
}

export const HEALTH_LABEL: Record<SourceHealth, string> = {
  healthy: "Healthy",
  failing: "Failing",
  zero_yield: "Zero yield",
  layout_changed: "Layout changed",
  manual: "Manual",
  unchecked: "Not checked yet",
};

export function HealthPill({ health }: { health: SourceHealth }) {
  const tone: Tone = health === "healthy" ? "pass" : health === "manual" ? "neutral" : health === "unchecked" ? "neutral" : "held";
  const icon = health === "healthy" ? "check" : health === "manual" ? "user" : health === "unchecked" ? "circle-dashed" : "alert-triangle";
  return (
    <Pill tone={tone} icon={icon} size="sm">
      {HEALTH_LABEL[health]}
    </Pill>
  );
}

/** Person id to name, from the people list. */
export function usePeopleNames(): (id: string | null | undefined) => string {
  const { data } = useApi(() => api.listPeople({ limit: 200 }), []);
  const { me } = useSession();
  const map = new Map((data?.items ?? []).map((p) => [p.id, p.display_name]));
  // The Platform Admin belongs to another workspace, so a country they set up doesn't list them.
  if (me) map.set(me.person_id, me.display_name);
  return (id) => (id ? (map.get(id) ?? id) : "Nobody");
}

export const DOC_TYPE_LABEL = (t: string) => t.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

export function sum(n: number[]): number {
  return n.reduce((a, b) => a + b, 0);
}

/** Learns the id of a thrown value's message, for toasts. */
export function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

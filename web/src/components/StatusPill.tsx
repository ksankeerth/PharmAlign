// StatusPill: a check's status as icon + colour + text, never colour alone.
// Covers every CheckStatus. Pill is the generic version for any other status
// (case status, pack state, record status) with a chosen tone and icon.
import type { CaseStatus, CheckStatus } from "../api/types";
import { CASE_STATUS_LABEL, CHECK_STATUS_HINT, CHECK_STATUS_LABEL } from "../lib/labels";
import { Icon, type IconName } from "./Icon";

export type Tone = "pass" | "look" | "held" | "error" | "accent" | "neutral";

const CHECK: Record<CheckStatus, { tone: Tone; icon: IconName }> = {
  pending: { tone: "neutral", icon: "circle-dashed" },
  running: { tone: "accent", icon: "loader" },
  pass: { tone: "pass", icon: "check" },
  look: { tone: "look", icon: "eye" },
  person_confirms: { tone: "accent", icon: "user-check" },
  confirmed: { tone: "pass", icon: "check-double" },
  held: { tone: "held", icon: "pause" },
  waived: { tone: "neutral", icon: "ban" },
  error: { tone: "error", icon: "alert-octagon" },
  stale: { tone: "look", icon: "refresh" },
  skipped: { tone: "neutral", icon: "skip" },
};

export interface PillProps {
  tone: Tone;
  icon?: IconName;
  children: React.ReactNode;
  title?: string;
  size?: "sm" | "md";
  spin?: boolean;
}

export function Pill({ tone, icon, children, title, size = "md", spin }: PillProps) {
  return (
    <span className={`pill pill-${tone} pill-${size}`} title={title}>
      {icon ? <Icon name={icon} size={size === "sm" ? 12 : 13} spin={spin} /> : null}
      <span>{children}</span>
    </span>
  );
}

export interface StatusPillProps {
  status: CheckStatus;
  size?: "sm" | "md";
  /** Show the explanation as a tooltip (default true). */
  hint?: boolean;
}

export function StatusPill({ status, size = "md", hint = true }: StatusPillProps) {
  const s = CHECK[status];
  return (
    <Pill tone={s.tone} icon={s.icon} size={size} title={hint ? CHECK_STATUS_HINT[status] : undefined} spin={status === "running"}>
      {CHECK_STATUS_LABEL[status]}
    </Pill>
  );
}

/** Tone for a check status, for screens that colour other things to match. */
export function checkTone(status: CheckStatus): Tone {
  return CHECK[status].tone;
}

const CASE: Record<CaseStatus, { tone: Tone; icon: IconName }> = {
  intake: { tone: "neutral", icon: "inbox" },
  in_pipeline: { tone: "accent", icon: "loader" },
  held: { tone: "held", icon: "pause" },
  with_company: { tone: "neutral", icon: "send" },
  in_review: { tone: "accent", icon: "eye" },
  promoted: { tone: "look", icon: "pen" },
  signed: { tone: "pass", icon: "stamp" },
  recording: { tone: "pass", icon: "upload" },
  closed: { tone: "neutral", icon: "check" },
};

/** CaseStatusPill: a case's status, with the same icon + colour + text rule. */
export function CaseStatusPill({ status, size = "md" }: { status: CaseStatus; size?: "sm" | "md" }) {
  const s = CASE[status];
  return (
    <Pill tone={s.tone} icon={s.icon} size={size} spin={status === "in_pipeline"}>
      {CASE_STATUS_LABEL[status]}
    </Pill>
  );
}

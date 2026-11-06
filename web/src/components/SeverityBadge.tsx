// SeverityBadge: how serious something is. Accepts check severities (blocking,
// warning, info), notice and notification severities (urgent, review, info) and
// pack issue severities (error, warning). Icon + text, compact.
import { humanize } from "../lib/labels";
import { Icon, type IconName } from "./Icon";
import type { Tone } from "./StatusPill";

const MAP: Record<string, { tone: Tone; icon: IconName }> = {
  blocking: { tone: "held", icon: "lock" },
  urgent: { tone: "held", icon: "alert-triangle" },
  error: { tone: "held", icon: "alert-octagon" },
  critical: { tone: "held", icon: "alert-octagon" },
  warning: { tone: "look", icon: "alert-triangle" },
  review: { tone: "look", icon: "eye" },
  high: { tone: "held", icon: "arrow-up" },
  medium: { tone: "look", icon: "circle" },
  low: { tone: "neutral", icon: "arrow-down" },
  info: { tone: "neutral", icon: "info" },
};

export function SeverityBadge({ severity, label }: { severity: string; label?: string }) {
  const m = MAP[severity] ?? { tone: "neutral" as Tone, icon: "info" as IconName };
  return (
    <span className={`sev sev-${m.tone}`}>
      <Icon name={m.icon} size={12} />
      {label ?? humanize(severity)}
    </span>
  );
}

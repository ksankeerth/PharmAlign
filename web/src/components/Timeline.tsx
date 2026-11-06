// Timeline: a vertical list of events (case timeline, audit trail, run history).
// Each item shows who acted (person, agent or system, with a distinct icon), when,
// and what happened. Pass `resolveActor` to turn ids into names.
import type { ReactNode } from "react";
import { formatDateTime, formatRelative } from "../lib/format";
import { Icon, type IconName } from "./Icon";

export interface TimelineItem {
  id: string;
  at: string;
  summary: ReactNode;
  actorType: "person" | "agent" | "system" | "connector";
  actorId: string;
  /** Extra content under the summary, such as a before and after. */
  detail?: ReactNode;
  tone?: "default" | "pass" | "look" | "held" | "error";
}

const ACTOR_ICON: Record<TimelineItem["actorType"], IconName> = { person: "user", agent: "cpu", system: "settings", connector: "plug" };

export function Timeline({ items, resolveActor, empty }: { items: TimelineItem[]; resolveActor?: (type: TimelineItem["actorType"], id: string) => string; empty?: ReactNode }) {
  if (!items.length) return <>{empty ?? <p className="muted">No events yet.</p>}</>;
  return (
    <ol className="timeline">
      {items.map((it) => (
        <li key={it.id} className={`timeline-item timeline-${it.tone ?? "default"}`}>
          <span className={`timeline-dot timeline-dot-${it.actorType}`} aria-hidden="true">
            <Icon name={ACTOR_ICON[it.actorType]} size={12} />
          </span>
          <div className="timeline-body">
            <div className="timeline-summary">{it.summary}</div>
            <div className="timeline-meta">
              <span>{resolveActor ? resolveActor(it.actorType, it.actorId) : it.actorId}</span>
              <span aria-hidden="true">·</span>
              <span className="actor-kind">{it.actorType}</span>
              <span aria-hidden="true">·</span>
              <time dateTime={it.at} title={formatDateTime(it.at)}>
                {formatRelative(it.at)}
              </time>
            </div>
            {it.detail ? <div className="timeline-detail">{it.detail}</div> : null}
          </div>
        </li>
      ))}
    </ol>
  );
}

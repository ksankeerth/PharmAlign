// Timeline: everything that happened on the case, by day: runs, comments, questions,
// recommendation versions and decisions, with who acted (person, agent or system).
import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../../api";
import type { TimelineEvent } from "../../api/types";
import { Button, EmptyState, ErrorState, SkeletonLines, Timeline, type TimelineItem } from "../../components";
import { useApi } from "../../hooks/useApi";
import { formatDate } from "../../lib/format";
import { useNames } from "./shared";

type Category = "all" | "runs" | "comments" | "questions" | "recommendations" | "decisions" | "foreign";

const CATEGORIES: { id: Category; label: string }[] = [
  { id: "all", label: "Everything" },
  { id: "runs", label: "Runs and checks" },
  { id: "comments", label: "Comments" },
  { id: "questions", label: "Questions" },
  { id: "recommendations", label: "Recommendations" },
  { id: "decisions", label: "Decisions" },
  { id: "foreign", label: "Foreign notices" },
];

function categoryOf(kind: string): Exclude<Category, "all"> {
  if (kind === "comment" || kind === "thread_resolved") return "comments";
  if (kind.startsWith("question") || kind === "notice_drafted") return "questions";
  if (kind === "recommendation") return "recommendations";
  if (kind === "promoted" || kind.startsWith("decision") || kind.startsWith("record") || kind === "applicant_told") return "decisions";
  if (kind === "foreign_notice" || kind === "rereview_opened") return "foreign";
  return "runs";
}

function toneOf(kind: string): TimelineItem["tone"] {
  if (kind === "check_held" || kind === "gate_refused" || kind === "decision_on_hold" || kind === "record_rejected" || kind === "decision_revoked") return "held";
  if (kind === "check_error") return "error";
  if (kind === "check_look" || kind === "challenge_disputed" || kind === "record_unknown" || kind === "foreign_notice" || kind === "no_source") return "look";
  if (kind === "decision_signed" || kind === "record_acknowledged" || kind === "promoted") return "pass";
  return "default";
}

function hrefOf(caseId: string, ref: string | null): string | null {
  if (!ref) return null;
  if (ref.startsWith("cr-")) return `/cases/${caseId}/checks/${ref}`;
  if (ref.startsWith("th-")) return `/cases/${caseId}/threads`;
  if (ref.startsWith("q-")) return `/cases/${caseId}/questions`;
  if (ref.startsWith("dec-")) return `/cases/decisions/${ref}`;
  if (ref.startsWith("d-")) return `/cases/${caseId}/evidence?doc=${encodeURIComponent(ref)}&page=1`;
  if (/^n-\d/.test(ref)) return `/watch/notices/${ref}`;
  if (ref.startsWith("gap-")) return null;
  return null;
}

export default function CaseTimelinePage() {
  const { caseId = "" } = useParams();
  const names = useNames();
  const { data, error, reload } = useApi(() => api.getCaseTimeline(caseId), [caseId]);
  const [category, setCategory] = useState<Category>("all");
  const [newestFirst, setNewestFirst] = useState(false);

  const days = useMemo(() => {
    const events: TimelineEvent[] = (data?.items ?? []).filter((e) => category === "all" || categoryOf(e.kind) === category);
    const ordered = newestFirst ? [...events].reverse() : events;
    const map = new Map<string, TimelineEvent[]>();
    for (const e of ordered) {
      const day = formatDate(e.at);
      map.set(day, [...(map.get(day) ?? []), e]);
    }
    return [...map.entries()];
  }, [data, category, newestFirst]);

  if (error) return <ErrorState error={error} onRetry={reload} />;
  if (!data) return <SkeletonLines lines={8} />;

  return (
    <div className="stack">
      <div className="row-between">
        <div className="cw-chips" role="group" aria-label="Filter events">
          {CATEGORIES.map((c) => (
            <button key={c.id} type="button" className="cw-chip" aria-pressed={category === c.id} onClick={() => setCategory(c.id)}>
              {c.label}
            </button>
          ))}
        </div>
        <Button size="sm" variant="ghost" icon="sort" onClick={() => setNewestFirst((v) => !v)}>
          {newestFirst ? "Newest first" : "Oldest first"}
        </Button>
      </div>
      {days.length === 0 ? (
        <EmptyState title="Nothing of this kind yet" icon="history">
          Pick another filter to see other events.
        </EmptyState>
      ) : null}
      {days.map(([day, events]) => (
        <section key={day} aria-label={day}>
          <h2 className="cw-day">{day}</h2>
          <Timeline
            resolveActor={(_, id) => names(id)}
            items={events.map((e) => {
              const href = hrefOf(caseId, e.object_ref);
              return {
                id: e.id,
                at: e.at,
                actorType: e.actor_type,
                actorId: e.actor_id,
                tone: toneOf(e.kind),
                summary: href ? <Link to={href}>{e.summary}</Link> : e.summary,
              };
            })}
          />
        </section>
      ))}
    </div>
  );
}

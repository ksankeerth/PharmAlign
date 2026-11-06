// Shared by the foreign watch screens: match pills, target links, and the dismiss and
// re-review actions (both need a reason; only a Case Officer may use them).
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, type CaseDetail, type WatchMatch } from "../../api";
import { Button, ConfirmModal, KeyValue, Pill, useToast } from "../../components";
import { humanize } from "../../lib/labels";
import "./watch.css";

export function ConfidencePill({ c }: { c: WatchMatch["match_confidence"] }) {
  if (c === "low") return <Pill tone="look" icon="user-check" size="sm" title="Low confidence. Code never closes this match; a person decides.">Low confidence</Pill>;
  if (c === "medium") return <Pill tone="look" size="sm" icon="circle">Medium confidence</Pill>;
  return <Pill tone="accent" size="sm" icon="check">High confidence</Pill>;
}

export function MatchStatusPill({ s }: { s: WatchMatch["status"] }) {
  if (s === "open") return <Pill tone="look" icon="clock" size="sm">Waiting for a person</Pill>;
  if (s === "dismissed") return <Pill tone="neutral" icon="ban" size="sm">Dismissed</Pill>;
  return <Pill tone="pass" icon="check" size="sm">Re-review opened</Pill>;
}

export function TargetLabel({ m }: { m: WatchMatch }) {
  if (m.target_type === "none" || !m.target_id) return <span className="strong">No match <span className="muted">(stored only)</span></span>;
  if (m.target_type === "case") return <Link to={`/cases/${m.target_id}`}><span className="muted">Case </span>{m.target_id}</Link>;
  if (m.target_type === "licence") return <span><span className="muted">Licence </span><code>{m.target_id}</code></span>;
  return <span><span className="muted">Site </span>{m.target_id}</span>;
}

export function Basis({ items }: { items: string[] }) {
  return (
    <ul className="basis-list" aria-label="Basis for the match">
      {items.map((b) => (
        <li key={b}>{b}</li>
      ))}
    </ul>
  );
}

/** Dismiss and Open re-review for one open match. Renders nothing if the match is decided. */
export function MatchActions({ match, noticeTitle, canAct, onRereview }: { match: WatchMatch; noticeTitle?: string; canAct: boolean; onRereview?: (c: CaseDetail, m: WatchMatch) => void }) {
  const [mode, setMode] = useState<"dismiss" | "rereview" | null>(null);
  const toast = useToast();
  if (match.status !== "open") {
    if (!match.decided_by) return null;
    return (
      <span className="muted xsmall">
        {match.reason}
        {match.rereview_case_id ? <> <Link to={`/cases/${match.rereview_case_id}`}>Open {match.rereview_case_id}</Link></> : null}
      </span>
    );
  }
  if (!canAct) return <span className="muted xsmall">A Case Officer decides</span>;
  const noTarget = match.target_type === "none" || match.target_type === "site" || !match.target_id;
  const summary = (
    <KeyValue
      items={[
        { label: "Notice", value: noticeTitle ?? match.notice_id },
        { label: "Matched to", value: <TargetLabel m={match} /> },
        { label: "Confidence", value: humanize(match.match_confidence) },
      ]}
    />
  );
  return (
    <>
      <div className="row" style={{ justifyContent: "flex-end", minWidth: 120 }}>
        <Button size="sm" variant="secondary" onClick={() => setMode("rereview")} disabled={noTarget} title={noTarget ? "This match has no licence or case to re-review." : undefined}>
          Open re-review
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setMode("dismiss")}>
          Dismiss
        </Button>
      </div>
      <ConfirmModal
        open={mode === "dismiss"}
        onClose={() => setMode(null)}
        title="Dismiss this match"
        summary={summary}
        confirmLabel="Dismiss match"
        requireReason
        reasonLabel="Why does this notice not affect it?"
        onConfirm={(reason) => api.dismissMatch(match.id, { reason })}
        onDone={() => toast.success("Match dismissed. The reason is saved in the audit log.")}
      />
      <ConfirmModal
        open={mode === "rereview"}
        onClose={() => setMode(null)}
        title="Open a reliance re-review"
        summary={
          <>
            {summary}
            <p className="small muted" style={{ marginBottom: 0 }}>
              This opens a case that re-runs the checks that relied on the foreign approval, and links it to this match.
            </p>
          </>
        }
        confirmLabel="Open re-review"
        requireReason
        reasonLabel="What should the re-review look at?"
        onConfirm={async (reason) => {
          const c = await api.openRereview(match.id, { reason });
          onRereview?.(c, match);
          return c;
        }}
        onDone={() => toast.success("Re-review opened.")}
      />
    </>
  );
}

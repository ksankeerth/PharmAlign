// My notifications: severity, delivery state, acknowledgement, and for urgent alerts the
// time left to acknowledge before they escalate. The bell in the top bar reloads after
// each acknowledgement because both read the same list.
import { useEffect, useState } from "react";
import { api, type Notification } from "../../api";
import { Button, ButtonLink, Callout, EmptyState, ErrorState, Field, PageHeader, Panel, Pill, Select, SeverityBadge, SkeletonLines, useToast } from "../../components";
import { useApi, useMutation } from "../../hooks/useApi";
import { formatDateTime, formatRelative } from "../../lib/format";
import { humanize } from "../../lib/labels";
import "../watch/watch.css";
import "./notifications.css";

type StateFilter = "all" | "open" | "urgent" | "acknowledged";

const STATE: Record<Notification["state"], { tone: "neutral" | "accent" | "error" | "pass" | "held"; label: string; icon: "clock" | "send" | "alert-octagon" | "check" | "arrow-up" }> = {
  pending: { tone: "neutral", label: "Pending", icon: "clock" },
  sent: { tone: "accent", label: "Sent", icon: "send" },
  failed: { tone: "error", label: "Delivery failed", icon: "alert-octagon" },
  acknowledged: { tone: "pass", label: "Acknowledged", icon: "check" },
  escalated: { tone: "held", label: "Escalated", icon: "arrow-up" },
};

function useNow(intervalMs: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(t);
  }, [intervalMs]);
  return now;
}

function clock(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${m}:${String(sec).padStart(2, "0")}`;
}

/** Time left to acknowledge. Counts down each second, then says how late it is. */
export function AckCountdown({ due, escalated }: { due: string; escalated: boolean }) {
  const now = useNow(1000);
  const left = Date.parse(due) - now;
  if (left <= 0 || escalated)
    return (
      <span className="countdown countdown-late" title={`Acknowledgement was due ${formatDateTime(due)}`}>
        {escalated ? "Not acknowledged in time. Escalated." : `Overdue by ${clock(-left)}`}
      </span>
    );
  return (
    <span>
      Acknowledge within <span className="countdown" title={`Due ${formatDateTime(due)}`}>{clock(left)}</span>
      <span className="visually-hidden"> (due {formatDateTime(due)})</span>
    </span>
  );
}

export default function NotificationsPage() {
  const toast = useToast();
  const [filter, setFilter] = useState<StateFilter>("all");
  const { data, error, loading, reload } = useApi(() => api.listNotifications({ limit: 200 }), []);
  const ack = useMutation((id: string) => api.ackNotification(id));
  const [busyId, setBusyId] = useState<string | null>(null);

  const all = data?.items ?? [];
  const open = all.filter((n) => n.state !== "acknowledged");
  const urgentOpen = open.filter((n) => n.severity === "urgent");
  const rank = (n: Notification) => (n.state === "acknowledged" ? 3 : n.severity === "urgent" ? 0 : n.severity === "review" ? 1 : 2);
  const rows = all
    .filter((n) => filter === "all" || (filter === "open" && n.state !== "acknowledged") || (filter === "urgent" && n.severity === "urgent") || (filter === "acknowledged" && n.state === "acknowledged"))
    .sort((a, b) => rank(a) - rank(b) || b.created_at.localeCompare(a.created_at));

  const doAck = async (n: Notification) => {
    setBusyId(n.id);
    try {
      await ack.run(n.id);
      toast.success("Acknowledged");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not acknowledge");
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="stack">
      <PageHeader title="Notifications" subtitle="What needs your attention. Urgent alerts must be acknowledged, or they escalate to someone else." />
      {urgentOpen.length ? (
        <Callout tone="held" title={`${urgentOpen.length} urgent alert${urgentOpen.length === 1 ? "" : "s"} waiting`}>
          Acknowledging tells the system you have seen it. It does not decide anything: the work itself happens on the linked page.
        </Callout>
      ) : null}
      <Panel
        title={`Mine (${rows.length}${filter === "all" ? "" : ` of ${all.length}`})`}
        padded={Boolean(error)}
        actions={
          <span className="muted small">
            {open.length} not yet acknowledged
          </span>
        }
      >
        {error ? (
          <ErrorState error={error} onRetry={reload} />
        ) : !data ? (
          <div style={{ padding: "var(--sp-4)" }}>{loading ? <SkeletonLines lines={5} /> : null}</div>
        ) : (
          <>
            <div className="watch-filters" style={{ padding: "var(--sp-3) var(--sp-4)", borderBottom: "1px solid var(--line)" }}>
              <Field label="Show">
                {(id) => (
                  <Select id={id} value={filter} onChange={(e) => setFilter(e.target.value as StateFilter)} options={[{ value: "all", label: "Everything" }, { value: "open", label: "Not acknowledged" }, { value: "urgent", label: "Urgent" }, { value: "acknowledged", label: "Acknowledged" }]} />
                )}
              </Field>
            </div>
            {rows.length ? (
              <ul className="notif-list" aria-label="Notifications">
                {rows.map((n) => (
                  <Item key={n.id} n={n} busy={busyId === n.id} onAck={() => doAck(n)} />
                ))}
              </ul>
            ) : (
              <EmptyState title={filter === "all" ? "Nothing yet" : "Nothing in this view"} icon="inbox">
                {filter === "all" ? "Alerts and reminders for you appear here." : "Change the filter to see the rest."}
              </EmptyState>
            )}
          </>
        )}
      </Panel>
    </div>
  );
}

function Item({ n, busy, onAck }: { n: Notification; busy: boolean; onAck: () => void }) {
  const st = STATE[n.state];
  const done = n.state === "acknowledged";
  const level = n.escalation_level ?? 0;
  const link = n.link;
  return (
    <li className={`notif-item ${n.severity === "urgent" && !done ? "notif-item-urgent" : ""} ${done ? "notif-item-done" : ""}`}>
      <div>
        <div className="row" style={{ gap: 6 }}>
          <SeverityBadge severity={n.severity} />
          <Pill tone={st.tone} icon={st.icon} size="sm">{st.label}</Pill>
          {level > 0 ? <Pill tone="held" icon="arrow-up" size="sm">Escalation level {level}</Pill> : null}
        </div>
        <h3 className="notif-title" style={{ marginTop: 6 }}>{n.title}</h3>
        {n.body ? <p className="notif-body">{n.body}</p> : null}
        <div className="notif-meta small muted">
          <span title={formatDateTime(n.created_at)}>{formatRelative(n.created_at)}</span>
          <span aria-hidden="true">·</span>
          <span>{humanize(n.event)}</span>
          {n.ack_due_at && !done ? (
            <>
              <span aria-hidden="true">·</span>
              <AckCountdown due={n.ack_due_at} escalated={n.state === "escalated"} />
            </>
          ) : null}
          {done && n.acknowledged_at ? (
            <>
              <span aria-hidden="true">·</span>
              <span>Acknowledged {formatRelative(n.acknowledged_at)}</span>
            </>
          ) : null}
        </div>
        {n.state === "failed" ? <p className="small" style={{ margin: "6px 0 0", color: "var(--error-ink)" }}>This message could not be delivered outside the app. It is listed here so it is not lost.</p> : null}
      </div>
      <div className="notif-actions">
        {link ? (
          <ButtonLink to={link} variant="secondary" size="sm" iconRight="arrow-right">
            Open
          </ButtonLink>
        ) : null}
        {!done ? (
          <Button size="sm" variant={n.severity === "urgent" ? "primary" : "secondary"} icon="check" loading={busy} onClick={onAck} aria-label={`Acknowledge: ${n.title}`}>
            Acknowledge
          </Button>
        ) : null}
      </div>
    </li>
  );
}


// Incidents: a burst, such as one site failure matching many licences, is grouped as
// one incident so it is handled once instead of as many separate alerts.
import { useEffect, useMemo } from "react";
import { useLocation, Link } from "react-router-dom";
import { api, type WatchIncident } from "../../api";
import { useSession } from "../../app/session";
import { Callout, type Column, EmptyState, ErrorState, PageHeader, Panel, Pill, SeverityBadge, SkeletonLines, Table } from "../../components";
import { useApi } from "../../hooks/useApi";
import { type WatchMatch } from "../../api";
import { Basis, ConfidencePill, MatchActions, MatchStatusPill, TargetLabel } from "./shared";
import "./watch.css";
import { actsAs } from "../../lib/roles";

export default function IncidentsPage() {
  const { view } = useSession();
  const { hash } = useLocation();
  const incidents = useApi(() => api.listIncidents({ limit: 200 }), []);
  const matches = useApi(() => api.listMatches({ limit: 200 }), []);
  const notices = useApi(() => api.listNotices({ limit: 200 }), []);
  const byNotice = useMemo(() => new Map((notices.data?.items ?? []).map((n) => [n.id, n])), [notices.data]);
  const items = incidents.data?.items ?? [];

  useEffect(() => {
    if (!hash || !incidents.data) return;
    document.getElementById(hash.slice(1))?.scrollIntoView({ block: "start" });
  }, [hash, incidents.data]);

  const cols: Column<WatchMatch>[] = [
    { key: "t", header: "Licence or case", render: (m) => <div className="title-cell"><TargetLabel m={m} /><Basis items={m.match_basis} /></div> },
    { key: "c", header: "Confidence", render: (m) => <ConfidencePill c={m.match_confidence} /> },
    { key: "s", header: "Status", render: (m) => <MatchStatusPill s={m.status} />, hideBelow: 900 },
    { key: "a", header: "Decision", render: (m) => <MatchActions match={m} noticeTitle={byNotice.get(m.notice_id)?.title} canAct={actsAs(view, "case_officer")} />, align: "right" },
  ];

  return (
    <div className="stack">
      <PageHeader title="Incidents" subtitle="One event that touches many licences is handled as one incident, not as a pile of separate alerts." />
      <Callout tone="accent" title="Why group">
        When a manufacturing site fails an inspection, every licence made there matches at once. The incident keeps the burst together; each match still needs its own decision from a person.
      </Callout>
      {incidents.error ? <ErrorState error={incidents.error} onRetry={incidents.reload} /> : null}
      {!incidents.data && !incidents.error ? <SkeletonLines lines={5} /> : null}
      {incidents.data && !items.length ? (
        <Panel>
          <EmptyState title="No incidents" icon="layers">A burst of matches from one notice is grouped here.</EmptyState>
        </Panel>
      ) : null}
      {items.map((inc) => (
        <IncidentPanel key={inc.id} inc={inc} matches={(matches.data?.items ?? []).filter((m) => m.notice_id === inc.notice_id)} loading={matches.loading && !matches.data} title={byNotice.get(inc.notice_id)?.title} columns={cols} />
      ))}
    </div>
  );
}

function IncidentPanel({ inc, matches, loading, title, columns }: { inc: WatchIncident; matches: WatchMatch[]; loading: boolean; title?: string; columns: Column<WatchMatch>[] }) {
  const openCount = matches.filter((m) => m.status === "open").length;
  return (
    <Panel
      id={inc.id}
      title={
        <span className="row">
          Incident {inc.id}
          <SeverityBadge severity={inc.severity} />
          <Pill tone={inc.status === "open" ? "look" : "neutral"} size="sm">{inc.status === "open" ? "Open" : "Closed"}</Pill>
        </span>
      }
      padded={false}
    >
      <div className="incident-head" style={{ padding: "var(--sp-3) var(--sp-4)" }}>
        <div>
          <div className="muted small">Site</div>
          <div className="strong">{inc.site ?? "Not named"}</div>
        </div>
        <div>
          <div className="muted small">Licences matched</div>
          <div className="strong">{inc.match_count}</div>
        </div>
        <div>
          <div className="muted small">Still waiting for a person</div>
          <div className="strong">{openCount}</div>
        </div>
        <div>
          <div className="muted small">Notice</div>
          <Link to={`/watch/notices/${inc.notice_id}`}>{title ?? inc.notice_id}</Link>
        </div>
      </div>
      <Table caption={`Matches in incident ${inc.id}`} dense rows={loading ? undefined : matches} loading={loading} rowKey={(m) => m.id} columns={columns} empty={<EmptyState title="No matches" />} />
    </Panel>
  );
}

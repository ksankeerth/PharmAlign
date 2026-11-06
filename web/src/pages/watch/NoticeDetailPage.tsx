// Notice detail: revisions, matches with their basis and confidence, and the agent's
// explanation, shown as what it is: a later note that arrived after the alert.
import { Link, useParams } from "react-router-dom";
import { api, type NoticeDetail, type WatchMatch } from "../../api";
import { ButtonLink, Callout, type Column, EmptyState, ErrorState, EvidenceQuote, KeyValue, PageHeader, Panel, Pill, SeverityBadge, SkeletonLines, Table } from "../../components";
import { useApi } from "../../hooks/useApi";
import { useSession } from "../../app/session";
import { formatDate, formatDateTime, shortFingerprint } from "../../lib/format";
import { humanize } from "../../lib/labels";
import { Basis, ConfidencePill, MatchActions, MatchStatusPill, TargetLabel } from "./shared";
import "./watch.css";

const RANK: Record<string, number> = { info: 0, review: 1, urgent: 2 };

export default function NoticeDetailPage() {
  const { noticeId = "" } = useParams();
  const { view } = useSession();
  const { data, error, loading, reload } = useApi(() => api.getNotice(noticeId), [noticeId]);
  const crumbs = [{ label: "Foreign watch" }, { label: "Foreign notices", to: "/watch/notices" }, { label: noticeId }];

  if (error) return (
    <div className="stack">
      <PageHeader title="Notice" breadcrumbs={crumbs} />
      <ErrorState error={error} onRetry={reload} />
    </div>
  );
  if (!data) return (
    <div className="stack">
      <PageHeader title="Notice" breadcrumbs={crumbs} />
      {loading ? <SkeletonLines lines={6} /> : null}
    </div>
  );
  return <Detail n={data} crumbs={crumbs} canAct={view === "case_officer"} />;
}

function Detail({ n, crumbs, canAct }: { n: NoticeDetail; crumbs: { label: string; to?: string }[]; canAct: boolean }) {
  const a = n.assessment;
  const raised = a && (RANK[a.suggested_severity] ?? 0) > (RANK[n.severity] ?? 0);
  const matchCols: Column<WatchMatch>[] = [
    {
      key: "target",
      header: "Matched to",
      render: (m) => (
        <div className="title-cell">
          <TargetLabel m={m} />
          <Basis items={m.match_basis} />
        </div>
      ),
    },
    { key: "conf", header: "Confidence", render: (m) => <ConfidencePill c={m.match_confidence} /> },
    { key: "status", header: "Status", render: (m) => <MatchStatusPill s={m.status} />, hideBelow: 900 },
    { key: "act", header: "Decision", render: (m) => <MatchActions match={m} noticeTitle={n.title} canAct={canAct} />, align: "right" },
  ];
  return (
    <div className="stack">
      <PageHeader
        eyebrow={<code>{n.id}</code>}
        title={n.title}
        breadcrumbs={crumbs}
        status={
          <>
            <SeverityBadge severity={n.severity} />
            {n.retracted ? <Pill tone="neutral" icon="ban">Retracted</Pill> : null}
            {n.unmatched ? <Pill tone="look" icon="eye">Unmatched</Pill> : null}
          </>
        }
        subtitle={`${n.regulator}${n.reference ? ` · ref ${n.reference}` : ""} · published ${formatDate(n.published_on)} · fetched ${formatDateTime(n.fetched_at)}`}
        actions={n.incident_id ? <ButtonLink to={`/watch/incidents#${n.incident_id}`} variant="secondary" icon="layers">View incident</ButtonLink> : undefined}
      />

      {n.retracted ? (
        <Callout tone="neutral" title="The regulator retracted this notice">
          The latest revision withdraws it. The notice and its earlier revisions stay on record, and matches already decided keep their reasons.
        </Callout>
      ) : null}
      {n.unmatched ? (
        <Callout tone="look" title="Nothing here matched">
          No licence, open case or site uses what this notice names. It is stored, not dropped, so it can be found if a licence or reliance link is added later.
        </Callout>
      ) : null}

      <Panel title="Alert, then explanation">
        <ol className="flow">
          <li className="flow-step">
            <span className="flow-num" aria-hidden="true">1</span>
            <div>
              <div className="strong">Alert, set by code <span className="muted small">· {formatDateTime(n.fetched_at)}</span></div>
              <p style={{ margin: "4px 0 0" }}>
                The notice was stored and matched by rules. Severity was set to <strong>{humanize(n.severity).toLowerCase()}</strong> from the country pack, and the people responsible were alerted. No agent was needed for this.
              </p>
            </div>
          </li>
          <li className="flow-step flow-late">
            <span className="flow-num" aria-hidden="true">2</span>
            <div>
              <div className="strong">Explanation, written afterward</div>
              {a ? (
                <div className="explanation" style={{ marginTop: 6 }}>
                  <p className="muted xsmall" style={{ margin: 0 }}>
                    Arrived after the alert · Reliance Watch agent · run <code>{a.run_id}</code>. Read it as an explanation, not as the alert.
                  </p>
                  <p style={{ margin: "6px 0" }}>{a.summary}</p>
                  <KeyValue
                    columns={2}
                    items={[
                      { label: "Change type", value: humanize(a.change_type) },
                      { label: "Suggested severity", value: <span className="row"><SeverityBadge severity={a.suggested_severity} />{raised ? <span className="muted small">Higher than the alert. A person decides whether to act on it.</span> : <span className="muted small">Agrees with the alert.</span>}</span> },
                    ]}
                  />
                  {a.evidence?.length ? (
                    <div className="stack-sm" style={{ marginTop: 8 }}>
                      {a.evidence.map((e) => (
                        <EvidenceQuote key={e.anchor} evidence={e} />
                      ))}
                    </div>
                  ) : null}
                  <p className="muted xsmall" style={{ margin: "8px 0 0" }}>An explanation can raise severity, never lower it.</p>
                </div>
              ) : (
                <p className="muted" style={{ margin: "4px 0 0" }}>No explanation has arrived yet. The alert stands without it.</p>
              )}
            </div>
          </li>
        </ol>
      </Panel>

      <Panel title={`Matches (${n.matches.length})`} padded={false}>
        <Table caption="Matches" dense rows={n.matches} rowKey={(m) => m.id} columns={matchCols} empty={<EmptyState title="No matches" icon="link">Nothing used what this notice names.</EmptyState>} />
        {n.matches.some((m) => m.match_confidence === "low" && m.status === "open") ? (
          <div style={{ padding: "var(--sp-3) var(--sp-4)" }}>
            <p className="muted small" style={{ margin: 0 }}>
              Low-confidence matches are never closed by code. A person dismisses each one with a reason or opens a re-review.
            </p>
          </div>
        ) : null}
        <div style={{ padding: "0 var(--sp-4) var(--sp-3)" }}>
          <Link to="/watch/matches">All matches</Link>
        </div>
      </Panel>

      {n.revisions[n.revisions.length - 1]?.text ? (
        <Panel title="Notice text">
          <p className="small" style={{ margin: 0, whiteSpace: "pre-wrap" }}>{n.revisions[n.revisions.length - 1].text}</p>
          <p className="muted xsmall" style={{ margin: "8px 0 0" }}>Latest revision, as the regulator published it.</p>
        </Panel>
      ) : null}

      <Panel title={`Revisions (${n.revisions.length})`} padded={false}>
        <Table
          caption="Revisions"
          dense
          rows={n.revisions}
          rowKey={(r) => String(r.revision)}
          columns={[
            { key: "rev", header: "Revision", render: (r) => `r${r.revision}` },
            { key: "kind", header: "Kind", render: (r) => (r.is_retraction ? <Pill tone="neutral" icon="ban" size="sm">Retraction</Pill> : r.revision === 1 ? "Original" : "Revision") },
            { key: "at", header: "Fetched", render: (r) => formatDateTime(r.fetched_at) },
            { key: "anchor", header: "Text anchor", render: (r) => <code className="small">{r.text_anchor}</code>, hideBelow: 900 },
            { key: "hash", header: "Content hash", render: (r) => <code className="small" title={r.content_hash}>{shortFingerprint(r.content_hash, 8)}</code>, hideBelow: 900 },
          ]}
        />
      </Panel>
    </div>
  );
}

// Matches: what a notice may affect. Each open match waits for a person, who dismisses
// it with a reason or opens a reliance re-review (which creates a case). Low-confidence
// matches are never closed by code.
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, type CaseDetail, type WatchMatch } from "../../api";
import { useSession } from "../../app/session";
import { Callout, type Column, EmptyState, ErrorState, Field, PageHeader, Panel, Select, SeverityBadge, Table } from "../../components";
import { useApi } from "../../hooks/useApi";
import { formatDateTime } from "../../lib/format";
import { Basis, ConfidencePill, MatchActions, MatchStatusPill, TargetLabel } from "./shared";
import "./watch.css";
import { actsAs } from "../../lib/roles";

type Filter = "open" | "all" | "dismissed" | "rereview_opened";

export default function MatchesPage() {
  const { view } = useSession();
  const canAct = actsAs(view, "case_officer");
  const [filter, setFilter] = useState<Filter>("open");
  const [opened, setOpened] = useState<CaseDetail | null>(null);
  const matches = useApi(() => api.listMatches({ limit: 200 }), []);
  const notices = useApi(() => api.listNotices({ limit: 200 }), []);
  const byNotice = useMemo(() => new Map((notices.data?.items ?? []).map((n) => [n.id, n])), [notices.data]);
  const all = matches.data?.items ?? [];
  const rows = all.filter((m) => filter === "all" || m.status === filter);
  const open = all.filter((m) => m.status === "open");
  const low = open.filter((m) => m.match_confidence === "low");

  const columns: Column<WatchMatch>[] = [
    {
      key: "notice",
      header: "Notice",
      render: (m) => {
        const n = byNotice.get(m.notice_id);
        return (
          <div className="title-cell">
            <Link to={`/watch/notices/${m.notice_id}`} className="strong">{n?.title ?? m.notice_id}</Link>
            <span className="row" style={{ gap: 6 }}>{n ? <SeverityBadge severity={n.severity} /> : null}<span className="muted small">{n?.regulator}</span></span>
          </div>
        );
      },
    },
    {
      key: "target",
      header: "May affect",
      render: (m) => (
        <div className="title-cell">
          <TargetLabel m={m} />
          <Basis items={m.match_basis} />
        </div>
      ),
    },
    { key: "conf", header: "Confidence", render: (m) => <ConfidencePill c={m.match_confidence} />, sort: (m) => ({ low: 0, medium: 1, high: 2 })[m.match_confidence] },
    {
      key: "status",
      header: "Status",
      render: (m) => (
        <div className="stack-sm" style={{ gap: 2 }}>
          <MatchStatusPill s={m.status} />
          {m.decided_at ? <span className="muted xsmall">{formatDateTime(m.decided_at)}</span> : null}
        </div>
      ),
      sort: (m) => m.status,
      hideBelow: 900,
    },
    { key: "act", header: "Decision", render: (m) => <MatchActions match={m} noticeTitle={byNotice.get(m.notice_id)?.title} canAct={canAct} onRereview={(c) => setOpened(c)} />, align: "right" },
  ];

  return (
    <div className="stack">
      <PageHeader title="Matches" subtitle="Where a foreign notice may affect a licence, a case or a site. A person decides every match." />
      {opened ? (
        <Callout tone="pass" title={`Re-review opened as ${opened.id}`}>
          The case re-runs the checks that relied on the foreign approval. <Link to={`/cases/${opened.id}`}>Open {opened.id}</Link>
        </Callout>
      ) : null}
      {!canAct ? <Callout tone="neutral">You can read matches here. Case Officers dismiss them or open a re-review.</Callout> : null}
      {low.length ? (
        <Callout tone="look" title={`${low.length} low-confidence match${low.length === 1 ? "" : "es"} need a person`}>
          The notice names something similar but not certain, such as a manufacturing site shared with other licences. Code does not close these.
        </Callout>
      ) : null}
      <Panel title={`Matches (${rows.length})`} padded={Boolean(matches.error)}>
        {matches.error ? (
          <ErrorState error={matches.error} onRetry={matches.reload} />
        ) : (
          <>
            <div className="watch-filters" style={{ padding: "var(--sp-3) var(--sp-4)" }}>
              <Field label="Show">
                {(id) => (
                  <Select id={id} value={filter} onChange={(e) => setFilter(e.target.value as Filter)} options={[{ value: "open", label: `Open (${open.length})` }, { value: "all", label: "All matches" }, { value: "dismissed", label: "Dismissed" }, { value: "rereview_opened", label: "Re-review opened" }]} />
                )}
              </Field>
            </div>
            <Table
              caption="Matches"
              dense
              rows={matches.data ? rows : undefined}
              loading={matches.loading && !matches.data}
              rowKey={(m) => m.id}
              columns={columns}
              empty={<EmptyState title={filter === "open" ? "No open matches" : "No matches"} icon="link">{filter === "open" ? "Every match has been decided." : "Nothing in this view."}</EmptyState>}
            />
          </>
        )}
      </Panel>
      <p className="muted small" style={{ margin: 0 }}>Decided matches keep their reason in the audit log.</p>
    </div>
  );
}

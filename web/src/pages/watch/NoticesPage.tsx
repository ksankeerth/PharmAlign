// Foreign notices: every notice from foreign regulators, including unmatched ones and
// retractions. Nothing is dropped. Severity is set first by code.
import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type NoticeSummary } from "../../api";
import { Callout, type Column, EmptyState, ErrorState, Field, PageHeader, Panel, Pill, Select, SeverityBadge, SyntheticTag, Table } from "../../components";
import { useApi } from "../../hooks/useApi";
import { formatDate, formatDateTime, formatRelative } from "../../lib/format";
import "./watch.css";

type Show = "all" | "unmatched" | "matched" | "retracted" | "revised";

export default function NoticesPage() {
  const navigate = useNavigate();
  const [show, setShow] = useState<Show>("all");
  const [severity, setSeverity] = useState("");
  const { data, error, loading, reload } = useApi(() => api.listNotices({ limit: 200 }), []);
  const all = data?.items ?? [];
  const rows = useMemo(
    () =>
      all.filter(
        (n) =>
          (!severity || n.severity === severity) &&
          (show === "all" || (show === "unmatched" && n.unmatched) || (show === "matched" && !n.unmatched) || (show === "retracted" && n.retracted) || (show === "revised" && n.latest_revision > 1)),
      ),
    [all, show, severity],
  );
  const count = (f: (n: NoticeSummary) => boolean) => all.filter(f).length;

  const columns: Column<NoticeSummary>[] = [
    { key: "sev", header: "Severity", render: (n) => <SeverityBadge severity={n.severity} />, sort: (n) => ({ urgent: 0, review: 1, info: 2 })[n.severity] ?? 3, width: "110px" },
    {
      key: "title",
      header: "Notice",
      render: (n) => (
        <div className="title-cell">
          <Link to={`/watch/notices/${n.id}`} onClick={(e) => e.stopPropagation()} className="strong">
            {n.title}
          </Link>
          <span className="muted small">{n.regulator} · <code>{n.id}</code></span>
        </div>
      ),
      sort: (n) => n.title,
    },
    {
      key: "state",
      header: "Status",
      render: (n) => (
        <div className="row" style={{ gap: 4 }}>
          {n.unmatched ? <Pill tone="look" icon="eye" size="sm" title="No licence, case or site matched. Kept so it can be found later.">Unmatched</Pill> : <Pill tone="accent" size="sm" icon="link">{n.match_count} match{n.match_count === 1 ? "" : "es"}</Pill>}
          {n.retracted ? <Pill tone="neutral" icon="ban" size="sm">Retracted</Pill> : null}
          {n.latest_revision > 1 && !n.retracted ? <Pill tone="neutral" icon="refresh" size="sm">Revised</Pill> : null}
        </div>
      ),
    },
    { key: "rev", header: "Revision", render: (n) => `r${n.latest_revision}`, align: "right", hideBelow: 1000 },
    { key: "pub", header: "Published", render: (n) => formatDate(n.published_on), sort: (n) => n.published_on ?? "", hideBelow: 1000 },
    { key: "fetched", header: "Fetched", render: (n) => <span title={formatDateTime(n.fetched_at)} className="nowrap">{formatRelative(n.fetched_at)}</span>, sort: (n) => n.fetched_at },
  ];

  return (
    <div className="stack">
      <PageHeader
        title="Foreign notices"
        subtitle="What foreign regulators published, as fetched. Unmatched notices stay in this list on purpose."
        status={<SyntheticTag label="Reference Agency A and B are fictional" />}
      />
      <Callout tone="accent" title="Severity is set by code first">
        When a notice arrives, its severity comes from the country pack's severity floor and people are alerted at once. An explanation written afterward can raise the severity, never lower it.
      </Callout>
      <div className="wstat-row">
        <div className="wstat"><div className="wstat-value">{all.length}</div><div className="wstat-label">Notices</div></div>
        <div className={`wstat ${count((n) => n.unmatched) ? "wstat-warn" : ""}`}><div className="wstat-value">{count((n) => n.unmatched)}</div><div className="wstat-label">Unmatched, kept</div></div>
        <div className="wstat"><div className="wstat-value">{count((n) => n.severity === "urgent")}</div><div className="wstat-label">Urgent</div></div>
        <div className="wstat"><div className="wstat-value">{count((n) => n.retracted || n.latest_revision > 1)}</div><div className="wstat-label">Revised or retracted</div></div>
      </div>
      <Panel title={`Notices${data ? ` (${rows.length})` : ""}`} padded={Boolean(error)}>
        {error ? (
          <ErrorState error={error} onRetry={reload} />
        ) : (
          <>
            <div className="watch-filters" style={{ padding: "var(--sp-3) var(--sp-4)" }}>
              <Field label="Show">
                {(id) => (
                  <Select id={id} value={show} onChange={(e) => setShow(e.target.value as Show)} options={[{ value: "all", label: "All notices" }, { value: "unmatched", label: "Unmatched only" }, { value: "matched", label: "Matched only" }, { value: "retracted", label: "Retracted" }, { value: "revised", label: "Revised" }]} />
                )}
              </Field>
              <Field label="Severity">
                {(id) => (
                  <Select id={id} value={severity} onChange={(e) => setSeverity(e.target.value)} options={[{ value: "", label: "Any severity" }, { value: "urgent", label: "Urgent" }, { value: "review", label: "Review" }, { value: "info", label: "Info" }]} />
                )}
              </Field>
            </div>
            <Table
              caption="Foreign notices"
              rows={data ? rows : undefined}
              loading={loading && !data}
              rowKey={(n) => n.id}
              columns={columns}
              initialSort={{ key: "fetched", dir: "desc" }}
              onRowClick={(n) => navigate(`/watch/notices/${n.id}`)}
              dense
              empty={<EmptyState title="No notices match" icon="radar">Change the filters to see more.</EmptyState>}
            />
          </>
        )}
      </Panel>
    </div>
  );
}

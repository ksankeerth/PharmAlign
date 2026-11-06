// Coverage: where the watch could be silent without anyone noticing. A licence with no
// reliance link can never match a foreign notice, and a source that stopped delivering
// looks the same as a quiet week. Silence is not safety.
import { useMemo } from "react";
import { api, type Licence, type WatchSource } from "../../api";
import { useSession } from "../../app/session";
import { Callout, type Column, EmptyState, ErrorState, KeyValue, PageHeader, Panel, Pill, SkeletonLines, Table } from "../../components";
import { useApi } from "../../hooks/useApi";
import { formatDate, formatDateTime, formatRelative } from "../../lib/format";
import { humanize } from "../../lib/labels";
import "./watch.css";

export default function CoveragePage() {
  const { view } = useSession();
  const cov = useApi(() => api.getWatchCoverage(), []);
  const licences = useApi(() => api.listLicences({ limit: 200 }), []);
  const medicines = useApi(() => api.listMedicines({ limit: 200 }), []);
  // The source list is for Case Officers and Platform Admins; other views see ids only.
  const canSeeSources = view === "case_officer" || view === "platform_admin";
  const sources = useApi(() => api.listWatchSources({ limit: 200 }), [], { enabled: canSeeSources });
  const med = useMemo(() => new Map((medicines.data?.items ?? []).map((m) => [m.id, m])), [medicines.data]);
  const lic = useMemo(() => new Map((licences.data?.items ?? []).map((l) => [l.id, l])), [licences.data]);
  const src = useMemo(() => new Map((sources.data?.items ?? []).map((s) => [s.id, s])), [sources.data]);

  if (cov.error) return (
    <div className="stack">
      <PageHeader title="Coverage" />
      <ErrorState error={cov.error} onRetry={cov.reload} />
    </div>
  );
  const c = cov.data;
  if (!c) return (
    <div className="stack">
      <PageHeader title="Coverage" />
      <SkeletonLines lines={5} />
    </div>
  );
  const pct = c.licences_total ? Math.round((c.licences_with_reliance_link / c.licences_total) * 100) : 0;

  const licenceCols: Column<string>[] = [
    { key: "id", header: "Licence", render: (id) => <code>{id}</code> },
    { key: "med", header: "Medicine", render: (id) => (lic.get(id) ? (med.get(lic.get(id)!.medicine_id)?.name ?? lic.get(id)!.medicine_id) : <span className="muted">Loading</span>) },
    { key: "ref", header: "Government reference", render: (id) => <code className="small">{lic.get(id)?.gov_ref ?? ""}</code>, hideBelow: 900 },
    { key: "status", header: "Status", render: (id) => humanize((lic.get(id) as Licence | undefined)?.gov_status) },
    { key: "to", header: "Valid to", render: (id) => formatDate(lic.get(id)?.valid_to), hideBelow: 1000 },
  ];
  const sourceCols: Column<string>[] = [
    { key: "id", header: "Source", render: (id) => <div className="title-cell"><span className="strong">{src.get(id)?.regulator ?? id}</span><code className="small muted">{id}</code></div> },
    { key: "kind", header: "Kind", render: (id) => humanize(src.get(id)?.kind), hideBelow: 900 },
    { key: "h", header: "Health", render: (id) => <HealthPill s={src.get(id)} /> },
    { key: "last", header: "Last success", render: (id) => (src.get(id)?.last_success_at ? <span title={formatDateTime(src.get(id)!.last_success_at)}>{formatRelative(src.get(id)!.last_success_at)}</span> : <span className="muted">Never</span>), hideBelow: 1000 },
    { key: "err", header: "Last error", render: (id) => src.get(id)?.last_error ?? <span className="muted">None</span> },
  ];

  return (
    <div className="stack">
      <PageHeader title="Coverage" subtitle="Where the watch could be quiet without anyone knowing." />
      <Callout tone="look" title="Silence is not safety">
        No alert about a licence does not mean its reference approval is fine. It may mean the licence has no reliance link, or that the source stopped delivering. Both are listed here so they are seen, not assumed.
      </Callout>

      <div className="wstat-row">
        <div className="wstat">
          <div className="wstat-value">{c.licences_with_reliance_link} of {c.licences_total}</div>
          <div className="wstat-label">Licences with a reliance link</div>
          <div className="cov-bar" role="img" aria-label={`${pct} percent of licences have a reliance link`}><span style={{ width: `${pct}%` }} /></div>
        </div>
        <div className={`wstat ${c.licences_without_link.length ? "wstat-warn" : ""}`}>
          <div className="wstat-value">{c.licences_without_link.length}</div>
          <div className="wstat-label">Licences the watch cannot match</div>
        </div>
        <div className="wstat">
          <div className="wstat-value">{c.sources_total}</div>
          <div className="wstat-label">Sources watched</div>
        </div>
        <div className={`wstat ${c.unhealthy_sources.length ? "wstat-warn" : ""}`}>
          <div className="wstat-value">{c.unhealthy_sources.length}</div>
          <div className="wstat-label">Sources not delivering</div>
        </div>
      </div>

      <Panel title={`Licences with no reliance link (${c.licences_without_link.length})`} padded={false}>
        <p className="muted small" style={{ margin: 0, padding: "var(--sp-3) var(--sp-4) 0" }}>
          A foreign notice can only match a licence by what it names or by a recorded reliance link. These licences have no link, so a notice about their reference approval would not find them.
        </p>
        <Table caption="Licences with no reliance link" dense rows={c.licences_without_link} rowKey={(id) => id} columns={licenceCols} empty={<EmptyState title="Every licence has a reliance link" icon="check" />} />
      </Panel>

      <Panel title={`Sources not delivering (${c.unhealthy_sources.length})`} padded={false}>
        <p className="muted small" style={{ margin: 0, padding: "var(--sp-3) var(--sp-4) 0" }}>
          A source that fails, returns nothing or changes layout produces no notices. That looks exactly like a quiet week.
        </p>
        {!canSeeSources && c.unhealthy_sources.length ? (
          <div style={{ padding: "var(--sp-3) var(--sp-4)" }}>
            <KeyValue items={[{ label: "Source ids", value: c.unhealthy_sources.join(", "), mono: true }]} />
            <p className="muted xsmall" style={{ marginBottom: 0 }}>Source details are visible to Case Officers and Platform Admins.</p>
          </div>
        ) : (
          <Table caption="Sources not delivering" dense rows={c.unhealthy_sources} rowKey={(id) => id} columns={sourceCols} loading={canSeeSources && sources.loading && !sources.data} empty={<EmptyState title="Every source is delivering" icon="check" />} />
        )}
      </Panel>
    </div>
  );
}

function HealthPill({ s }: { s: WatchSource | undefined }) {
  if (!s) return <span className="muted">Unknown</span>;
  return <Pill tone="held" icon="alert-triangle" size="sm">{humanize(s.health)}</Pill>;
}

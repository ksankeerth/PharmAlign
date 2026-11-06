// Case reconstruction index: pick a case, then read exactly what the saved records say.
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type CaseSummary } from "../../api";
import { CaseStatusPill, PageHeader, Callout, type Column, EmptyState, ErrorState, Field, Pill, TextInput, Panel, Table } from "../../components";
import { useApi } from "../../hooks/useApi";
import { formatDateTime } from "../../lib/format";
import { humanize } from "../../lib/labels";
import "./audit.css";

export default function ReconstructionIndexPage() {
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const cases = useApi(() => api.listCases({ limit: 200 }), []);
  const meds = useApi(() => api.listMedicines({ limit: 200 }), []);
  const medName = useMemo(() => new Map((meds.data?.items ?? []).map((m) => [m.id, m.name])), [meds.data]);
  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (cases.data?.items ?? []).filter((c) => !needle || `${c.id} ${medName.get(c.medicine_id) ?? ""} ${c.case_type} ${c.status}`.toLowerCase().includes(needle));
  }, [cases.data, q, medName]);

  const columns: Column<CaseSummary>[] = [
    { key: "id", header: "Case", render: (r) => <code>{r.id}</code>, sort: (r) => r.id },
    { key: "med", header: "Medicine", render: (r) => medName.get(r.medicine_id) ?? r.medicine_id, sort: (r) => medName.get(r.medicine_id) ?? r.medicine_id },
    { key: "type", header: "Type", render: (r) => humanize(r.case_type), hideBelow: 900 },
    { key: "status", header: "Status", render: (r) => <CaseStatusPill status={r.status} size="sm" />, sort: (r) => r.status },
    { key: "env", header: "Environment", render: (r) => <Pill tone="neutral" size="sm">{humanize(r.environment)}</Pill>, hideBelow: 1000 },
    { key: "seq", header: "Sequence", render: (r) => r.sequence, align: "right", hideBelow: 900 },
    { key: "created", header: "Opened", render: (r) => formatDateTime(r.created_at), sort: (r) => r.created_at, hideBelow: 1100 },
  ];

  return (
    <div className="stack">
      <PageHeader title="Case reconstruction" subtitle="Pick a case to read everything that happened to it, in order, from the saved records." />
      <Callout tone="accent" title="Saved records only">
        Reconstruction reads what was stored at the time. It does not re-run any check, agent or rule, so it cannot change the outcome or the log.
      </Callout>
      <Panel title={`Cases${cases.data ? ` (${rows.length})` : ""}`} padded={Boolean(cases.error)} actions={undefined}>
        {cases.error ? (
          <ErrorState error={cases.error} onRetry={cases.reload} />
        ) : (
          <>
            <div style={{ padding: "var(--sp-3) var(--sp-4)", maxWidth: 380 }}>
              <Field label="Find a case">{(id) => <TextInput id={id} type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Case number, medicine or status" />}</Field>
            </div>
            <Table caption="Cases" rows={cases.data ? rows : undefined} loading={cases.loading && !cases.data} rowKey={(r) => r.id} columns={columns} onRowClick={(r) => navigate(`/audit/cases/${r.id}`)} dense initialSort={{ key: "id", dir: "asc" }} empty={<EmptyState title="No case matches" />} />
          </>
        )}
      </Panel>
    </div>
  );
}

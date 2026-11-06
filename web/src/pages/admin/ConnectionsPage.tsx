// Connections: the government connection (a mock regulator in the demo), conformance
// results, last sync. Approving use belongs to a Process Owner.
import { useState } from "react";
import { api } from "../../api";
import type { Connection } from "../../api/types";
import { Button, Callout, ConfirmModal, EmptyState, ErrorState, Field, FillFromJson, KeyValue, PageHeader, Panel, Pill, Select, SkeletonLines, Table, TextInput, type Tone, useToast } from "../../components";
import { useApi, useMutation } from "../../hooks/useApi";
import { pickFields, type FieldMap } from "../../lib/fillJson";
import { formatDateTime, formatRelative } from "../../lib/format";
import { humanize } from "../../lib/labels";
import { Metric, usePeopleNames } from "./shared";

const CONFORMANCE: Record<string, string> = {
  auth: "Signs in",
  read_licence: "Reads a licence",
  read_back: "Reads back what it sent",
  propose_grant: "Accepts a grant proposal",
  propose_suspension: "Accepts a suspension proposal",
  idempotency_honoured: "Repeating a request changes nothing",
  version_conflict_refused: "Refuses an outdated version",
  timeout_then_read_back: "After a timeout, reads back before retrying",
  rejection_reason_returned: "Returns the reason for a rejection",
  clock_skew_tolerated: "Tolerates clock differences",
  rate_limit_respected: "Respects rate limits",
  spec_version_match: "Speaks the agreed spec version",
  manual_ack_recorded: "A person records each reference",
};

const STATUS: Record<Connection["status"], { tone: Tone; label: string; icon: "check" | "x" | "circle-dashed" | "stamp" }> = {
  unchecked: { tone: "neutral", label: "Not checked", icon: "circle-dashed" },
  failing: { tone: "held", label: "Failing", icon: "x" },
  passing: { tone: "look", label: "Passing, awaiting approval", icon: "check" },
  approved: { tone: "pass", label: "Approved for use", icon: "stamp" },
};

export default function ConnectionsPage() {
  const { data, error, loading, reload } = useApi(() => api.listConnections(), []);
  const names = usePeopleNames();
  const toast = useToast();
  const [reg, setReg] = useState(false);
  const check = useMutation((id: string) => api.checkConnection(id));
  const items = data?.items ?? [];

  if (error) return <ErrorState error={error} onRetry={reload} />;
  return (
    <div className="stack">
      <PageHeader
        title="Connections"
        subtitle="How decisions reach the government record. Each connection must pass conformance checks before it is used."
        actions={<Button icon="plus" onClick={() => setReg(true)}>Register a connection</Button>}
      />
      <Callout tone="accent" icon="stamp" title="A Process Owner approves use">
        Platform Admin registers a connection and runs its checks. Passing is not approval: a named Process Owner approves use on their own screen, and the approval is audited.
      </Callout>
      <div className="adm-metrics">
        <Metric label="Connections" value={data ? items.length : "..."} />
        <Metric label="Approved for use" value={items.filter((c) => c.status === "approved").length} tone="pass" />
        <Metric label="Awaiting approval" value={items.filter((c) => c.status === "passing").length} tone="look" />
        <Metric label="Failing or unchecked" value={items.filter((c) => c.status === "failing" || c.status === "unchecked").length} tone="held" />
      </div>

      {loading && !data ? <SkeletonLines lines={6} /> : null}
      {data && items.length === 0 ? <EmptyState title="No connections yet" icon="plug" /> : null}
      {items.map((c) => {
        const s = STATUS[c.status];
        const failed = c.results.filter((r) => !r.passed).length;
        return (
          <Panel
            key={c.id}
            title={<span className="row">{c.name} <Pill tone={s.tone} icon={s.icon} size="sm">{s.label}</Pill>{c.kind === "mock" ? <Pill tone="look" size="sm" icon="beaker">Mock regulator</Pill> : null}</span>}
            actions={<Button size="sm" icon="refresh" loading={check.pending} onClick={() => void check.run(c.id).then((r) => toast.success(`${r.name}: ${r.status === "failing" ? "some checks fail" : "all checks pass"}`)).catch(() => toast.error("The check could not run"))}>Run conformance check</Button>}
          >
            <div className="stack">
              <KeyValue
                columns={3}
                items={[
                  { label: "Kind", value: humanize(c.kind) },
                  { label: "Spec version", value: c.spec_version, mono: true },
                  { label: "Address", value: c.base_url, mono: true },
                  { label: "Last checked", value: c.last_checked_at ? `${formatDateTime(c.last_checked_at)} (${formatRelative(c.last_checked_at)})` : "Never" },
                  { label: "Last sync", value: c.last_sync_at ? `${formatRelative(c.last_sync_at)}` : c.kind === "manual" ? "None: a person records each reference" : "Never" },
                  { label: "Approved for use by", value: c.approved_by ? names(c.approved_by) : "Not approved yet" },
                ]}
              />
              {c.kind === "mock" ? (
                <p className="small muted">Checks run against the built-in mock, which behaves like the agreed spec. A real government system must pass again before it replaces it.</p>
              ) : null}
              <Table
                rows={c.results}
                rowKey={(r) => r.key}
                dense
                caption={`Conformance results for ${c.name}`}
                empty={<EmptyState title="No checks run yet" icon="list-checks">Run the conformance check to see results.</EmptyState>}
                columns={[
                  { key: "k", header: `Conformance (${c.results.length - failed} of ${c.results.length} pass)`, render: (r) => CONFORMANCE[r.key] ?? humanize(r.key) },
                  { key: "p", header: "Result", width: "110px", render: (r) => (r.passed ? <Pill tone="pass" icon="check" size="sm">Pass</Pill> : <Pill tone="held" icon="x" size="sm">Fail</Pill>) },
                  { key: "d", header: "Detail", render: (r) => <span className="small">{r.detail ?? <span className="muted">None</span>}</span> },
                ]}
              />
            </div>
          </Panel>
        );
      })}
      <RegisterModal open={reg} onClose={() => setReg(false)} />
    </div>
  );
}

const CONNECTION_FIELDS: FieldMap = { name: "string", kind: "string", base_url: "string", spec_version: "string" };
const CONNECTION_KINDS: Connection["kind"][] = ["api", "manual", "mock"];

function RegisterModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [name, setName] = useState("");
  const [kind, setKind] = useState<Connection["kind"]>("api");
  const [url, setUrl] = useState("");
  const [spec, setSpec] = useState("1.0.0");
  const fill = (item: unknown) => {
    const f = pickFields(item, CONNECTION_FIELDS);
    if (typeof f.name === "string") setName(f.name);
    if (CONNECTION_KINDS.includes(f.kind as Connection["kind"])) setKind(f.kind as Connection["kind"]);
    setUrl(typeof f.base_url === "string" ? f.base_url : "");
    if (typeof f.spec_version === "string") setSpec(f.spec_version);
  };
  return (
    <ConfirmModal
      open={open}
      onClose={onClose}
      title="Register a connection"
      confirmLabel="Register"
      summary={
        <div className="stack-sm">
          <div>
            <FillFromJson onFill={fill} />
          </div>
          <Field label="Name" required>{(id) => <TextInput id={id} value={name} onChange={(e) => setName(e.target.value)} />}</Field>
          <Field label="Kind">{(id) => <Select id={id} value={kind} onChange={(e) => setKind(e.target.value as Connection["kind"])} options={[{ value: "api", label: "Programmatic interface" }, { value: "manual", label: "Manual entry (portal with no interface)" }, { value: "mock", label: "Mock" }]} />}</Field>
          {kind === "api" ? <Field label="Address" required>{(id) => <TextInput id={id} value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://" />}</Field> : null}
          <Field label="Spec version">{(id) => <TextInput id={id} value={spec} onChange={(e) => setSpec(e.target.value)} />}</Field>
        </div>
      }
      onConfirm={async () => {
        if (!name.trim()) throw new Error("A name is required.");
        await api.createConnection({ name: name.trim(), kind, base_url: kind === "api" ? url.trim() || null : null, spec_version: spec.trim() || "1.0.0" });
      }}
    />
  );
}

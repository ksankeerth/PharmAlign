// Watch sources: health of each foreign regulator source. A source that stops reporting
// is prominent, because silence is not safety.
import { useState } from "react";
import { api } from "../../api";
import type { WatchSource } from "../../api/types";
import { Button, Callout, ConfirmModal, EmptyState, ErrorState, Field, FillFromJson, PageHeader, Panel, Select, Table, TextInput, useToast } from "../../components";
import { useApi } from "../../hooks/useApi";
import { pickFields, type FieldMap } from "../../lib/fillJson";
import { formatDateTime, formatRelative } from "../../lib/format";
import { humanize } from "../../lib/labels";
import { HEALTH_LABEL, HealthPill, Metric } from "./shared";

const UNHEALTHY = new Set(["failing", "zero_yield", "layout_changed"]);
const WHAT: Record<string, string> = {
  failing: "The source could not be read.",
  zero_yield: "It was read but produced nothing for longer than usual.",
  layout_changed: "The page layout changed, so the reader finds nothing even though the page loads.",
};

function every(min: number): string {
  return min % 60 === 0 ? `${min / 60} h` : `${min} min`;
}

export default function WatchSourcesPage() {
  const { data, error, loading, reload } = useApi(() => api.listWatchSources({ limit: 100 }), []);
  const toast = useToast();
  const [busy, setBusy] = useState<string | null>(null);
  const [add, setAdd] = useState(false);
  const items = data?.items ?? [];
  const bad = items.filter((s) => UNHEALTHY.has(s.health));
  const fetchNow = async (s: WatchSource) => {
    setBusy(s.id);
    try {
      const r = await api.fetchWatchSource(s.id);
      if (UNHEALTHY.has(r.health)) toast.warning(`${s.regulator}: still ${HEALTH_LABEL[r.health].toLowerCase()}. No new notices could be read.`);
      else toast.success(`${s.regulator}: ${r.new_notices} new notice${r.new_notices === 1 ? "" : "s"}, ${r.new_revisions} new revision${r.new_revisions === 1 ? "" : "s"}.`);
    } catch {
      toast.error("The fetch could not run.");
    } finally {
      setBusy(null);
    }
  };

  if (error) return <ErrorState error={error} onRetry={reload} />;
  return (
    <div className="stack">
      <PageHeader title="Watch sources" subtitle="Where foreign regulator notices come from. Reviews depend on these staying readable." actions={<Button icon="plus" onClick={() => setAdd(true)}>Add a source</Button>} />

      {bad.length ? (
        <div className="adm-silence" style={{ borderRadius: "var(--r-lg)" }} role="alert">
          <Callout tone="held" icon="alert-triangle" title={`${bad.length} source${bad.length === 1 ? " is" : "s are"} not working. Silence is not safety.`}>
            A source that reports nothing is not the same as a regulator with nothing to say. Until each source below is fixed, reviews cannot rely on it to warn them.
            <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
              {bad.map((s) => (
                <li key={s.id}>
                  <strong>{s.regulator}</strong> ({HEALTH_LABEL[s.health].toLowerCase()}): {s.last_error ?? WHAT[s.health]}
                </li>
              ))}
            </ul>
          </Callout>
        </div>
      ) : data ? (
        <Callout tone="pass" title="Every automatic source is healthy">Manual sources still depend on a person checking them.</Callout>
      ) : null}

      <div className="adm-metrics">
        <Metric label="Sources" value={data ? items.length : "..."} />
        <Metric label="Healthy" value={items.filter((s) => s.health === "healthy").length} tone="pass" />
        <Metric label="Need attention" value={bad.length} tone={bad.length ? "held" : "pass"} />
        <Metric label="Manual" value={items.filter((s) => s.health === "manual").length} note="A person checks these" />
        <Metric label="Notices, 30 days" value={items.reduce((n, s) => n + (s.items_last_30_days ?? 0), 0)} />
      </div>

      <Panel title="Sources" padded={false}>
        <Table
          rows={data ? items : undefined}
          loading={loading}
          rowKey={(s) => s.id}
          caption="Watch sources"
          initialSort={{ key: "health", dir: "desc" }}
          empty={<EmptyState title="No sources" icon="radar" />}
          columns={[
            {
              key: "reg",
              header: "Regulator and source",
              sort: (s) => s.regulator,
              render: (s) => (
                <div style={UNHEALTHY.has(s.health) ? { borderLeft: "3px solid var(--held)", paddingLeft: 10 } : undefined}>
                  <div className="strong">{s.regulator}</div>
                  <div className="xsmall muted">{humanize(s.kind)}{s.url ? ` · ${s.url.replace(/^https?:\/\//, "")}` : ""}</div>
                  {s.notes ? <div className="xsmall muted">{s.notes}</div> : null}
                </div>
              ),
            },
            { key: "health", header: "Health", sort: (s) => (UNHEALTHY.has(s.health) ? 0 : s.health === "unchecked" ? 1 : 2), render: (s) => <HealthPill health={s.health} /> },
            { key: "last", header: "Last success", sort: (s) => s.last_success_at ?? "", render: (s) => (s.last_success_at ? <span className="small" title={formatDateTime(s.last_success_at)}>{formatRelative(s.last_success_at)}</span> : <span className="small muted">Never</span>) },
            { key: "err", header: "Message", hideBelow: 900, render: (s) => (s.last_error ? <span className="small" style={{ color: "var(--held-ink)" }}>{s.last_error}</span> : <span className="muted small">None</span>) },
            { key: "every", header: "Checked every", hideBelow: 1100, align: "right", render: (s) => <span className="num small">{s.kind === "manual" ? "By hand" : every(s.check_every_minutes)}</span> },
            { key: "floor", header: "Lowest severity", hideBelow: 1100, render: (s) => (s.severity_floor ? <span className="small">{humanize(s.severity_floor)}</span> : <span className="muted small">Set by the pack</span>) },
            { key: "n", header: "Notices, 30 days", hideBelow: 1100, align: "right", sort: (s) => s.items_last_30_days ?? 0, render: (s) => <span className="num small">{s.items_last_30_days ?? 0}</span> },
            { key: "act", header: <span className="visually-hidden">Action</span>, align: "right", render: (s) => <Button size="sm" icon="refresh" loading={busy === s.id} onClick={() => void fetchNow(s)}>Fetch now</Button> },
          ]}
        />
      </Panel>
      <AddSource open={add} onClose={() => setAdd(false)} />
    </div>
  );
}

const SOURCE_FIELDS: FieldMap = { regulator: "string", kind: "string", url: "string", check_every_minutes: "number", severity_floor: "string", notes: "string" };
const SOURCE_KINDS: WatchSource["kind"][] = ["web_page", "feed", "manual", "staged"];

function AddSource({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [regulator, setRegulator] = useState("");
  const [kind, setKind] = useState<WatchSource["kind"]>("web_page");
  const [url, setUrl] = useState("");
  const [mins, setMins] = useState(120);
  const [floor, setFloor] = useState("");
  const [notes, setNotes] = useState("");
  const fill = (item: unknown) => {
    const f = pickFields(item, SOURCE_FIELDS);
    if (typeof f.regulator === "string") setRegulator(f.regulator);
    if (SOURCE_KINDS.includes(f.kind as WatchSource["kind"])) setKind(f.kind as WatchSource["kind"]);
    setUrl(typeof f.url === "string" ? f.url : "");
    if (typeof f.check_every_minutes === "number") setMins(f.check_every_minutes);
    setFloor(f.severity_floor === "review" || f.severity_floor === "urgent" ? f.severity_floor : "");
    setNotes(typeof f.notes === "string" ? f.notes : "");
  };
  return (
    <ConfirmModal
      open={open}
      onClose={onClose}
      title="Add a watch source"
      confirmLabel="Add source"
      summary={
        <div className="stack-sm">
          <div>
            <FillFromJson onFill={fill} />
          </div>
          <Field label="Regulator" required>{(id) => <TextInput id={id} value={regulator} onChange={(e) => setRegulator(e.target.value)} />}</Field>
          <Field label="Kind">{(id) => <Select id={id} value={kind} onChange={(e) => setKind(e.target.value as WatchSource["kind"])} options={[{ value: "web_page", label: "Web page" }, { value: "feed", label: "Feed" }, { value: "manual", label: "Manual" }, { value: "staged", label: "Staged (simulation)" }]} />}</Field>
          {kind === "web_page" || kind === "feed" ? <Field label="Address">{(id) => <TextInput id={id} value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://" />}</Field> : null}
          <Field label="Check every (minutes)">{(id) => <TextInput id={id} type="number" min={5} value={mins} onChange={(e) => setMins(Number(e.target.value))} />}</Field>
          <Field label="Lowest severity" hint="Notices from this source never rank below it. The pack can only raise it.">{(id) => <Select id={id} value={floor} onChange={(e) => setFloor(e.target.value)} options={[{ value: "", label: "Set by the pack" }, { value: "review", label: "Review" }, { value: "urgent", label: "Urgent" }]} />}</Field>
          <Field label="Notes" hint="Optional.">{(id, d) => <TextInput id={id} aria-describedby={d} value={notes} onChange={(e) => setNotes(e.target.value)} />}</Field>
        </div>
      }
      onConfirm={async () => {
        await api.createWatchSource({ regulator: regulator.trim(), kind, url: url.trim() || null, check_every_minutes: mins, severity_floor: floor || null, notes: notes.trim() || null });
      }}
    />
  );
}

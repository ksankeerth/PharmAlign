// Audit log: the filtered, hash-chained record of every change. Read only.
// Filter by case, actor, action or object; open a row to see who acted with which
// role, what changed (before and after), the reason and the row hash.
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, type AuditEvent } from "../../api";
import { Button, Callout, PageHeader, type Column, DiffView, Drawer, EmptyState, ErrorState, Field, KeyValue, Panel, Select, Table, TextInput } from "../../components";
import { useApi } from "../../hooks/useApi";
import { formatDateTime } from "../../lib/format";
import { jsonDiff } from "../../lib/jsonDiff";
import { humanize, ROLE_LABEL } from "../../lib/labels";
import { ActorChip, type ActorType, usePeopleNames } from "./actors";
import "./audit.css";

const PAGE = 50;

function isObj(v: unknown): v is Record<string, unknown> {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

/** Before and after as a list of changed paths. A missing side is treated as empty. */
export function changesOf(before: unknown, after: unknown) {
  if ((before ?? null) === null && (after ?? null) === null) return [];
  const b = before ?? (isObj(after) ? {} : null);
  const a = after ?? (isObj(before) ? {} : null);
  return jsonDiff(b, a);
}

const roleLabel = (r: string | null | undefined) => (r ? ((ROLE_LABEL as Record<string, string>)[r] ?? humanize(r)) : "");

export default function AuditLogPage() {
  const [params, setParams] = useSearchParams();
  const { name } = usePeopleNames();
  const [caseId, setCaseId] = useState(params.get("case") ?? "");
  const [actorId, setActorId] = useState("");
  const [action, setAction] = useState("");
  const [objectId, setObjectId] = useState("");
  const [applied, setApplied] = useState({ caseId: params.get("case") ?? "", actorId: "", action: "", objectId: "" });
  const [extra, setExtra] = useState<AuditEvent[]>([]);
  const [cursor, setCursor] = useState<string | null | undefined>(undefined);
  const [loadingMore, setLoadingMore] = useState(false);
  const [open, setOpen] = useState<AuditEvent | null>(null);

  // Unfiltered recent rows give the filter lists their options.
  const options = useApi(() => api.listAuditEvents({ limit: 200 }), []);
  const cases = useApi(() => api.listCases({ limit: 200 }), []);
  const query = useMemo(
    () => ({ limit: PAGE, ...(applied.caseId ? { case_id: applied.caseId } : {}), ...(applied.actorId ? { actor_id: applied.actorId } : {}), ...(applied.action ? { action: applied.action } : {}), ...(applied.objectId ? { object_id: applied.objectId.trim() } : {}) }),
    [applied],
  );
  const { data, error, loading, reload } = useApi(() => api.listAuditEvents(query), [query]);

  useEffect(() => {
    setExtra([]);
    setCursor(undefined);
  }, [query]);

  // Opened from the verification screen: show the named row.
  const rowParam = params.get("row");
  useEffect(() => {
    if (!rowParam || !options.data) return;
    const found = options.data.items.find((e) => e.id === rowParam);
    if (found) setOpen(found);
  }, [rowParam, options.data]);

  const rows = data ? [...data.items, ...extra] : undefined;
  const nextCursor = cursor === undefined ? (data?.next_cursor ?? null) : cursor;

  const loadMore = async () => {
    if (!nextCursor) return;
    setLoadingMore(true);
    try {
      const page = await api.listAuditEvents({ ...query, cursor: nextCursor });
      setExtra((e) => [...e, ...page.items]);
      setCursor(page.next_cursor ?? null);
    } finally {
      setLoadingMore(false);
    }
  };

  const actorOptions = useMemo(() => {
    const seen = new Map<string, string>();
    for (const e of options.data?.items ?? []) if (!seen.has(e.actor_id)) seen.set(e.actor_id, `${name(e.actor_type, e.actor_id)} (${e.actor_type})`);
    return [...seen].map(([value, label]) => ({ value, label })).sort((a, b) => a.label.localeCompare(b.label));
  }, [options.data, name]);
  const actionOptions = useMemo(() => [...new Set((options.data?.items ?? []).map((e) => e.action))].sort().map((a) => ({ value: a, label: humanize(a) })), [options.data]);

  const filtered = Boolean(applied.caseId || applied.actorId || applied.action || applied.objectId);
  const apply = (e?: React.FormEvent) => {
    e?.preventDefault();
    setApplied({ caseId, actorId, action, objectId });
  };
  const clear = () => {
    setCaseId("");
    setActorId("");
    setAction("");
    setObjectId("");
    setApplied({ caseId: "", actorId: "", action: "", objectId: "" });
  };

  const columns: Column<AuditEvent>[] = [
    { key: "at", header: "When", render: (r) => <span className="nowrap">{formatDateTime(r.at)}</span>, sort: (r) => r.at, width: "120px" },
    { key: "actor", header: "Actor", render: (r) => <ActorChip type={r.actor_type as ActorType} id={r.actor_id} name={name(r.actor_type, r.actor_id)} />, sort: (r) => r.actor_id },
    { key: "role", header: "Role used", render: (r) => roleLabel(r.role_used) || <span className="muted">None</span>, hideBelow: 1180 },
    { key: "action", header: "Action", render: (r) => <span title={r.action}>{humanize(r.action)}</span>, sort: (r) => r.action },
    {
      key: "obj",
      header: "Object",
      render: (r) => (
        <span>
          <span className="muted small">{humanize(r.object_type)} </span>
          <code className="small">{r.object_id}</code>
        </span>
      ),
      hideBelow: 960,
    },
    { key: "case", header: "Case", render: (r) => (r.case_id ? <code className="small">{r.case_id}</code> : <span className="muted">None</span>), hideBelow: 1000 },
    { key: "open", header: <span className="visually-hidden">Details</span>, render: (r) => <Button size="sm" variant="ghost" onClick={() => setOpen(r)} aria-label={`Details of ${humanize(r.action)}, row ${r.id}`}>Details</Button>, align: "right" },
  ];

  return (
    <div className="stack">
      <PageHeader title="Audit log" subtitle="Every change is written here once, in order, and linked to the row before it. You can read it; you cannot change it." />

      <Panel title="Filter">
        <form className="audit-filters" onSubmit={apply}>
          <Field label="Case">
            {(id) => (
              <Select id={id} value={caseId} onChange={(e) => setCaseId(e.target.value)} options={[{ value: "", label: "Any case" }, ...(cases.data?.items ?? []).map((c) => ({ value: c.id, label: c.id }))]} />
            )}
          </Field>
          <Field label="Actor">{(id) => <Select id={id} value={actorId} onChange={(e) => setActorId(e.target.value)} options={[{ value: "", label: "Any actor" }, ...actorOptions]} />}</Field>
          <Field label="Action">{(id) => <Select id={id} value={action} onChange={(e) => setAction(e.target.value)} options={[{ value: "", label: "Any action" }, ...actionOptions]} />}</Field>
          <Field label="Object id" hint="Exact id, such as cr-1042-qual-07">
            {(id, d) => <TextInput id={id} aria-describedby={d} value={objectId} onChange={(e) => setObjectId(e.target.value)} placeholder="Any object" />}
          </Field>
          <div className="row">
            <Button type="submit" variant="primary" icon="filter">
              Apply
            </Button>
            <Button variant="ghost" onClick={clear} disabled={!filtered && !caseId && !actorId && !action && !objectId}>
              Clear
            </Button>
          </div>
        </form>
      </Panel>

      {rowParam && !open && options.data && !options.data.items.some((e) => e.id === rowParam) ? <Callout tone="look">Row {rowParam} is not among the latest 200 rows. Filter by its case or object to find it.</Callout> : null}

      <Panel
        title={`Events${rows ? ` (${rows.length}${nextCursor ? "+" : ""})` : ""}`}
        padded={Boolean(error)}
        footer={
          nextCursor ? (
            <div className="row-between">
              <span className="muted small">Newest first. More rows are available.</span>
              <Button onClick={loadMore} loading={loadingMore}>
                Load {PAGE} more
              </Button>
            </div>
          ) : undefined
        }
      >
        {error ? (
          <ErrorState error={error} onRetry={reload} />
        ) : (
          <Table
            caption="Audit events, newest first"
            rows={rows}
            loading={loading && !rows}
            rowKey={(r) => r.id}
            columns={columns}
            dense
            selectedKey={open?.id ?? null}
            onRowClick={(r) => setOpen(r)}
            empty={<EmptyState title={filtered ? "No events match these filters" : "No events yet"}>{filtered ? "Clear a filter to see more." : "Changes appear here as they happen."}</EmptyState>}
          />
        )}
      </Panel>

      <EventDrawer event={open} onClose={() => { setOpen(null); if (rowParam) { params.delete("row"); setParams(params, { replace: true }); } }} name={name} />
    </div>
  );
}

function EventDrawer({ event, onClose, name }: { event: AuditEvent | null; onClose: () => void; name: (type: string, id: string) => string }) {
  const changes = event ? changesOf(event.before, event.after) : [];
  return (
    <Drawer open={Boolean(event)} onClose={onClose} title={event ? humanize(event.action) : ""} subtitle={event ? `Row ${event.id} · ${formatDateTime(event.at)}` : undefined} width={680}>
      {event ? (
        <div className="stack">
          <KeyValue
            columns={2}
            items={[
              { label: "Actor", value: <ActorChip type={event.actor_type as ActorType} id={event.actor_id} name={name(event.actor_type, event.actor_id)} /> },
              { label: "Account", value: event.account_id, mono: true },
              { label: "Role used", value: roleLabel(event.role_used) },
              { label: "Action", value: <code>{event.action}</code> },
              { label: "Object", value: <span>{humanize(event.object_type)} <code>{event.object_id}</code></span> },
              { label: "Case", value: event.case_id ? <code>{event.case_id}</code> : null },
            ]}
          />
          <div>
            <h3 style={{ margin: "0 0 var(--sp-2)", fontSize: "var(--fs-md)" }}>What changed</h3>
            <DiffView changes={changes} empty={<p className="muted small">This row records an action without a before and after.</p>} />
          </div>
          <KeyValue items={[{ label: "Reason", value: event.reason }]} />
          <div>
            <div className="muted small">Row hash</div>
            <div className="hash">{event.row_hash ?? "None"}</div>
            <p className="muted xsmall" style={{ margin: "var(--sp-1) 0 0" }}>
              Computed from this row and the row before it, so changing either breaks the chain.
            </p>
          </div>
        </div>
      ) : null}
    </Drawer>
  );
}

// Adoptions: this country's decision on each document version, its legal status and
// authority level. The same shared document can hold a different status in another country.
import { useMemo, useState } from "react";
import { api, ApiError } from "../../api";
import type { Adoption, Document, LegalStatus } from "../../api/types";
import { useSession } from "../../app/session";
import { Button, Callout, Checkbox, ConfirmModal, Drawer, EmptyState, ErrorState, Field, KeyValue, PageHeader, Panel, Pill, Select, Table, type Tone, TextArea, TextInput, useToast } from "../../components";
import { useApi } from "../../hooks/useApi";
import { formatDate } from "../../lib/format";
import { loadAll, usePersonName } from "./ownerShared";

const STATUS: Record<LegalStatus, { label: string; tone: Tone; hint: string }> = {
  in_force: { label: "In force", tone: "pass", hint: "Can be cited as a requirement." },
  draft: { label: "Draft", tone: "look", hint: "Not yet binding. Context only." },
  superseded: { label: "Superseded", tone: "neutral", hint: "Replaced. Never cited as a requirement." },
  review_needed: { label: "Review needed", tone: "look", hint: "Status unclear. A person must decide." },
  not_applicable: { label: "Not applicable", tone: "neutral", hint: "Does not apply in this country." },
};
const LEVELS: Record<number, string> = { 1: "1: Binding law", 2: "2: Regulation or decree", 3: "3: Authority guideline", 4: "4: Reference guideline", 5: "5: Context only" };

export default function AdoptionsPage() {
  const { workspace } = useSession();
  const ws = workspace?.id ?? "";
  const name = usePersonName();
  const { data, error, loading, reload } = useApi(
    async () => {
      const [adoptions, docs] = await Promise.all([loadAll((cursor) => api.listAdoptions(ws, { cursor, limit: 200 })), loadAll((cursor) => api.listDocuments({ cursor, limit: 200 }))]);
      const byVersion = new Map<string, Document>();
      for (const d of docs) if (d.latest_version_id) byVersion.set(d.latest_version_id, d);
      const rows = adoptions.map((a) => ({ a, doc: byVersion.get(a.document_version_id) ?? null, undecided: false }));
      // A document this country has not decided on yet has no adoption, such as every document in a new country.
      const decided = new Set(adoptions.map((a) => a.document_version_id));
      for (const [versionId, doc] of byVersion) {
        if (!decided.has(versionId)) rows.push({ a: { workspace_id: ws, document_version_id: versionId, adopted: false, legal_status: "review_needed", authority_level: 5, notes: null, decided_by: "", decided_at: "" }, doc, undecided: true });
      }
      return rows;
    },
    [ws],
    { enabled: Boolean(ws) },
  );
  const [text, setText] = useState("");
  const [status, setStatus] = useState("");
  const [level, setLevel] = useState("");
  const [shelf, setShelf] = useState("");
  const [openKey, setOpenKey] = useState<string | null>(null);
  const rows = useMemo(
    () =>
      (data ?? []).filter(({ a, doc }) => {
        const q = text.trim().toLowerCase();
        if (q && !`${doc?.id ?? ""} ${doc?.title ?? ""} ${a.document_version_id}`.toLowerCase().includes(q)) return false;
        if (status && a.legal_status !== status) return false;
        if (level && String(a.authority_level) !== level) return false;
        if (shelf && doc?.shelf !== shelf) return false;
        return true;
      }),
    [data, text, status, level, shelf],
  );
  const open = data?.find((r) => r.a.document_version_id === openKey) ?? null;
  const toast = useToast();
  const [freezeOpen, setFreezeOpen] = useState(false);
  const [freezeLabel, setFreezeLabel] = useState("");
  const defaultLabel = `${workspace?.name ?? "Country"} library, ${formatDate(new Date().toISOString())}`;
  const freeze = async () => {
    const snap = await api.createSnapshot(ws, { label: freezeLabel.trim() || defaultLabel });
    toast.success(`Library frozen as ${snap.id}: ${snap.item_count} approved version${snap.item_count === 1 ? "" : "s"}`);
  };
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const { a, undecided } of data ?? []) if (!undecided) c[a.legal_status] = (c[a.legal_status] ?? 0) + 1;
    return c;
  }, [data]);
  return (
    <>
      <PageHeader
        title="Adoptions"
        subtitle={<>How {workspace?.name ?? "this country"} treats each document: whether it applies, its legal status and its authority level. Searches for a requirement use only what is in force here.</>}
        actions={
          <Button
            icon="layers"
            disabled={!ws}
            onClick={() => {
              setFreezeLabel(defaultLabel);
              setFreezeOpen(true);
            }}
          >
            Freeze the library
          </Button>
        }
      />
      <ConfirmModal
        open={freezeOpen}
        onClose={() => setFreezeOpen(false)}
        title="Freeze the library"
        confirmLabel="Freeze the library"
        summary={
          <div className="stack">
            <p style={{ margin: 0 }}>
              A snapshot fixes the approved versions that {workspace?.name ?? "this country"} uses for new cases, each with the legal status and level it has today. Later changes to adoptions do not alter it. Sign-off picks a snapshot.
            </p>
            <Field label="Snapshot name" required>
              {(id) => <TextInput id={id} value={freezeLabel} maxLength={500} onChange={(e) => setFreezeLabel(e.target.value)} data-autofocus />}
            </Field>
          </div>
        }
        onConfirm={freeze}
      />
      <div className="ow-stack">
        <div className="ow-tiles">
          {(Object.keys(STATUS) as LegalStatus[]).map((s) => (
            <button key={s} type="button" className="ow-tile" style={{ textAlign: "left", cursor: "pointer" }} aria-pressed={status === s} onClick={() => setStatus(status === s ? "" : s)}>
              <div className="small">{STATUS[s].label}</div>
              <div className="ow-count">{counts[s] ?? 0}</div>
            </button>
          ))}
        </div>
        <Panel title={`Documents${data ? ` (${rows.length} of ${data.length})` : ""}`} padded={Boolean(error)}>
          {error ? (
            <ErrorState error={error} onRetry={reload} />
          ) : (
            <>
              <div className="ow-filter" style={{ padding: "12px 16px 0" }}>
                <Field label="Search">{(id) => <TextInput id={id} value={text} placeholder="Title or id" onChange={(e) => setText(e.target.value)} />}</Field>
                <Field label="Legal status">{(id) => <Select id={id} value={status} onChange={(e) => setStatus(e.target.value)} options={[{ value: "", label: "Any" }, ...(Object.keys(STATUS) as LegalStatus[]).map((s) => ({ value: s, label: STATUS[s].label }))]} />}</Field>
                <Field label="Authority level">{(id) => <Select id={id} value={level} onChange={(e) => setLevel(e.target.value)} options={[{ value: "", label: "Any" }, ...[1, 2, 3, 4, 5].map((l) => ({ value: String(l), label: LEVELS[l] }))]} />}</Field>
                <Field label="Shelf">{(id) => <Select id={id} value={shelf} onChange={(e) => setShelf(e.target.value)} options={[{ value: "", label: "Any" }, { value: "shared", label: "Shared" }, { value: "country", label: "This country" }]} />}</Field>
              </div>
              <Table
                rows={rows}
                loading={loading}
                rowKey={(r) => r.a.document_version_id}
                caption="Adoptions"
                onRowClick={(r) => setOpenKey(r.a.document_version_id)}
                selectedKey={openKey}
                empty={<EmptyState title="No document matches" icon="book">Clear a filter to see more.</EmptyState>}
                columns={[
                  { key: "doc", header: "Document", sort: (r) => r.doc?.title ?? r.a.document_version_id, render: (r) => <><div><strong>{r.doc?.id ?? r.a.document_version_id}</strong></div><div className="small">{r.doc?.title}</div></> },
                  { key: "shelf", header: "Shelf", sort: (r) => r.doc?.shelf ?? "", render: (r) => (r.doc?.shelf === "shared" ? "Shared" : "This country"), hideBelow: 900 },
                  { key: "status", header: "Legal status", sort: (r) => r.a.legal_status, render: (r) => (r.undecided ? <Pill tone="neutral" size="sm">Not decided</Pill> : <Pill tone={STATUS[r.a.legal_status].tone} size="sm">{STATUS[r.a.legal_status].label}</Pill>) },
                  { key: "level", header: "Level", align: "right", sort: (r) => r.a.authority_level, render: (r) => <span title={LEVELS[r.a.authority_level]}>{r.a.authority_level}</span> },
                  { key: "adopted", header: "Applies here", sort: (r) => (r.a.adopted ? 1 : 0), render: (r) => (r.undecided ? "" : r.a.adopted ? "Yes" : "No") },
                  { key: "by", header: "Decided by", sort: (r) => r.a.decided_at, render: (r) => (r.undecided ? "" : <><div>{name(r.a.decided_by)}</div><div className="muted xsmall">{formatDate(r.a.decided_at)}</div></>), hideBelow: 1100 },
                  { key: "notes", header: "Notes", render: (r) => <span className="small">{r.a.notes ?? ""}</span>, hideBelow: 1100 },
                ]}
                dense
              />
            </>
          )}
        </Panel>
      </div>
      <Drawer open={Boolean(open)} onClose={() => setOpenKey(null)} title={open ? `${open.doc?.id ?? open.a.document_version_id}` : ""} subtitle={open?.doc?.title} width={560}>
        {open ? <AdoptionForm key={open.a.document_version_id} ws={ws} a={open.a} doc={open.doc} undecided={open.undecided} onDone={() => { setOpenKey(null); reload(); }} /> : null}
      </Drawer>
    </>
  );
}

function AdoptionForm({ ws, a, doc, undecided, onDone }: { ws: string; a: Adoption; doc: Document | null; undecided: boolean; onDone: () => void }) {
  const toast = useToast();
  const { workspace, workspaces } = useSession();
  const [adopted, setAdopted] = useState(a.adopted);
  const [legal, setLegal] = useState<LegalStatus>(a.legal_status);
  const [level, setLevel] = useState(a.authority_level);
  const [notes, setNotes] = useState(a.notes ?? "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const others = workspaces.filter((w) => w.id !== ws);
  const elsewhere = useApi(
    async () =>
      Promise.all(
        others.map(async (w) => {
          try {
            const list = await loadAll((cursor) => api.listAdoptions(w.id, { cursor, limit: 200 }));
            return { w, a: list.find((x) => x.document_version_id === a.document_version_id) ?? null };
          } catch {
            return { w, a: null };
          }
        }),
      ),
    [a.document_version_id],
    { enabled: doc?.shelf === "shared" && others.length > 0, live: false },
  );
  const changed = undecided || adopted !== a.adopted || legal !== a.legal_status || level !== a.authority_level || notes.trim() !== (a.notes ?? "");
  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      await api.setAdoption({ workspace_id: ws, version_id: a.document_version_id }, { adopted, legal_status: legal, authority_level: level, notes: notes.trim() || null });
      toast.success("Adoption saved");
      onDone();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Not saved.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="stack">
      <Callout tone="neutral" title={doc?.shelf === "shared" ? "A shared document" : "A document of this country"}>
        {doc?.shelf === "shared" ? `Your decision applies to ${workspace?.name ?? "this country"} only. Each country decides its own status and level for the same text.` : "Your decision applies to this country."}
      </Callout>
      <KeyValue columns={2} items={[{ label: "Issuer", value: doc?.issuer }, { label: "Jurisdiction", value: doc?.jurisdiction }, { label: "Version", value: <code>{a.document_version_id}</code> }, { label: "Last decided", value: undecided ? "Not decided yet" : formatDate(a.decided_at) }]} />
      <Checkbox checked={adopted} onChange={(e) => setAdopted(e.target.checked)} label="This document applies in this country" hint="A document that does not apply is never searched." />
      <Field label="Legal status" hint={STATUS[legal].hint}>
        {(id, d) => <Select id={id} aria-describedby={d} value={legal} onChange={(e) => setLegal(e.target.value as LegalStatus)} options={(Object.keys(STATUS) as LegalStatus[]).map((s) => ({ value: s, label: STATUS[s].label }))} />}
      </Field>
      <Field label="Authority level" hint="When two sources disagree, the lower level wins first.">
        {(id, d) => <Select id={id} aria-describedby={d} value={String(level)} onChange={(e) => setLevel(Number(e.target.value))} options={[1, 2, 3, 4, 5].map((l) => ({ value: String(l), label: LEVELS[l] }))} />}
      </Field>
      <Field label="Notes" hint="Why this status, for the next person.">{(id) => <TextArea id={id} rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} />}</Field>
      {doc?.shelf === "shared" && elsewhere.data ? (
        <div>
          <div className="ow-section">Other countries</div>
          <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
            {elsewhere.data.map(({ w, a: o }) => (
              <li key={w.id}>{w.name}: {o ? `${STATUS[o.legal_status].label}, level ${o.authority_level}${o.adopted ? "" : ", does not apply"}` : "No decision visible to you"}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {err ? <Callout tone="held" title="Not saved">{err}</Callout> : null}
      <div className="ow-actions">
        <Button variant="primary" disabled={!changed} loading={busy} onClick={save}>Save adoption</Button>
        <Button variant="ghost" onClick={onDone}>Cancel</Button>
      </div>
    </div>
  );
}

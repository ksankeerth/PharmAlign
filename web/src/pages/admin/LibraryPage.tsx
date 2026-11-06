// Document library: every public document, filtered by shelf, slot, review status,
// legal status and due-for-check, with clear flags.
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../../api";
import { toProblem } from "../../api/errors";
import type { Adoption, Document, LegalStatus, Slot } from "../../api/types";
import { Button, Callout, EmptyState, ErrorState, Field, FillFromJson, Modal, PageHeader, Panel, Pill, PrototypeNotice, Select, Table, TextInput, Checkbox, useToast } from "../../components";
import { useSession } from "../../app/session";
import { useApi, useMutation } from "../../hooks/useApi";
import { formatDate } from "../../lib/format";
import { humanize } from "../../lib/labels";
import { pickFields, type FieldMap } from "../../lib/fillJson";
import { DOC_TYPE_LABEL, LEGAL_LABEL, LegalPill, Metric, REVIEW_LABEL, ReviewPill } from "./shared";

const REVIEW_STATUSES = ["unreviewed", "approved", "approved_with_notes", "rejected", "needs_update"] as const;
const PAGE = 40;

const SHELVES = ["shared", "country"] as const;
const UPDATE_PATTERNS = ["fixed", "versioned", "periodic", "living", "point_in_time", "draft"] as const;
const SHARING = ["yes", "no", "unknown"] as const;

const FIELDS: FieldMap = {
  title: "string",
  title_local: "string",
  issuer: "string",
  jurisdiction: "string",
  shelf: "string",
  doc_type: "string",
  language: "string",
  source_url: "string",
  watch_url: "string",
  update_pattern: "string",
  sharing_rights: "string",
  slot_id: "string",
};

const EMPTY_FORM = {
  title: "",
  title_local: "",
  issuer: "",
  jurisdiction: "",
  shelf: "country",
  doc_type: "",
  language: "en",
  source_url: "",
  watch_url: "",
  update_pattern: "fixed",
  sharing_rights: "unknown",
  slot_id: "",
};
type Form = typeof EMPTY_FORM;

/** Keeps a value only if the list allows it, so a file with an unknown choice leaves the default. */
function oneOf(value: unknown, allowed: readonly string[]): string | undefined {
  return typeof value === "string" && allowed.includes(value) ? value : undefined;
}

function RegisterDocumentModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const nav = useNavigate();
  const toast = useToast();
  const { workspace } = useSession();
  const ws = workspace?.id ?? "";
  const slots = useApi(() => api.listSlots(ws, { limit: 200 }).then((r) => r.items), [ws], { enabled: open && Boolean(ws) });
  const [form, setForm] = useState<Form>(EMPTY_FORM);
  const create = useMutation((body: Parameters<typeof api.createDocument>[0]) => api.createDocument(body));
  const set = (k: keyof Form) => (v: string) => setForm((f) => ({ ...f, [k]: v }));

  const close = () => {
    setForm(EMPTY_FORM);
    create.reset();
    onClose();
  };
  const fill = (value: unknown) => {
    const got = pickFields(value, FIELDS) as Partial<Form>;
    setForm({
      ...EMPTY_FORM,
      ...got,
      shelf: oneOf(got.shelf, SHELVES) ?? EMPTY_FORM.shelf,
      update_pattern: oneOf(got.update_pattern, UPDATE_PATTERNS) ?? EMPTY_FORM.update_pattern,
      sharing_rights: oneOf(got.sharing_rights, SHARING) ?? EMPTY_FORM.sharing_rights,
    });
    create.reset();
  };

  const docType = form.doc_type.trim();
  const language = form.language.trim();
  const typeOk = /^[a-z][a-z0-9_-]{0,63}$/.test(docType);
  const languageOk = /^[a-z]{2}(-[A-Z]{2})?$/.test(language);
  const canCreate = form.title.trim() !== "" && form.issuer.trim() !== "" && form.jurisdiction.trim() !== "" && typeOk && languageOk;
  const optional = (v: string) => v.trim() || null;

  const submit = async () => {
    try {
      const doc = await create.run({
        title: form.title.trim(),
        title_local: optional(form.title_local),
        issuer: form.issuer.trim(),
        jurisdiction: form.jurisdiction.trim(),
        shelf: form.shelf as (typeof SHELVES)[number],
        doc_type: docType,
        language,
        source_url: optional(form.source_url),
        watch_url: optional(form.watch_url),
        update_pattern: form.update_pattern as (typeof UPDATE_PATTERNS)[number],
        sharing_rights: form.sharing_rights as (typeof SHARING)[number],
        slot_id: optional(form.slot_id),
      });
      toast.success(`Document ${doc.id} registered. Add its first version on the next page.`);
      close();
      nav(`/admin/library/${doc.id}`);
    } catch {
      /* shown in the dialog */
    }
  };

  const slotOptions = [{ value: "", label: "None" }, ...(slots.data ?? []).map((s) => ({ value: s.id, label: s.label }))];
  if (form.slot_id && !slotOptions.some((o) => o.value === form.slot_id)) slotOptions.push({ value: form.slot_id, label: form.slot_id });
  const problem = create.error ? toProblem(create.error) : null;

  return (
    <Modal
      open={open}
      onClose={close}
      busy={create.pending}
      size="lg"
      title="Register a document"
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={create.pending}>Cancel</Button>
          <Button variant="primary" icon="plus" loading={create.pending} disabled={!canCreate} onClick={() => void submit()}>
            Register document
          </Button>
        </>
      }
    >
      <div className="stack">
        <div className="row">
          <FillFromJson onFill={fill} />
          <span className="xsmall muted">Fills this form from a file. Check the values before you register.</span>
        </div>
        <div className="adm-opts">
          <div className="span-2">
            <Field label="Title" required>{(id) => <TextInput id={id} value={form.title} onChange={(e) => set("title")(e.target.value)} />}</Field>
          </div>
          <div className="span-2">
            <Field label="Title in the local language" hint="Optional.">{(id, d) => <TextInput id={id} aria-describedby={d} value={form.title_local} onChange={(e) => set("title_local")(e.target.value)} />}</Field>
          </div>
          <Field label="Issuer" required>{(id) => <TextInput id={id} value={form.issuer} onChange={(e) => set("issuer")(e.target.value)} />}</Field>
          <Field label="Jurisdiction" required>{(id) => <TextInput id={id} value={form.jurisdiction} onChange={(e) => set("jurisdiction")(e.target.value)} />}</Field>
          <Field
            label="Shelf"
            hint={form.shelf === "country" ? `Only ${workspace?.name ?? "this country"} sees it.` : "Every country sees it."}
          >
            {(id, d) => (
              <Select id={id} aria-describedby={d} value={form.shelf} onChange={(e) => set("shelf")(e.target.value)} options={[{ value: "country", label: "Country" }, { value: "shared", label: "Shared" }]} />
            )}
          </Field>
          <Field label="Type" required hint="One lowercase word, such as law or guideline." error={docType && !typeOk ? "Use lowercase letters, digits, dashes or underscores, starting with a letter." : undefined}>
            {(id, d) => <TextInput id={id} aria-describedby={d} value={form.doc_type} onChange={(e) => set("doc_type")(e.target.value)} placeholder="law" />}
          </Field>
          <Field label="Language" required hint="A two-letter code, such as en." error={language && !languageOk ? "Use a code like en or pt-BR." : undefined}>
            {(id, d) => <TextInput id={id} aria-describedby={d} value={form.language} onChange={(e) => set("language")(e.target.value)} />}
          </Field>
          <Field label="Update pattern" hint="How often the text changes. It sets when the next check is due.">
            {(id, d) => (
              <Select id={id} aria-describedby={d} value={form.update_pattern} onChange={(e) => set("update_pattern")(e.target.value)} options={UPDATE_PATTERNS.map((p) => ({ value: p, label: humanize(p) }))} />
            )}
          </Field>
          <div className="span-2">
            <Field label="Source URL" hint="The official page or file. Optional.">{(id, d) => <TextInput id={id} aria-describedby={d} value={form.source_url} onChange={(e) => set("source_url")(e.target.value)} inputMode="url" placeholder="https://" />}</Field>
          </div>
          <div className="span-2">
            <Field label="Watch page" hint="The page to check for changes. Optional.">{(id, d) => <TextInput id={id} aria-describedby={d} value={form.watch_url} onChange={(e) => set("watch_url")(e.target.value)} inputMode="url" placeholder="https://" />}</Field>
          </div>
          <Field label="Sharing rights" hint="Whether the publisher allows redistribution.">
            {(id, d) => (
              <Select id={id} aria-describedby={d} value={form.sharing_rights} onChange={(e) => set("sharing_rights")(e.target.value)} options={SHARING.map((r) => ({ value: r, label: humanize(r) }))} />
            )}
          </Field>
          <Field label="Slot" hint="The required slot this document is meant to fill. Optional.">
            {(id, d) => <Select id={id} aria-describedby={d} value={form.slot_id} onChange={(e) => set("slot_id")(e.target.value)} options={slotOptions} />}
          </Field>
        </div>
        {problem ? (
          <Callout tone="held" title={problem.title}>
            {problem.detail}
          </Callout>
        ) : null}
      </div>
    </Modal>
  );
}

interface Row {
  doc: Document;
  review: string;
  legal: LegalStatus | null;
  slot: Slot | null;
  due: boolean;
  flags: { key: string; label: string; tone: "held" | "look" | "neutral"; title: string }[];
}

async function loadAll(workspaceId: string): Promise<Row[]> {
  const pages = async (q: Parameters<typeof api.listDocuments>[0]) => {
    const out: Document[] = [];
    let cursor: string | undefined;
    do {
      const r = await api.listDocuments({ ...q, limit: 200, cursor });
      out.push(...r.items);
      cursor = r.next_cursor ?? undefined;
    } while (cursor);
    return out;
  };
  const [all, due, adoptions, slots, ...byReview] = await Promise.all([
    pages({}),
    pages({ due_for_check: true }),
    api.listAdoptions(workspaceId, { limit: 200 }).then(async (r) => {
      const items: Adoption[] = [...r.items];
      let cursor = r.next_cursor;
      while (cursor) {
        const n = await api.listAdoptions(workspaceId, { limit: 200, cursor });
        items.push(...n.items);
        cursor = n.next_cursor;
      }
      return items;
    }),
    api.listSlots(workspaceId, { limit: 200 }).then((r) => r.items).catch(() => [] as Slot[]),
    ...REVIEW_STATUSES.map((s) => pages({ review_status: s })),
  ]);
  const review = new Map<string, string>();
  byReview.forEach((docs, i) => docs.forEach((d) => review.set(d.id, REVIEW_STATUSES[i])));
  const dueIds = new Set(due.map((d) => d.id));
  const legal = new Map(adoptions.map((a) => [a.document_version_id, a.legal_status]));
  const slotOf = new Map(slots.filter((s) => s.document_id).map((s) => [s.document_id as string, s]));
  return all.map((doc) => {
    const l = legal.get(doc.latest_version_id ?? "") ?? null;
    const slot = slotOf.get(doc.id) ?? null;
    const flags: Row["flags"] = [];
    if (slot?.status === "replace") flags.push({ key: "nonofficial", label: "Non-official copy", tone: "held", title: slot.notes ?? "Replace with the official text." });
    if (slot?.status === "bad_copy") flags.push({ key: "badcopy", label: "Bad copy", tone: "held", title: slot.notes ?? "The copy is unusable." });
    if (l === "superseded") flags.push({ key: "superseded", label: "Superseded", tone: "held", title: "Replaced by a newer text. Cannot back a requirement." });
    if (l === "review_needed") flags.push({ key: "review", label: "Status under review", tone: "look", title: "Legal status needs a decision." });
    if (doc.sharing_rights === "unknown") flags.push({ key: "rights", label: "Rights unknown", tone: "neutral", title: "Sharing rights have not been checked." });
    if (doc.sharing_rights === "no") flags.push({ key: "norights", label: "Not shareable", tone: "look", title: "The publisher does not allow redistribution." });
    if (!doc.source_url) flags.push({ key: "nosource", label: "No source link", tone: "look", title: "No source URL is recorded." });
    return { doc, review: review.get(doc.id) ?? "unreviewed", legal: l, slot, due: dueIds.has(doc.id), flags };
  });
}

export default function LibraryPage() {
  const nav = useNavigate();
  const { workspace } = useSession();
  const wsId = workspace?.id ?? "ws-ae";
  const { data, error, loading, reload } = useApi(() => loadAll(wsId), [wsId]);
  const [q, setQ] = useState("");
  const [shelf, setShelf] = useState("");
  const [slot, setSlot] = useState("");
  const [review, setReview] = useState("");
  const [legal, setLegal] = useState("");
  const [due, setDue] = useState(false);
  const [flaggedOnly, setFlaggedOnly] = useState(false);
  const [page, setPage] = useState(0);
  const [registerOpen, setRegisterOpen] = useState(false);

  const slots = useMemo(() => {
    const m = new Map<string, Slot>();
    data?.forEach((r) => r.slot && m.set(r.slot.id, r.slot));
    return [...m.values()];
  }, [data]);

  const rows = useMemo(() => {
    const term = q.trim().toLowerCase();
    return (data ?? []).filter(
      (r) =>
        (!term || r.doc.id.toLowerCase().includes(term) || r.doc.title.toLowerCase().includes(term) || r.doc.issuer.toLowerCase().includes(term)) &&
        (!shelf || r.doc.shelf === shelf) &&
        (!slot || (slot === "any" ? r.slot !== null : r.slot?.id === slot)) &&
        (!review || r.review === review) &&
        (!legal || (legal === "none" ? r.legal === null : r.legal === legal)) &&
        (!due || r.due) &&
        (!flaggedOnly || r.flags.some((f) => f.tone !== "neutral")),
    );
  }, [data, q, shelf, slot, review, legal, due, flaggedOnly]);

  const pages = Math.max(1, Math.ceil(rows.length / PAGE));
  const cur = Math.min(page, pages - 1);
  const shown = rows.slice(cur * PAGE, cur * PAGE + PAGE);
  const filtered = Boolean(q || shelf || slot || review || legal || due || flaggedOnly);
  const clear = () => {
    setQ("");
    setShelf("");
    setSlot("");
    setReview("");
    setLegal("");
    setDue(false);
    setFlaggedOnly(false);
    setPage(0);
  };
  const upd = <T,>(set: (v: T) => void) => (v: T) => {
    set(v);
    setPage(0);
  };

  const all = data ?? [];
  return (
    <div className="stack">
      <PageHeader
        title="Document library"
        subtitle="Public documents only. Company dossiers are never stored here, so this screen shows catalogue facts and health, not case content."
        actions={
          <Button variant="primary" icon="plus" onClick={() => setRegisterOpen(true)}>
            Register a document
          </Button>
        }
      />
      <RegisterDocumentModal open={registerOpen} onClose={() => setRegisterOpen(false)} />
      <PrototypeNotice />
      <div className="adm-metrics">
        <Metric label="Documents" value={data ? all.length : "..."} note={data ? `${all.filter((r) => r.doc.shelf === "shared").length} shared, ${all.filter((r) => r.doc.shelf === "country").length} country` : undefined} />
        <Metric label="Awaiting review" value={data ? all.filter((r) => r.review === "unreviewed").length : "..."} tone="look" note="Agents never see these" />
        <Metric label="Due for a check" value={data ? all.filter((r) => r.due).length : "..."} tone="look" />
        <Metric label="Need replacing" value={data ? all.filter((r) => r.flags.some((f) => f.key === "nonofficial" || f.key === "badcopy" || f.key === "superseded")).length : "..."} tone="held" note="Non-official, bad copy or superseded" />
        <Metric label="Rights unknown" value={data ? all.filter((r) => r.doc.sharing_rights === "unknown").length : "..."} />
      </div>

      <Panel padded>
        <form className="adm-toolbar" role="search" aria-label="Filter documents" onSubmit={(e) => e.preventDefault()}>
          <Field label="Search">{(id) => <TextInput id={id} value={q} onChange={(e) => upd(setQ)(e.target.value)} placeholder="Title, id or issuer" />}</Field>
          <Field label="Shelf">
            {(id) => (
              <Select id={id} value={shelf} onChange={(e) => upd(setShelf)(e.target.value)} options={[{ value: "", label: "All shelves" }, { value: "shared", label: "Shared" }, { value: "country", label: "Country" }]} />
            )}
          </Field>
          <Field label="Slot">
            {(id) => (
              <Select id={id} value={slot} onChange={(e) => upd(setSlot)(e.target.value)} options={[{ value: "", label: "Any" }, { value: "any", label: "Fills a slot" }, ...slots.map((s) => ({ value: s.id, label: s.label }))]} />
            )}
          </Field>
          <Field label="Review status">
            {(id) => (
              <Select id={id} value={review} onChange={(e) => upd(setReview)(e.target.value)} options={[{ value: "", label: "Any" }, ...REVIEW_STATUSES.map((s) => ({ value: s, label: REVIEW_LABEL[s] }))]} />
            )}
          </Field>
          <Field label="Legal status">
            {(id) => (
              <Select
                id={id}
                value={legal}
                onChange={(e) => upd(setLegal)(e.target.value)}
                options={[{ value: "", label: "Any" }, ...(Object.keys(LEGAL_LABEL) as LegalStatus[]).map((s) => ({ value: s, label: LEGAL_LABEL[s] })), { value: "none", label: "Not adopted" }]}
              />
            )}
          </Field>
          <Checkbox checked={due} onChange={(e) => upd(setDue)(e.target.checked)} label="Due for a check" />
          <Checkbox checked={flaggedOnly} onChange={(e) => upd(setFlaggedOnly)(e.target.checked)} label="Needs action" />
          {filtered ? (
            <Button variant="ghost" size="sm" icon="x" onClick={clear}>
              Clear filters
            </Button>
          ) : null}
        </form>
        <p className="xsmall muted" style={{ marginTop: 8 }}>
          Legal status is the decision for {workspace?.name ?? "the active country"}. Each country decides for itself.
        </p>
      </Panel>

      {error ? (
        <ErrorState error={error} onRetry={reload} />
      ) : (
        <Panel title={`${rows.length} of ${all.length} documents`} padded={false}>
          <Table
            rows={data ? shown : undefined}
            loading={loading}
            rowKey={(r) => r.doc.id}
            caption="Documents"
            dense
            onRowClick={(r) => nav(`/admin/library/${r.doc.id}`)}
            empty={<EmptyState title="No documents match" icon="search" action={<Button onClick={clear}>Clear filters</Button>}>Change or clear a filter.</EmptyState>}
            columns={[
              { key: "id", header: "Id", render: (r) => <code>{r.doc.id}</code>, sort: (r) => r.doc.id, width: "74px" },
              {
                key: "title",
                header: "Title",
                sort: (r) => r.doc.title,
                render: (r) => (
                  <div style={{ minWidth: 220 }}>
                    <div className="strong">{r.doc.title}</div>
                    <div className="xsmall muted">
                      {r.doc.issuer} · {DOC_TYPE_LABEL(r.doc.doc_type)} · {r.doc.language.toUpperCase()}
                    </div>
                  </div>
                ),
              },
              { key: "shelf", header: "Shelf", render: (r) => <Pill tone={r.doc.shelf === "shared" ? "accent" : "neutral"} size="sm">{r.doc.shelf === "shared" ? "Shared" : "Country"}</Pill>, hideBelow: 900 },
              { key: "slot", header: "Slot", render: (r) => (r.slot ? <span className="small">{r.slot.label}</span> : <span className="muted small">None</span>), hideBelow: 1100 },
              { key: "review", header: "Review", render: (r) => <ReviewPill status={r.review} />, sort: (r) => r.review },
              { key: "legal", header: "Legal status", render: (r) => <LegalPill status={r.legal} />, sort: (r) => r.legal ?? "z", hideBelow: 900 },
              {
                key: "flags",
                header: "Flags",
                render: (r) =>
                  r.flags.length ? (
                    <div className="row" style={{ gap: 4 }}>
                      {r.flags.map((f) => (
                        <Pill key={f.key} tone={f.tone} size="sm" title={f.title} icon={f.tone === "held" ? "alert-triangle" : f.tone === "look" ? "eye" : undefined}>
                          {f.label}
                        </Pill>
                      ))}
                    </div>
                  ) : (
                    <span className="muted small">None</span>
                  ),
              },
              {
                key: "next",
                header: "Next check",
                sort: (r) => r.doc.next_check_on ?? "9999",
                hideBelow: 1100,
                render: (r) => (r.doc.next_check_on ? <span className={`small nowrap ${r.due ? "strong" : ""}`}>{formatDate(r.doc.next_check_on)}{r.due ? " (due)" : ""}</span> : <span className="muted small">Not scheduled</span>),
              },
            ]}
          />
          {rows.length > PAGE ? (
            <div className="adm-pager small">
              <span className="muted">
                Page {cur + 1} of {pages}
              </span>
              <Button size="sm" icon="chevron-left" disabled={cur === 0} onClick={() => setPage(cur - 1)} aria-label="Previous page" />
              <Button size="sm" icon="chevron-right" disabled={cur >= pages - 1} onClick={() => setPage(cur + 1)} aria-label="Next page" />
            </div>
          ) : null}
        </Panel>
      )}
    </div>
  );
}

// Document reviews: versions waiting for a person to accept them into the library.
// A reviewer sees the quality report and its flags, then approves, approves with notes,
// asks for an update or rejects. Nobody reviews a version they added.
import { useState } from "react";
import { api, ApiError } from "../../api";
import type { Document, DocumentVersion, GuardResult, QualityReport } from "../../api/types";
import { useSession } from "../../app/session";
import { Button, Callout, Drawer, EmptyState, ErrorState, Field, GuardList, KeyValue, PageHeader, Panel, Pill, Select, Table, TextArea, useToast } from "../../components";
import { useApi } from "../../hooks/useApi";
import { formatBytes, formatDate, formatDateTime } from "../../lib/format";
import { humanize } from "../../lib/labels";
import { QualityPill, usePersonName } from "./ownerShared";
import { DECISION_DONE, DECISION_LABEL, needsNotes, REVIEW_STATUS_LABEL, type ReviewDecision, reviewReadiness } from "./reviewLogic";

interface Row {
  doc: Document;
  version: DocumentVersion;
  quality: QualityReport | null;
}

const FILTERS: { value: DocumentVersion["review_status"]; label: string }[] = [
  { value: "unreviewed", label: "Waiting for review" },
  { value: "needs_update", label: "Needs an update" },
  { value: "rejected", label: "Rejected" },
  { value: "approved_with_notes", label: "Approved with notes" },
];

async function load(status: DocumentVersion["review_status"]): Promise<Row[]> {
  const docs = (await api.listDocuments({ review_status: status, limit: 200 })).items.filter((d) => d.latest_version_id);
  return Promise.all(
    docs.map(async (doc) => {
      const [version, quality] = await Promise.all([api.getDocumentVersion(doc.latest_version_id!), api.getQualityReport(doc.latest_version_id!).catch(() => null)]);
      return { doc, version, quality };
    }),
  );
}

export default function DocumentReviewsPage() {
  const name = usePersonName();
  const [status, setStatus] = useState<DocumentVersion["review_status"]>("unreviewed");
  const { data, error, loading, reload } = useApi(() => load(status), [status]);
  const [openId, setOpenId] = useState<string | null>(null);
  const open = data?.find((r) => r.version.id === openId) ?? null;
  return (
    <>
      <PageHeader
        title="Document reviews"
        subtitle="A document version enters this country's library only after a second person has read its quality report and accepted it."
      />
      <div className="ow-stack">
        <div className="ow-filter" style={{ marginBottom: 0 }}>
          <Field label="Show">
            {(id) => <Select id={id} value={status} onChange={(e) => { setStatus(e.target.value as DocumentVersion["review_status"]); setOpenId(null); }} options={FILTERS} />}
          </Field>
        </div>
        <Panel title={`${FILTERS.find((f) => f.value === status)?.label}${data ? ` (${data.length})` : ""}`} padded={Boolean(error)}>
          {error ? (
            <ErrorState error={error} onRetry={reload} />
          ) : (
            <Table
              rows={data}
              loading={loading}
              rowKey={(r) => r.version.id}
              caption="Document versions"
              onRowClick={(r) => setOpenId(r.version.id)}
              selectedKey={openId}
              empty={<EmptyState title="Nothing waiting" icon="check">{status === "unreviewed" ? "Every document version has been reviewed." : "No versions in this state."}</EmptyState>}
              columns={[
                { key: "doc", header: "Document", sort: (r) => r.doc.title, render: (r) => <><div><strong>{r.doc.id}</strong> <span className="muted">{r.version.version_label ?? ""}</span></div><div className="small">{r.doc.title}</div></> },
                { key: "issuer", header: "Issuer", sort: (r) => r.doc.issuer, render: (r) => <span className="small">{r.doc.issuer}</span>, hideBelow: 1100 },
                { key: "shelf", header: "Shelf", sort: (r) => r.doc.shelf, render: (r) => (r.doc.shelf === "shared" ? "Shared" : "This country"), hideBelow: 1100 },
                { key: "by", header: "Added by", sort: (r) => name(r.version.added_by), render: (r) => <><div>{name(r.version.added_by)}</div><div className="muted xsmall">{formatDateTime(r.version.added_at)}</div></> },
                { key: "q", header: "Quality", sort: (r) => r.version.quality_status ?? "", render: (r) => <><QualityPill status={r.version.quality_status} />{r.version.held ? <> <Pill tone="held" size="sm" title="Hidden text with an instruction was found. A person looks before approval.">Held</Pill></> : null}</> },
                { key: "flags", header: "Flags", render: (r) => <FlagSummary q={r.quality} /> },
                { key: "st", header: "Status", render: (r) => REVIEW_STATUS_LABEL[r.version.review_status], hideBelow: 900 },
              ]}
              dense
            />
          )}
        </Panel>
      </div>
      <Drawer open={Boolean(open)} onClose={() => setOpenId(null)} title={open ? `${open.doc.id}: ${open.doc.title}` : ""} subtitle={open ? `Version ${open.version.version_label ?? open.version.id}` : undefined} width={620}>
        {open ? <ReviewForm row={open} onDone={() => { setOpenId(null); reload(); }} /> : null}
      </Drawer>
    </>
  );
}

function FlagSummary({ q }: { q: QualityReport | null }) {
  if (!q) return <span className="muted">None</span>;
  const flagged = q.checks.filter((c) => c.status !== "pass");
  if (!flagged.length) return <span className="muted">None</span>;
  return (
    <span className="small">
      {flagged.slice(0, 2).map((c) => humanize(c.key)).join(", ")}
      {flagged.length > 2 ? ` and ${flagged.length - 2} more` : ""}
    </span>
  );
}

function ReviewForm({ row, onDone }: { row: Row; onDone: () => void }) {
  const { me } = useSession();
  const name = usePersonName();
  const toast = useToast();
  const { doc, version, quality } = row;
  const [decision, setDecision] = useState<ReviewDecision>("approved");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [refused, setRefused] = useState<GuardResult[] | null>(null);
  const [fail, setFail] = useState<string | null>(null);
  const readiness = reviewReadiness(version, me?.person_id, decision, notes);
  const blocked = readiness.filter((g) => !g.passed);
  const official = quality?.checks.find((c) => c.key === "official_source");

  const submit = async () => {
    setBusy(true);
    setRefused(null);
    setFail(null);
    try {
      await api.reviewDocumentVersion(version.id, { decision, notes: notes.trim() || null });
      toast.success(`${doc.id} ${DECISION_DONE[decision]}`);
      onDone();
    } catch (e) {
      if (e instanceof ApiError && e.failed.length) setRefused(e.failed);
      else setFail(e instanceof Error ? e.message : "The review was not saved.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack">
      <KeyValue
        columns={2}
        items={[
          { label: "Issuer", value: doc.issuer },
          { label: "Jurisdiction", value: doc.jurisdiction },
          { label: "Shelf", value: doc.shelf === "shared" ? "Shared across countries" : "This country only" },
          { label: "File", value: <>{version.filename} <span className="muted small">({formatBytes(version.size_bytes)})</span></> },
          { label: "Published", value: formatDate(version.published_on) },
          { label: "Added", value: `${name(version.added_by)}, ${formatDateTime(version.added_at)}` },
          { label: "Source", value: doc.source_url ? <a href={doc.source_url} target="_blank" rel="noreferrer">Open the source</a> : "No link" },
          { label: "Sharing rights", value: humanize(doc.sharing_rights) },
        ]}
      />

      <Panel title="Quality report" actions={<><QualityPill status={quality?.status ?? version.quality_status} />{quality?.held || version.held ? <Pill tone="held">Held for a person</Pill> : null}</>}>
        {quality ? (
          <div className="stack-sm">
            <ul className="ow-order" aria-label="Quality checks">
              {quality.checks.map((c) => (
                <li key={c.key}>
                  <span style={{ width: 96 }}><QualityPill status={c.status} /></span>
                  <span className="ow-grow"><strong>{humanize(c.key)}</strong>{c.detail ? <div className="small muted">{c.detail}</div> : null}</span>
                </li>
              ))}
            </ul>
            {quality.flagged_pages.length ? (
              <div>
                <div className="ow-section">Flagged pages</div>
                <div className="ow-chips">
                  {quality.flagged_pages.map((f) => <Pill key={f.page} tone="look" size="sm" title={f.detail ?? undefined}>{`Page ${f.page}: ${humanize(f.reason)}`}</Pill>)}
                </div>
              </div>
            ) : null}
            {quality.expected_articles ? <p className="small muted" style={{ margin: 0 }}>Articles found: {quality.found_articles} of {quality.expected_articles}. Words lost: {quality.words_lost ?? 0}.</p> : null}
          </div>
        ) : (
          <p className="muted small">No quality report yet. Run a trial ingest from the library first.</p>
        )}
      </Panel>

      {official && official.status !== "pass" ? <Callout tone="look" title="Not an official copy">{official.detail} A person must accept it explicitly before it can be cited.</Callout> : null}

      <Panel title="Your decision">
        <div className="stack">
          <div className="ow-autonomy" role="radiogroup" aria-label="Decision">
            {(Object.keys(DECISION_LABEL) as ReviewDecision[]).map((d) => (
              <label className="ow-radio" key={d}>
                <input type="radio" name="decision" checked={decision === d} onChange={() => { setDecision(d); setRefused(null); }} />
                <span>{DECISION_LABEL[d]}<small>{needsNotes(d) ? "Notes required." : "No notes needed."}</small></span>
              </label>
            ))}
          </div>
          <Field label={needsNotes(decision) ? "Notes" : "Notes (optional)"} required={needsNotes(decision)} hint="Saved with your name in the audit log.">
            {(id, d) => <TextArea id={id} aria-describedby={d} rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} />}
          </Field>
          <GuardList guards={readiness.map((g) => ({ ...g, check_id: null }))} title={blocked.length ? `${blocked.length} thing${blocked.length === 1 ? "" : "s"} to sort out` : "Ready to submit"} />
          {refused ? <GuardList guards={refused} title="The server refused this review" onlyFailed /> : null}
          {fail ? <Callout tone="held" title="Not saved">{fail}</Callout> : null}
          <div className="ow-actions">
            <Button variant="primary" loading={busy} disabled={!readiness.find((g) => g.guard === "notes_given")?.passed} onClick={submit}>{`Submit: ${DECISION_LABEL[decision].toLowerCase()}`}</Button>
          </div>
        </div>
      </Panel>
    </div>
  );
}

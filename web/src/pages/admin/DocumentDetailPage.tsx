// Document detail: versions (never edited or deleted), source, duplicate detection,
// next check date, and adding a version by URL or upload.
import { useState } from "react";
import { useParams } from "react-router-dom";
import { api, isApiError } from "../../api";
import type { DocumentVersion } from "../../api/types";
import { Button, ButtonLink, Callout, EmptyState, ErrorState, Field, KeyValue, PrototypeNotice, PageHeader, Panel, Pill, SkeletonLines, Table, Tabs, TextInput, useToast } from "../../components";
import { useSession } from "../../app/session";
import { useApi, useMutation } from "../../hooks/useApi";
import { toProblem } from "../../api/errors";
import { formatBytes, formatDate, formatDateTime, shortFingerprint } from "../../lib/format";
import { humanize } from "../../lib/labels";
import { DOC_TYPE_LABEL, LegalPill, QualityPill, ReviewPill, usePeopleNames } from "./shared";

const INTERVAL: Record<string, number | null> = { fixed: 90, versioned: 90, periodic: 180, living: 30, draft: 30, point_in_time: null };

export default function DocumentDetailPage() {
  const { documentId = "" } = useParams();
  const { workspace } = useSession();
  const names = usePeopleNames();
  const toast = useToast();
  const { data: doc, error, reload } = useApi(() => api.getDocument(documentId), [documentId]);
  const adoptions = useApi(() => api.listAdoptions(workspace?.id ?? "ws-ae", { limit: 200 }), [workspace?.id]);
  const slots = useApi(() => api.listSlots(workspace?.id ?? "ws-ae", { limit: 200 }).catch(() => ({ items: [] })), [workspace?.id]);
  const [tab, setTab] = useState("url");
  const [url, setUrl] = useState("");
  const [label, setLabel] = useState("");
  const [published, setPublished] = useState("");
  const [effective, setEffective] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [note, setNote] = useState("");
  const add = useMutation((body: Parameters<typeof api.addDocumentVersion>[1]) => api.addDocumentVersion(documentId, body));

  if (error) return <ErrorState error={error} onRetry={reload} />;
  if (!doc) return <SkeletonLines lines={8} />;

  const slot = slots.data?.items.find((s) => s.document_id === doc.id) ?? null;
  const latest = doc.versions.find((v) => v.id === doc.latest_version_id) ?? doc.versions[0];
  const adoption = adoptions.data?.items.find((a) => a.document_version_id === latest?.id);
  const today = new Date().toISOString().slice(0, 10);
  const due = Boolean(doc.next_check_on && doc.next_check_on <= today);
  const interval = INTERVAL[doc.update_pattern];
  const hashCounts = new Map<string, number>();
  doc.versions.forEach((v) => hashCounts.set(v.file_hash, (hashCounts.get(v.file_hash) ?? 0) + 1));
  const dupes = doc.versions.filter((v) => v.duplicate_of || (hashCounts.get(v.file_hash) ?? 0) > 1);

  const submit = async () => {
    const meta = { version_label: label.trim() || null, published_on: published || null, effective_on: effective || null };
    try {
      // A URL goes as JSON and the server fetches it. An upload goes as a form with the file itself.
      let body: Parameters<typeof api.addDocumentVersion>[1];
      if (tab === "url") body = { source_url: url.trim(), ...meta };
      else {
        if (!file) return;
        const form = new FormData();
        form.append("file", file, file.name);
        form.append("provenance_note", note.trim());
        for (const [k, v] of Object.entries(meta)) if (v) form.append(k, v);
        body = form;
      }
      const v = await add.run(body);
      toast.success(`Version ${v.id} added. It starts unreviewed until a Process Owner reviews it.`);
      setUrl("");
      setLabel("");
      setPublished("");
      setEffective("");
      setFile(null);
      setNote("");
      reload();
    } catch {
      /* shown below */
    }
  };
  const canSubmit = tab === "url" ? url.trim().length > 6 : Boolean(file) && note.trim().length > 3;

  return (
    <div className="stack">
      <PageHeader
        breadcrumbs={[{ label: "Document library", to: "/admin/library" }, { label: doc.id }]}
        eyebrow={<code>{doc.id}</code>}
        title={doc.title}
        subtitle={`${doc.issuer} · ${doc.jurisdiction}`}
        status={
          <>
            <Pill tone={doc.shelf === "shared" ? "accent" : "neutral"}>{doc.shelf === "shared" ? "Shared shelf" : "Country shelf"}</Pill>
            <LegalPill status={adoption?.legal_status} />
          </>
        }
      />

      <PrototypeNotice sourceUrl={doc.source_url} />

      {slot && (slot.status === "replace" || slot.status === "bad_copy") ? (
        <Callout tone="held" title={slot.status === "replace" ? "Non-official copy: replace" : "Bad copy"}>
          {slot.notes ?? "This copy should be replaced with the official text."} Add the official file below as a new version; the old version stays for the record.
        </Callout>
      ) : null}
      {adoption?.legal_status === "superseded" ? (
        <Callout tone="held" title="Superseded">
          A newer text replaced this one. It can be kept as context but cannot back a requirement.
        </Callout>
      ) : null}
      {doc.sharing_rights === "unknown" ? (
        <Callout tone="neutral" title="Sharing rights unknown">
          The publisher's terms have not been checked. The text stays in the library for search and is not redistributed.
        </Callout>
      ) : null}

      <div className="grid-2">
        <Panel title="Source and details">
          <KeyValue
            columns={2}
            items={[
              { label: "Type", value: DOC_TYPE_LABEL(doc.doc_type) },
              { label: "Language", value: doc.language.toUpperCase() },
              { label: "Update pattern", value: humanize(doc.update_pattern) },
              { label: "Sharing rights", value: humanize(doc.sharing_rights) },
              { label: "Slot", value: slot ? `${slot.label} (${humanize(slot.status)})` : null },
              { label: "Registered", value: formatDate(doc.created_at) },
              {
                label: "Source",
                value: doc.source_url ? (
                  <a href={doc.source_url} target="_blank" rel="noreferrer noopener">
                    {doc.source_url.replace(/^https?:\/\//, "").slice(0, 52)}
                  </a>
                ) : (
                  "No source link recorded"
                ),
              },
              {
                label: "Watch page",
                value: doc.watch_url ? (
                  <a href={doc.watch_url} target="_blank" rel="noreferrer noopener">
                    {doc.watch_url.replace(/^https?:\/\//, "").slice(0, 52)}
                  </a>
                ) : null,
              },
            ]}
          />
        </Panel>
        <Panel title="Freshness">
          <KeyValue
            columns={2}
            items={[
              { label: "Next check", value: doc.next_check_on ? `${formatDate(doc.next_check_on)}${due ? " (due now)" : ""}` : "Not scheduled" },
              { label: "Check interval", value: interval ? `Every ${interval} days` : "Checked when its watch page changes" },
              { label: "Latest version", value: latest ? latest.version_label ?? latest.id : "None yet" },
              { label: "Versions kept", value: doc.versions.length },
            ]}
          />
          <p className="small muted" style={{ marginTop: 10 }}>
            A replaced text flags every rule and check that cites it, so a past decision can still show the law that applied.
          </p>
        </Panel>
      </div>

      <Panel title="Versions" actions={<span className="small muted">Never edited or deleted</span>} padded={false}>
        <Table
          rows={doc.versions}
          rowKey={(v) => v.id}
          dense
          caption="Versions of this document"
          empty={<EmptyState title="No versions yet" icon="file">Add the first version below.</EmptyState>}
          columns={[
            { key: "v", header: "Version", render: (v: DocumentVersion) => <div><div className="strong">{v.version_label ?? "Unlabelled"}</div><code className="xsmall">{v.id}</code>{v.id === doc.latest_version_id ? <> <Pill tone="accent" size="sm">Latest</Pill></> : null}</div> },
            { key: "file", header: "File", hideBelow: 900, render: (v) => <div className="small"><div className="truncate" style={{ maxWidth: 220 }}>{v.filename}</div><div className="muted xsmall">{formatBytes(v.size_bytes)} · {v.content_type.replace("application/", "").replace("text/", "")}</div></div> },
            { key: "added", header: "Added", hideBelow: 1100, render: (v) => <div className="small"><div>{formatDateTime(v.added_at)}</div><div className="muted xsmall">{names(v.added_by)} · {humanize(v.source)}</div></div> },
            { key: "review", header: "Review", render: (v) => <ReviewPill status={v.review_status} /> },
            { key: "quality", header: "Quality", render: (v) => <QualityPill status={v.quality_status} /> },
            {
              key: "act",
              header: <span className="visually-hidden">Actions</span>,
              align: "right",
              render: (v) => (
                <div className="row" style={{ justifyContent: "flex-end" }}>
                  <ButtonLink size="sm" to={`/admin/library/versions/${v.id}/ingest`}>Ingest</ButtonLink>
                  <ButtonLink size="sm" variant="ghost" to={`/admin/library/versions/${v.id}/quality`}>Quality</ButtonLink>
                </div>
              ),
            },
          ]}
        />
      </Panel>

      <div className="grid-2">
        <Panel title="Duplicate detection">
          <p className="small">
            A new file is compared by content hash, not by name. If the bytes match a version already stored, it is refused and nothing is added.
          </p>
          <div style={{ marginTop: 10 }}>
            {dupes.length ? (
              <Callout tone="look" title="Matching content found">
                {dupes.map((v) => `${v.id}${v.duplicate_of ? ` repeats ${v.duplicate_of}` : " shares a hash"}`).join("; ")}
              </Callout>
            ) : (
              <Callout tone="pass" title="No duplicates">
                None of the {doc.versions.length} stored version{doc.versions.length === 1 ? "" : "s"} share a hash.
              </Callout>
            )}
          </div>
          <table className="table table-dense" style={{ marginTop: 10 }}>
            <caption className="visually-hidden">Content hash of each version</caption>
            <thead><tr><th scope="col">Version</th><th scope="col">Content hash</th></tr></thead>
            <tbody>
              {doc.versions.map((v) => (
                <tr key={v.id}><td><code>{v.id}</code></td><td><code title={v.file_hash}>{shortFingerprint(v.file_hash, 14)}</code></td></tr>
              ))}
            </tbody>
          </table>
        </Panel>

        <Panel title="Add a version">
          <Tabs
            label="How the version arrives"
            tabs={[{ id: "url", label: "From a URL" }, { id: "upload", label: "Upload a file" }]}
            value={tab}
            onChange={(t) => { setTab(t); add.reset(); }}
          >
            <div className="stack-sm" style={{ paddingTop: 12 }}>
              {tab === "url" ? (
                <Field label="Source URL" required hint="The official page or file. Fetched once and stored. Only public http and https addresses work.">
                  {(id, d) => <TextInput id={id} aria-describedby={d} value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://" inputMode="url" />}
                </Field>
              ) : (
                <>
                  <Field label="File" required hint="Use upload when a site blocks downloads. The file is stored as it is.">
                    {(id, d) => <input id={id} aria-describedby={d} type="file" className="input" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />}
                  </Field>
                  <Field label="Where it came from" required hint="Saved with who added it. For example: downloaded from the official gazette on a given day.">
                    {(id, d) => <TextInput id={id} aria-describedby={d} value={note} onChange={(e) => setNote(e.target.value)} />}
                  </Field>
                </>
              )}
              <div className="adm-opts">
                <Field label="Version label">{(id) => <TextInput id={id} value={label} onChange={(e) => setLabel(e.target.value)} placeholder="For example: 2026 edition" />}</Field>
                <span />
                <Field label="Published on">{(id) => <TextInput id={id} type="date" value={published} onChange={(e) => setPublished(e.target.value)} />}</Field>
                <Field label="In effect from">{(id) => <TextInput id={id} type="date" value={effective} onChange={(e) => setEffective(e.target.value)} />}</Field>
              </div>
              {add.error ? (
                <Callout tone="held" title={isApiError(add.error) && add.error.code === "duplicate_version" ? "Already stored" : toProblem(add.error).title}>
                  {toProblem(add.error).detail}
                </Callout>
              ) : null}
              <div className="row">
                <Button variant="primary" icon="plus" loading={add.pending} disabled={!canSubmit} onClick={() => void submit()}>
                  Add version
                </Button>
                <span className="xsmall muted">The new version starts unreviewed.</span>
              </div>
            </div>
          </Tabs>
        </Panel>
      </div>
    </div>
  );
}

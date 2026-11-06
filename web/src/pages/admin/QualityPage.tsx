// Quality report: checks, flagged pages, expected vs found articles, words lost, and a
// chunk preview with anchors.
import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../../api";
import type { QualityReport } from "../../api/types";
import { ButtonLink, Callout, EmptyState, ErrorState, Field, PageHeader, Panel, Pill, PrototypeNotice, SkeletonLines, Table, TextInput, type Tone } from "../../components";
import { useApi } from "../../hooks/useApi";
import { formatNumber } from "../../lib/format";
import { humanize } from "../../lib/labels";
import { Metric, QualityPill, ReviewPill } from "./shared";

const CHECK_LABEL: Record<string, string> = {
  text_layer: "Text layer present",
  language_detected: "Language detected",
  garbled_text: "Text is readable",
  articles_complete: "Every expected article found",
  chunk_size_cap: "No chunk over the size cap",
  words_lost: "Words lost in cleaning",
  official_source: "Official source",
  duplicate_text: "No duplicated text",
};
const REASON_LABEL: Record<string, string> = { image_only: "Image only", hidden_text: "Hidden text", garbled: "Garbled", low_text: "Low text", other: "Other" };
const REASON_TONE: Record<string, Tone> = { image_only: "look", hidden_text: "held", garbled: "held", low_text: "look", other: "neutral" };

export default function QualityPage() {
  const { versionId = "" } = useParams();
  const version = useApi(() => api.getDocumentVersion(versionId), [versionId]);
  const doc = useApi(() => api.getDocument(version.data!.document_id), [version.data?.document_id], { enabled: Boolean(version.data) });
  const quality = useApi(() => api.getQualityReport(versionId), [versionId]);
  const chunks = useApi(() => api.listChunks(versionId, { limit: 200 }), [versionId]);
  const [q, setQ] = useState("");
  const [onlyFlagged, setOnlyFlagged] = useState(false);

  const flaggedPages = useMemo(() => new Set((quality.data?.flagged_pages ?? []).map((p) => p.page)), [quality.data]);
  const shown = useMemo(() => {
    const term = q.trim().toLowerCase();
    return (chunks.data?.items ?? []).filter(
      (c) => (!term || c.text.toLowerCase().includes(term) || c.anchor.toLowerCase().includes(term) || (c.section_path ?? "").toLowerCase().includes(term)) && (!onlyFlagged || (c.page !== null && c.page !== undefined && flaggedPages.has(c.page))),
    );
  }, [chunks.data, q, onlyFlagged, flaggedPages]);

  if (quality.error) return <ErrorState error={quality.error} onRetry={quality.reload} />;
  if (!quality.data || !version.data) return <SkeletonLines lines={8} />;
  const r: QualityReport = quality.data;
  const v = version.data;
  const missing = r.expected_articles !== null && r.found_articles !== null ? r.expected_articles - r.found_articles : 0;

  return (
    <div className="stack">
      <PageHeader
        breadcrumbs={[{ label: "Document library", to: "/admin/library" }, { label: v.document_id, to: `/admin/library/${v.document_id}` }, { label: "Quality report" }]}
        eyebrow={<code>{v.id}</code>}
        title="Quality report"
        subtitle={doc.data?.title ?? v.filename}
        status={<><QualityPill status={r.status} /><ReviewPill status={v.review_status} /></>}
        actions={<ButtonLink to={`/admin/library/versions/${v.id}/ingest`} icon="settings">Ingestion options</ButtonLink>}
      />

      <PrototypeNotice sourceUrl={doc.data?.source_url} />

      {r.held ? (
        <Callout tone="held" title="Held for a person">Hidden text with an instruction was found and removed. A full ingest is refused until a Process Owner approves the version with notes that say what they checked.</Callout>
      ) : null}

      {r.status === "fail" ? (
        <Callout tone="held" title="The quality gate failed">A full ingest is refused and the version cannot be approved. Change the options and run a trial again, or add a better copy.</Callout>
      ) : r.status === "attention" ? (
        <Callout tone="look" title="Passed with items to look at">Check the flagged pages and the attention items below. A Process Owner decides whether the version can be approved.</Callout>
      ) : null}

      <div className="adm-metrics">
        <Metric label="Articles found" value={r.expected_articles === null ? "Not applicable" : `${r.found_articles} of ${r.expected_articles}`} tone={missing > 0 ? "held" : r.expected_articles === null ? "neutral" : "pass"} note={r.expected_articles === null ? "Not a numbered text" : missing > 0 ? `${missing} missing` : "None missing"} />
        <Metric label="Words lost" value={formatNumber(r.words_lost)} tone={(r.words_lost ?? 0) > 0 ? "look" : "pass"} note="Not recovered from the file" />
        <Metric label="Flagged pages" value={r.flagged_pages.length} tone={r.flagged_pages.length ? "look" : "pass"} />
        <Metric label="Chunks" value={chunks.data ? chunks.data.items.length : "..."} note={chunks.data?.next_cursor ? "First 200 shown" : "In this version"} />
      </div>

      <div className="grid-2">
        <Panel title="Checks" padded={false}>
          <Table
            rows={r.checks}
            rowKey={(c) => c.key}
            dense
            caption="Quality checks"
            columns={[
              { key: "k", header: "Check", render: (c) => <span className="strong">{CHECK_LABEL[c.key] ?? humanize(c.key)}</span> },
              { key: "s", header: "Result", render: (c) => <QualityPill status={c.status} /> },
              { key: "d", header: "Detail", render: (c) => <span className="small">{c.detail ?? <span className="muted">No issue</span>}</span> },
            ]}
          />
        </Panel>
        <Panel title="Flagged pages" padded={false}>
          <Table
            rows={r.flagged_pages}
            rowKey={(p) => `${p.page}-${p.reason}`}
            dense
            caption="Flagged pages"
            empty={<EmptyState title="No pages flagged" icon="check">Every page has readable text.</EmptyState>}
            columns={[
              { key: "p", header: "Page", render: (p) => <span className="num strong">{p.page}</span>, sort: (p) => p.page, width: "70px" },
              { key: "r", header: "Reason", render: (p) => <Pill tone={REASON_TONE[p.reason] ?? "neutral"} size="sm" icon="flag">{REASON_LABEL[p.reason] ?? p.reason}</Pill> },
              { key: "d", header: "Detail", render: (p) => <span className="small">{p.detail ?? ""}</span> },
            ]}
          />
        </Panel>
      </div>

      <Panel title="Chunk preview">
        <p className="small muted" style={{ marginBottom: 10 }}>
          The anchor is the stable id a citation points to.
        </p>
        <div className="adm-toolbar" style={{ marginBottom: 12 }}>
          <Field label="Find in chunks">{(id) => <TextInput id={id} value={q} onChange={(e) => setQ(e.target.value)} placeholder="Words or anchor" />}</Field>
          <label className="checkbox">
            <input type="checkbox" checked={onlyFlagged} onChange={(e) => setOnlyFlagged(e.target.checked)} disabled={flaggedPages.size === 0} />
            <span>Only chunks on flagged pages</span>
          </label>
          <span className="small muted" aria-live="polite">{shown.length} shown</span>
        </div>
        {chunks.error ? <ErrorState error={chunks.error} onRetry={chunks.reload} /> : !chunks.data ? <SkeletonLines lines={5} /> : shown.length === 0 ? <EmptyState title="No chunks match" icon="search" /> : (
          <div className="stack-sm">
            {shown.slice(0, 60).map((c) => (
              <article key={c.anchor} className="adm-chunk">
                <div className="adm-chunk-meta">
                  <code className="strong">{c.anchor}</code>
                  <Pill tone="neutral" size="sm">{humanize(c.chunk_type)}</Pill>
                  {c.page ? <span className="xsmall muted">Page {c.page}{c.page_end && c.page_end !== c.page ? `-${c.page_end}` : ""}</span> : null}
                  {c.article ? <span className="xsmall muted">{c.article}</span> : null}
                  {c.page && flaggedPages.has(c.page) ? <Pill tone="look" size="sm" icon="flag">Flagged page</Pill> : null}
                </div>
                {c.section_path ? <div className="xsmall muted" style={{ marginBottom: 4 }}>{c.section_path}</div> : null}
                <p className="small">{c.text}</p>
              </article>
            ))}
            {shown.length > 60 ? <p className="small muted">Showing the first 60. Narrow the search to see others.</p> : null}
          </div>
        )}
      </Panel>
    </div>
  );
}

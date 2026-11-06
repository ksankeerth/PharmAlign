// A cited source: the document, the version an answer cited, and the passage with the quote marked.
// Anyone signed in can open it, so a citation in the Knowledge Assistant can always be followed.
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api } from "../../api";
import { Callout, ErrorState, KeyValue, PageHeader, Panel, PrototypeNotice, SkeletonLines } from "../../components";
import { useApi } from "../../hooks/useApi";
import { formatDate } from "../../lib/format";
import { humanize } from "../../lib/labels";
import "../../app/assistant/assistant.css";

function marked(text: string, quote: string | null) {
  const flat = (s: string) => s.replace(/\s+/g, " ");
  const at = quote ? flat(text).toLowerCase().indexOf(flat(quote).toLowerCase()) : -1;
  if (!quote || at < 0) return <>{text}</>;
  const t = flat(text);
  return (
    <>
      {t.slice(0, at)}
      <mark>{t.slice(at, at + flat(quote).length)}</mark>
      {t.slice(at + flat(quote).length)}
    </>
  );
}

export default function KnowledgeSourcePage() {
  const { documentId = "" } = useParams();
  const [params] = useSearchParams();
  const anchor = params.get("anchor");
  const quote = params.get("quote");
  const doc = useApi(() => api.getDocument(documentId), [documentId], { live: false });
  const version = params.get("version") ?? doc.data?.versions[0]?.id ?? "";
  const chunks = useApi(() => api.listChunks(version, { limit: 200 }), [version], { enabled: Boolean(version), live: false });
  const crumbs = [{ label: "Knowledge base" }, { label: documentId }];

  if (doc.error) {
    return (
      <div className="stack">
        <PageHeader title="Source" breadcrumbs={crumbs} />
        <ErrorState error={doc.error} onRetry={doc.reload} />
      </div>
    );
  }
  if (!doc.data) {
    return (
      <div className="stack">
        <PageHeader title="Source" breadcrumbs={crumbs} />
        <SkeletonLines lines={5} />
      </div>
    );
  }
  const d = doc.data;
  const v = d.versions.find((x) => x.id === version);
  const list = chunks.data?.items ?? [];
  const at = list.findIndex((c) => c.anchor === anchor);
  const shown = at >= 0 ? list.slice(Math.max(0, at - 1), at + 2) : list.slice(0, 3);
  return (
    <div className="stack">
      <PageHeader
        eyebrow={d.id}
        title={d.title}
        breadcrumbs={crumbs}
        subtitle={
          <span>
            {d.issuer}, {d.jurisdiction}. {humanize(d.doc_type)}.
          </span>
        }
      />
      <PrototypeNotice sourceUrl={d.source_url} />
      <Callout tone="neutral" title="Where this comes from" icon="book">
        This is the version the answer cited{v?.version_label ? `: ${v.version_label}` : ""}. Its legal status is shown on the citation, as pinned for the answer.
      </Callout>
      <div className="cw-split">
        <Panel title="The passage">
          {chunks.error ? <ErrorState error={chunks.error} onRetry={chunks.reload} /> : null}
          {!chunks.data && !chunks.error ? <SkeletonLines lines={4} /> : null}
          <div className="stack-sm">
            {shown.map((c) => (
              <div key={c.anchor} className={`src-passage ${c.anchor === anchor ? "src-passage-cited" : ""}`}>
                <div className="xsmall muted">
                  {c.article ?? c.section_path ?? "Passage"}
                  {c.page ? `, page ${c.page}` : ""} <code>{c.anchor}</code>
                </div>
                <p>{c.anchor === anchor ? marked(c.text, quote) : c.text}</p>
              </div>
            ))}
            {chunks.data && !shown.length ? <p className="muted">This version has no readable passages yet.</p> : null}
          </div>
        </Panel>
        <Panel title="The document">
          <KeyValue
            items={[
              { label: "Version", value: v?.version_label ?? version },
              { label: "Published", value: v?.published_on ? formatDate(v.published_on) : "Not given" },
              { label: "In effect from", value: v?.effective_on ? formatDate(v.effective_on) : "Not given" },
              { label: "Language", value: d.language },
              { label: "Other versions", value: d.versions.length > 1 ? d.versions.filter((x) => x.id !== version).map((x) => x.version_label ?? x.id).join(", ") : "None" },
            ]}
          />
          <p className="small muted">
            <Link to="/">Back to your home</Link>
          </p>
        </Panel>
      </div>
    </div>
  );
}

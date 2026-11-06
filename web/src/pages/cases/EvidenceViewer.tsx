// The dossier page beside its citations. DossierPageView shows one page with cited
// quotes marked, and any hidden text apart from what agents read. SourceCard shows a
// passage from outside the dossier: a clause, a reference approval or a foreign notice.
import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api";
import type { DossierDocument } from "../../api/types";
import { Button, ErrorState, Icon, Pill, SkeletonLines, TextInput } from "../../components";
import { useApi } from "../../hooks/useApi";
import { describeSource, parseAnchor } from "./shared";

/** Marks each quote that appears on the page. A quote not found is listed by the caller. */
function Marked({ text, quotes }: { text: string; quotes: string[] }): ReactNode {
  const usable = quotes.filter((q) => q.trim() && text.includes(q));
  if (!usable.length) return text;
  const pattern = new RegExp(`(${usable.map((q) => q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "g");
  return text.split(pattern).map((part, i) => (i % 2 === 1 ? <mark key={i}>{part}</mark> : part));
}

export function HiddenText({ text }: { text: string }) {
  return (
    <section className="cw-hidden-text" aria-label="Hidden text found on this page">
      <div className="cw-hidden-text-head">
        <Icon name="eye-off" />
        <span>Hidden text. Not part of the visible page</span>
      </div>
      <div className="small">It was removed from what agents read, and is shown here only for a person to review. It has no effect on any check.</div>
      <pre>{text}</pre>
    </section>
  );
}

export interface DossierPageViewProps {
  documentId: string;
  page: number;
  docs?: DossierDocument[];
  /** Quotes to mark on this page. */
  quotes?: string[];
  /** Lets the person move through the document. Without it the page is fixed. */
  onNavigate?: (page: number) => void;
}

export function DossierPageView({ documentId, page, docs, quotes = [], onNavigate }: DossierPageViewProps) {
  const doc = docs?.find((d) => d.id === documentId);
  const { data, error, loading, reload } = useApi(() => api.getDossierPage(documentId, page), [documentId, page], { live: false });
  const [pageInput, setPageInput] = useState(String(page));
  useEffect(() => setPageInput(String(page)), [page]);
  const last = doc?.pages ?? null;
  const go = () => {
    const n = Number(pageInput);
    if (Number.isInteger(n) && n >= 1 && (last === null || n <= last)) onNavigate?.(n);
    else setPageInput(String(page));
  };

  return (
    <article className="cw-page" aria-label={`${doc?.filename ?? documentId}, page ${page}`}>
      <div className="cw-page-head">
        <div className="row">
          <Icon name="file-text" size={14} />
          <strong className="cw-clip">{doc?.filename ?? documentId}</strong>
          {doc?.module ? <span className="muted">Module {doc.module}</span> : null}
          {data?.suspect ? (
            <Pill tone="held" icon="eye-off" size="sm">
              Suspect page
            </Pill>
          ) : null}
        </div>
        {onNavigate ? (
          <div className="cw-page-pager">
            <Button size="sm" variant="ghost" icon="chevron-left" aria-label="Previous page" disabled={page <= 1} onClick={() => onNavigate(page - 1)} />
            <label className="visually-hidden" htmlFor={`pg-${documentId}`}>
              Page number
            </label>
            <TextInput
              id={`pg-${documentId}`}
              inputMode="numeric"
              value={pageInput}
              onChange={(e) => setPageInput(e.target.value)}
              onBlur={go}
              onKeyDown={(e) => {
                if (e.key === "Enter") go();
              }}
            />
            <span className="small muted num">of {last ?? "?"}</span>
            <Button size="sm" variant="ghost" icon="chevron-right" aria-label="Next page" disabled={last !== null && page >= last} onClick={() => onNavigate(page + 1)} />
          </div>
        ) : (
          <span className="small muted num">
            Page {page}
            {last ? ` of ${last}` : ""}
          </span>
        )}
      </div>
      {error ? (
        <ErrorState error={error} onRetry={reload} />
      ) : !data ? (
        <div className="cw-page-body">{loading ? <SkeletonLines lines={5} /> : null}</div>
      ) : (
        <>
          <div className="cw-page-body">
            <Marked text={data.text} quotes={quotes} />
          </div>
          {data.hidden_text ? <HiddenText text={data.hidden_text} /> : null}
        </>
      )}
    </article>
  );
}

/** A cited passage from outside the dossier, shown beside the page. */
export function SourceCard({ anchor, quote, children }: { anchor: string; quote: string; children?: ReactNode }) {
  const a = parseAnchor(anchor);
  return (
    <div className="cw-source-card">
      <div className="row">
        <Icon name={a.kind === "notice" ? "globe" : "book"} size={14} />
        <strong>{describeSource(anchor)}</strong>
        {a.kind === "notice" ? <Link to={`/watch/notices/${a.doc}`}>Open notice</Link> : null}
      </div>
      <blockquote className="evidence-quote">{quote}</blockquote>
      <code className="xsmall muted cw-clip">{anchor}</code>
      {children}
    </div>
  );
}

// One citation as a card: the document, the clause, its legal status, the quote, and a link to the source.
// The server checked the quote against the source and set the title and status. The assistant did neither.
import { Link } from "react-router-dom";
import type { AssistantCitation } from "../../api/types";
import { Icon, LegalPill, Pill } from "../../components";

function emphasiseNumbers(text: string) {
  const parts = text.split(/(\d+(?:[.,]\d+)?\s?(?:mg|mL|ml|C|%|months?|days?)?)/g);
  return parts.map((p, i) => (i % 2 === 1 ? <mark key={i}>{p}</mark> : <span key={i}>{p}</span>));
}

/** The clause in words: the article, else the page, else the passage number. */
export function whereIn(c: AssistantCitation): string {
  if (c.article) return c.page ? `${c.article}, page ${c.page}` : c.article;
  if (c.page) return `Page ${c.page}`;
  const position = c.anchor.split(":")[2] ?? "";
  return /^c\d+$/.test(position) ? `Passage ${Number(position.slice(1))}` : position;
}

function SourcePill({ c }: { c: AssistantCitation }) {
  if (c.source === "knowledge") return c.legal_status ? <LegalPill status={c.legal_status} /> : null;
  return (
    <Pill tone="neutral" icon={c.source === "notice" ? "radar" : "file-text"} size="sm" title={c.source === "notice" ? "A foreign regulator's notice. Context, not a requirement here." : "From this case's file. Not law."}>
      {c.source === "notice" ? "Foreign notice" : "Case file"}
    </Pill>
  );
}

function SourceLink({ c, caseId, canOpenNotice }: { c: AssistantCitation; caseId: string | null; canOpenNotice: boolean }) {
  if (c.source === "knowledge") {
    const to = `/knowledge/${encodeURIComponent(c.document_id)}?${new URLSearchParams({ ...(c.document_version_id ? { version: c.document_version_id } : {}), anchor: c.anchor })}`;
    return (
      <Link to={to} className="as-cite-link">
        Open document version <Icon name="external" size={12} />
      </Link>
    );
  }
  if (c.source === "case_file" && caseId) {
    return (
      <Link to={`/cases/${caseId}/evidence?doc=${encodeURIComponent(c.document_id)}&page=${c.page ?? 1}`} className="as-cite-link">
        Open in the case file <Icon name="external" size={12} />
      </Link>
    );
  }
  if (c.source === "notice" && canOpenNotice) {
    return (
      <Link to={`/watch/notices/${encodeURIComponent(c.document_id)}`} className="as-cite-link">
        Open the notice <Icon name="external" size={12} />
      </Link>
    );
  }
  return null;
}

export function CitationCard({ citation: c, caseId, canOpenNotice }: { citation: AssistantCitation; caseId: string | null; canOpenNotice: boolean }) {
  return (
    <article className="as-cite" aria-label={`Source: ${c.title}`}>
      <header className="as-cite-head">
        <span className="as-cite-title">{c.title}</span>
        <SourcePill c={c} />
      </header>
      <div className="as-cite-where">
        <Icon name="book" size={12} />
        <span>{whereIn(c)}</span>
        <code className="as-cite-anchor">{c.anchor}</code>
      </div>
      <blockquote className="as-cite-quote">{emphasiseNumbers(c.quote)}</blockquote>
      <SourceLink c={c} caseId={caseId} canOpenNotice={canOpenNotice} />
    </article>
  );
}

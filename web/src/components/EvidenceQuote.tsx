// EvidenceQuote: a quote tied to its anchor and source. Use for every piece of
// evidence an agent or rule cites. Clicking it calls onOpen(anchor) so the screen can
// show the page or clause beside it (and record that the evidence was opened).
// Numbers in the quote are emphasised, since code compares them exactly.
import type { Evidence } from "../api/types";
import { Icon } from "./Icon";

export interface EvidenceQuoteProps {
  evidence: Evidence;
  /** Where the anchor points, such as "stability.pdf, page 41" or "E-S25, clause 3.2.6.1". */
  source?: string;
  /** Marks a quote that contradicts the claim (from the Challenger). */
  tone?: "default" | "contradicts" | "reference";
  onOpen?: (anchor: string) => void;
}

function emphasiseNumbers(text: string) {
  const parts = text.split(/(\d+(?:[.,]\d+)?\s?(?:mg|mL|ml|C|%|months?|days?)?)/g);
  return parts.map((p, i) => (i % 2 === 1 ? <mark key={i}>{p}</mark> : <span key={i}>{p}</span>));
}

/** Describes an anchor `doc:version:position` in words. */
export function describeAnchor(anchor: string): string {
  const [doc, version, pos] = anchor.split(":");
  if (!pos) return anchor;
  const where = pos.startsWith("p") ? `page ${pos.slice(1)}` : pos.startsWith("c") ? `chunk ${Number(pos.slice(1))}` : pos.startsWith("r") ? `revision ${pos.slice(1)}` : pos;
  return `${doc} ${version}, ${where}`;
}

export function EvidenceQuote({ evidence, source, tone = "default", onOpen }: EvidenceQuoteProps) {
  const body = (
    <>
      <blockquote className="evidence-quote">{emphasiseNumbers(evidence.quote)}</blockquote>
      <div className="evidence-meta">
        <Icon name={tone === "reference" ? "book" : "file-text"} size={12} />
        <span>{source ?? describeAnchor(evidence.anchor)}</span>
        <code className="evidence-anchor">{evidence.anchor}</code>
        {tone === "contradicts" ? <span className="evidence-flag">Contradicts the claim</span> : null}
      </div>
    </>
  );
  if (!onOpen) return <div className={`evidence evidence-${tone}`}>{body}</div>;
  return (
    <button type="button" className={`evidence evidence-${tone} evidence-click`} onClick={() => onOpen(evidence.anchor)} aria-label={`Open evidence at ${describeAnchor(evidence.anchor)}`}>
      {body}
    </button>
  );
}

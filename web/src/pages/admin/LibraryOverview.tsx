// What the agents know: the library at a glance, for someone with no regulatory background.
// Built from the documents the library page already loads: kind, source, language and legal status.
import type { Document } from "../../api/types";
import type { RefCallback } from "react";
import { Icon } from "../../components";

interface Item {
  doc: Document;
  legal: string | null;
}

const KIND: [string, (t: string) => boolean][] = [
  ["Laws and regulations", (t) => /law|regulation|decree|resolution/.test(t)],
  ["Guidelines and standards", (t) => /guideline|standard/.test(t)],
  ["Procedures and policies", (t) => /procedure|policy|service_card|form/.test(t)],
];
const OTHER_KIND = "Reports, labels and references";

const UAE = /^(uae|dubai|abu dhabi|sharjah)/i;
const GULF = /^(gcc|arab league|saudi|bahrain|oman|kuwait|qatar)/i;

function source(j: string): string {
  if (UAE.test(j)) return "UAE, federal and emirates";
  if (GULF.test(j)) return "Gulf and Arab region";
  if (/^international/i.test(j)) return "International (WHO, ICH and others)";
  return "Other regulators (EU, US, UK and others)";
}

export function documentCategory(t: string): string {
  return KIND.find(([, test]) => test(t))?.[0] ?? OTHER_KIND;
}

function tally(values: string[]): [string, number][] {
  const m = new Map<string, number>();
  values.forEach((v) => m.set(v, (m.get(v) ?? 0) + 1));
  return [...m.entries()].sort((a, b) => b[1] - a[1]);
}

function Bars({ title, rows, total }: { title: string; rows: [string, number][]; total: number }) {
  return (
    <div className="lo-bars">
      <h3>{title}</h3>
      <ul>
        {rows.map(([label, n]) => (
          <li key={label}>
            <span className="lo-label">{label}</span>
            <span className="lo-track" aria-hidden="true">
              <span style={{ width: `${Math.max(3, (n / total) * 100)}%` }} />
            </span>
            <span className="lo-n">{n}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function LibraryOverview({ items, category, onCategory, categoryAnchor }: {
  items: Item[] | null;
  category: string;
  onCategory: (category: string) => void;
  categoryAnchor?: RefCallback<HTMLElement>;
}) {
  const docs = items ?? [];
  const total = docs.length;
  const inForce = docs.filter((r) => r.legal === "in_force").length;
  const history = docs.filter((r) => r.legal === "superseded" || r.legal === "repealed").length;
  const publishers = new Set(docs.map((r) => r.doc.issuer)).size;
  const arabic = docs.filter((r) => r.doc.language === "ar").length;
  const categories = new Map(tally(docs.map((r) => documentCategory(r.doc.doc_type))));
  const n = (v: number) => (items ? v.toLocaleString() : "…");

  return (
    <section className="lo" aria-labelledby="lo-heading">
      <header>
        <h2 id="lo-heading">What the agents know</h2>
        <p>Every AI finding and answer quotes a passage from these public documents, word for word, with the document's legal status.</p>
      </header>

      <div className="lo-figures">
        <div>
          <strong>{n(total)}</strong>
          <span>public documents</span>
        </div>
        <div>
          <strong className="lo-pass">{n(inForce)}</strong>
          <span>in force in the UAE{history ? `, ${history} kept for history only` : ""}</span>
        </div>
        <div>
          <strong>{n(publishers)}</strong>
          <span>publishers and regulators</span>
        </div>
        <div>
          <strong>{items ? (arabic ? 2 : 1) : "…"}</strong>
          <span>{arabic ? `languages: English and Arabic (${arabic})` : "language: English"}</span>
        </div>
      </div>

      <div className="lo-split">
        <section ref={categoryAnchor} className="lo-bars" tabIndex={-1} aria-labelledby="library-categories">
          <h3 id="library-categories">Document categories</h3>
          <div className="lo-categories">
            {[...KIND.map(([label]) => label), OTHER_KIND].map((label) => (
              <button type="button" key={label} aria-pressed={category === label}
                disabled={!categories.has(label) && category !== label}
                onClick={() => onCategory(category === label ? "" : label)}>
                <span>{label}</span><span className="lo-n">{n(categories.get(label) ?? 0)}</span>
              </button>
            ))}
          </div>
        </section>
        {items ? (
          <Bars title="Where they come from" rows={tally(docs.map((r) => source(r.doc.jurisdiction)))} total={total} />
        ) : null}
      </div>

      <p className="lo-how">
        <Icon name="shield" size={13} />
        Each document is checked by a person and given a legal status, then split into searchable passages. Agents may rely on law in force; repealed law is
        kept for history and never applied.
      </p>
    </section>
  );
}

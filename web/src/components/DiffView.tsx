// DiffView: before and after for each changed path, such as a pack version diff
// (PackDiff.changes) or an audit event's before/after. Values are shown as compact
// JSON; added and removed values are marked in words as well as colour.
import type { ReactNode } from "react";

export interface DiffChange {
  path: string;
  before?: unknown;
  after?: unknown;
}

function show(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "string") return v;
  return JSON.stringify(v, null, 2);
}

export function DiffView({ changes, empty, beforeLabel = "Before", afterLabel = "After" }: { changes: DiffChange[]; empty?: ReactNode; beforeLabel?: string; afterLabel?: string }) {
  if (!changes.length) return <>{empty ?? <p className="muted">No changes.</p>}</>;
  return (
    <div className="diff" role="table" aria-label="Changes">
      <div className="diff-row diff-header" role="row">
        <div role="columnheader">Path</div>
        <div role="columnheader">{beforeLabel}</div>
        <div role="columnheader">{afterLabel}</div>
      </div>
      {changes.map((c, i) => {
        const added = c.before === null || c.before === undefined;
        const removed = c.after === null || c.after === undefined;
        return (
          <div className="diff-row" role="row" key={`${c.path}-${i}`}>
            <div role="cell" className="diff-path">
              <code>{c.path}</code>
              {added ? <span className="diff-tag diff-tag-add">Added</span> : removed ? <span className="diff-tag diff-tag-del">Removed</span> : <span className="diff-tag">Changed</span>}
            </div>
            <div role="cell" className="diff-before">
              {added ? <span className="muted">None</span> : <pre>{show(c.before)}</pre>}
            </div>
            <div role="cell" className="diff-after">
              {removed ? <span className="muted">None</span> : <pre>{show(c.after)}</pre>}
            </div>
          </div>
        );
      })}
    </div>
  );
}

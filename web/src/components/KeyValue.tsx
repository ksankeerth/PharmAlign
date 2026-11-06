// KeyValue: a labelled list of facts (case summary, document details, signing
// package). Use `columns={2}` for wide panels. Empty values show "None".
import type { ReactNode } from "react";

export interface KeyValueItem {
  label: ReactNode;
  value: ReactNode;
  /** Monospace value, for ids and hashes. */
  mono?: boolean;
}

export function KeyValue({ items, columns = 1 }: { items: KeyValueItem[]; columns?: 1 | 2 | 3 }) {
  return (
    <dl className={`kv kv-${columns}`}>
      {items.map((it, i) => (
        <div className="kv-item" key={i}>
          <dt>{it.label}</dt>
          <dd className={it.mono ? "mono" : undefined}>{it.value === null || it.value === undefined || it.value === "" ? <span className="muted">None</span> : it.value}</dd>
        </div>
      ))}
    </dl>
  );
}

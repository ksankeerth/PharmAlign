// Table: dense data tables with optional sorting, row links, a loading skeleton and
// an empty state. Columns declare how to render and, if sortable, what to sort by.
//
//   <Table rows={cases} rowKey={(c) => c.id} columns={[
//     { key: "id", header: "Case", render: (c) => c.id, sort: (c) => c.id },
//   ]} onRowClick={(c) => navigate(`/cases/${c.id}`)} empty={<EmptyState title="No cases" />} />
import { type ReactNode, useMemo, useState } from "react";
import { Icon } from "./Icon";
import { Skeleton } from "./Skeleton";

export interface Column<T> {
  key: string;
  header: ReactNode;
  render: (row: T) => ReactNode;
  /** Value to sort by. Columns without it are not sortable. */
  sort?: (row: T) => string | number | null | undefined;
  align?: "left" | "right" | "center";
  width?: string;
  /** Hide below this viewport width (px), for less important columns. */
  hideBelow?: number;
}

export interface TableProps<T> {
  rows: T[] | null | undefined;
  columns: Column<T>[];
  rowKey: (row: T) => string;
  loading?: boolean;
  empty?: ReactNode;
  onRowClick?: (row: T) => void;
  /** Accessible caption; visually hidden unless `showCaption`. */
  caption?: string;
  showCaption?: boolean;
  initialSort?: { key: string; dir: "asc" | "desc" };
  /** Highlights a row, such as the one open in a side panel. */
  selectedKey?: string | null;
  dense?: boolean;
}

export function Table<T>({ rows, columns, rowKey, loading, empty, onRowClick, caption, showCaption, initialSort, selectedKey, dense }: TableProps<T>) {
  const [sort, setSort] = useState(initialSort ?? null);
  const sorted = useMemo(() => {
    if (!rows) return [];
    if (!sort) return rows;
    const col = columns.find((c) => c.key === sort.key);
    if (!col?.sort) return rows;
    const get = col.sort;
    return [...rows].sort((a, b) => {
      const av = get(a);
      const bv = get(b);
      if (av === bv) return 0;
      if (av === null || av === undefined) return 1;
      if (bv === null || bv === undefined) return -1;
      const r = typeof av === "number" && typeof bv === "number" ? av - bv : String(av).localeCompare(String(bv), "en", { numeric: true });
      return sort.dir === "asc" ? r : -r;
    });
  }, [rows, sort, columns]);

  const toggle = (key: string) => setSort((s) => (s?.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: "asc" }));
  const hideClass = (c: Column<T>) => (c.hideBelow ? `hide-below-${c.hideBelow}` : "");

  return (
    <div className="table-wrap">
      <table className={`table ${dense ? "table-dense" : ""}`}>
        {caption ? <caption className={showCaption ? "table-caption" : "visually-hidden"}>{caption}</caption> : null}
        <thead>
          <tr>
            {columns.map((c) => {
              const active = sort?.key === c.key;
              return (
                <th
                  key={c.key}
                  scope="col"
                  style={{ width: c.width, textAlign: c.align }}
                  className={hideClass(c)}
                  aria-sort={c.sort ? (active ? (sort?.dir === "asc" ? "ascending" : "descending") : "none") : undefined}
                >
                  {c.sort ? (
                    <button type="button" className="th-sort" onClick={() => toggle(c.key)}>
                      {c.header}
                      <Icon name={active ? (sort?.dir === "asc" ? "arrow-up" : "arrow-down") : "sort"} size={12} />
                    </button>
                  ) : (
                    c.header
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {loading && !rows ? (
            Array.from({ length: 5 }, (_, i) => (
              <tr key={`sk-${i}`}>
                {columns.map((c) => (
                  <td key={c.key} className={hideClass(c)}>
                    <Skeleton width={`${50 + ((i * 17 + c.key.length * 7) % 45)}%`} />
                  </td>
                ))}
              </tr>
            ))
          ) : sorted.length === 0 ? (
            <tr>
              <td colSpan={columns.length} className="table-empty">
                {empty ?? <span className="muted">Nothing to show.</span>}
              </td>
            </tr>
          ) : (
            sorted.map((row) => {
              const key = rowKey(row);
              return (
                <tr
                  key={key}
                  className={[onRowClick ? "row-click" : "", selectedKey === key ? "row-selected" : ""].join(" ").trim() || undefined}
                  onClick={onRowClick ? () => onRowClick(row) : undefined}
                  onKeyDown={
                    onRowClick
                      ? (e) => {
                          if (e.target !== e.currentTarget || (e.key !== "Enter" && e.key !== " ")) return;
                          // Without this, the key press would also reach the control that takes focus when a panel opens.
                          e.preventDefault();
                          onRowClick(row);
                        }
                      : undefined
                  }
                  tabIndex={onRowClick ? 0 : undefined}
                >
                  {columns.map((c) => (
                    <td key={c.key} style={{ textAlign: c.align }} className={hideClass(c)}>
                      {c.render(row)}
                    </td>
                  ))}
                </tr>
              );
            })
          )}
        </tbody>
      </table>
    </div>
  );
}

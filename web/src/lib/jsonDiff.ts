// A small structural diff for JSON values, used for pack version diffs. Arrays of
// objects that carry an `id` are matched by id, so reordering is not reported as change.
export interface JsonChange {
  path: string;
  before: unknown;
  after: unknown;
}

function isObj(v: unknown): v is Record<string, unknown> {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

function keyed(arr: unknown[]): Map<string, unknown> | null {
  if (!arr.every((x) => isObj(x) && typeof x.id === "string")) return null;
  return new Map(arr.map((x) => [(x as { id: string }).id, x]));
}

export function jsonDiff(before: unknown, after: unknown, path = ""): JsonChange[] {
  if (Object.is(before, after)) return [];
  if (Array.isArray(before) && Array.isArray(after)) {
    const a = keyed(before);
    const b = keyed(after);
    if (a && b) {
      const out: JsonChange[] = [];
      for (const [id, v] of a) out.push(...jsonDiff(v, b.get(id), `${path}[${id}]`));
      for (const [id, v] of b) if (!a.has(id)) out.push({ path: `${path}[${id}]`, before: undefined, after: v });
      return out.map((c) => (c.before === undefined ? { ...c, before: null } : c.after === undefined ? { ...c, after: null } : c));
    }
    if (JSON.stringify(before) === JSON.stringify(after)) return [];
    return [{ path: path || "(root)", before, after }];
  }
  if (isObj(before) && isObj(after)) {
    const keys = new Set([...Object.keys(before), ...Object.keys(after)]);
    const out: JsonChange[] = [];
    for (const k of keys) out.push(...jsonDiff(before[k], after[k], path ? `${path}.${k}` : k));
    return out;
  }
  if (before === undefined) return [{ path: path || "(root)", before: null, after }];
  if (after === undefined) return [{ path: path || "(root)", before, after: null }];
  return [{ path: path || "(root)", before, after }];
}

// Reading a JSON file into a form: what a file may hold and which values a form keeps. Pure functions,
// so they run without a browser. The button is components/FillFromJson.tsx.
export type JsonObject = Record<string, unknown>;
export type FieldKind = "string" | "number" | "boolean" | "string[]";
export type FieldMap = Record<string, FieldKind>;

function isObj(v: unknown): v is JsonObject {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

/** A file's text as JSON, or a short sentence saying why not. */
export function parseJson(text: string): { ok: true; value: unknown } | { ok: false; error: string } {
  try {
    return { ok: true, value: JSON.parse(text) };
  } catch (e) {
    const why = e instanceof Error ? e.message.slice(0, 80) : "parse error";
    return { ok: false, error: `That file is not valid JSON (${why}).` };
  }
}

/**
 * The items a file holds: a bare list, {items: [...]}, {[key]: [...]}, or one object on its own.
 * Only objects count. An object that has an `items` (or `key`) entry that is not a list is a wrapper, not an item.
 */
export function itemsOf(value: unknown, key?: string): JsonObject[] {
  if (Array.isArray(value)) return value.filter(isObj);
  if (!isObj(value)) return [];
  for (const k of key ? ["items", key] : ["items"]) {
    if (k in value) return Array.isArray(value[k]) ? (value[k] as unknown[]).filter(isObj) : [];
  }
  return [value];
}

/** What names an item in a pick list: its display name, name, title, label or id. */
export function labelOf(item: JsonObject, fallback: string): string {
  for (const k of ["display_name", "name", "title", "label", "id"]) {
    const v = item[k];
    if (typeof v === "string" && v.trim()) return v.trim();
  }
  return fallback;
}

/** Only the form's own fields, each with the right type. Unknown keys and values of the wrong type are dropped. */
export function pickFields(value: unknown, fields: FieldMap): JsonObject {
  const out: JsonObject = {};
  if (!isObj(value)) return out;
  for (const [name, kind] of Object.entries(fields)) {
    const v = value[name];
    const ok =
      kind === "string" ? typeof v === "string"
      : kind === "number" ? typeof v === "number" && Number.isFinite(v)
      : kind === "boolean" ? typeof v === "boolean"
      : Array.isArray(v) && v.every((x) => typeof x === "string");
    if (ok) out[name] = kind === "string[]" ? [...(v as string[])] : v;
  }
  return out;
}

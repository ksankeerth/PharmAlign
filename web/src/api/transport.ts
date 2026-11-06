// Makes every call to the server, at the same origin unless VITE_API_BASE says otherwise.
import { ApiError } from "./errors";
import { emitApiChange } from "./events";
import { OPERATIONS, type OperationId } from "./operations";
import type { AssistantEvent, CallRequest, Ok, Problem } from "./types";

const BASE_URL: string = import.meta.env.VITE_API_BASE ?? "";

/** Replaces `{name}` segments of an operation path with encoded values. */
export function buildPath(template: string, params: Record<string, unknown> | undefined): string {
  return template.replace(/\{([^}]+)\}/g, (_, name: string) => {
    const value = params?.[name];
    if (value === undefined || value === null) throw new Error(`missing path parameter ${name}`);
    return encodeURIComponent(String(value));
  });
}

function buildQuery(query: Record<string, unknown> | undefined): string {
  if (!query) return "";
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v === undefined || v === null || v === "") continue;
    qs.set(k, String(v));
  }
  const s = qs.toString();
  return s ? `?${s}` : "";
}

async function problemFrom(res: Response): Promise<Problem> {
  try {
    const body = (await res.json()) as Problem;
    if (body && typeof body.title === "string" && typeof body.code === "string") return body;
  } catch {
    // fall through to a generic problem
  }
  return { type: "about:blank", title: res.statusText || "Request failed", status: res.status, code: "http_error", detail: null, run_id: null };
}

async function httpFetch(op: OperationId, req: CallRequest, accept: string): Promise<Response> {
  const meta = OPERATIONS[op];
  const url = BASE_URL + buildPath(meta.path, req.path as Record<string, unknown>) + buildQuery(req.query as Record<string, unknown>);
  const headers: Record<string, string> = { Accept: accept };
  const form = typeof FormData !== "undefined" && req.body instanceof FormData; // the browser sets the multipart boundary
  if (req.body !== undefined && !form) headers["Content-Type"] = "application/json";
  if (req.idempotencyKey) headers["Idempotency-Key"] = req.idempotencyKey;
  if (req.ifMatch) headers["If-Match"] = req.ifMatch;
  if (req.asPerson) headers.Authorization = `Bearer ${req.asPerson}`;
  const res = await fetch(url, {
    method: meta.method,
    headers,
    credentials: "include",
    body: req.body === undefined ? undefined : form ? (req.body as FormData) : JSON.stringify(req.body),
  });
  if (!res.ok) throw new ApiError(await problemFrom(res));
  return res;
}

async function httpCall<K extends OperationId>(op: K, req: CallRequest<K>): Promise<Ok<K>> {
  const res = await httpFetch(op, req as CallRequest, "application/json");
  if (res.status === 204) return undefined as Ok<K>;
  return (await res.json()) as Ok<K>;
}

async function* httpStream(op: OperationId, req: CallRequest): AsyncGenerator<AssistantEvent> {
  const res = await httpFetch(op, req, "text/event-stream");
  if (!res.body) return;
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buffer.indexOf("\n\n")) >= 0) {
      const chunk = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const data = chunk
        .split("\n")
        .filter((l) => l.startsWith("data:"))
        .map((l) => l.slice(5).trimStart())
        .join("\n");
      if (data) yield JSON.parse(data) as AssistantEvent;
    }
  }
}

/** POST calls that only read. Announcing them would reload the view that made them, and so call them again. */
const READ_ONLY_POSTS: ReadonlySet<OperationId> = new Set<OperationId>(["post_api_v1_pack_versions_version_id_validate"]);

/** Makes one call. Mutations announce a change so open views can reload. */
export async function call<K extends OperationId>(op: K, req: CallRequest<K> = {}): Promise<Ok<K>> {
  const result = await httpCall(op, req);
  if (OPERATIONS[op].method !== "GET" && !READ_ONLY_POSTS.has(op)) emitApiChange({ op });
  return result;
}

/** Streams server-sent events, for the Knowledge Assistant. */
export async function* stream(op: OperationId, req: CallRequest = {}): AsyncGenerator<AssistantEvent> {
  yield* httpStream(op, req);
  emitApiChange({ op });
}

// Formatting helpers: dates, relative times, sizes and fingerprints. English only.
const DATE = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric" });
const DATETIME = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "None";
  const d = new Date(iso.length === 10 ? `${iso}T00:00:00Z` : iso);
  return Number.isNaN(d.getTime()) ? iso : DATE.format(d);
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "None";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : DATETIME.format(d);
}

/** "5 min ago", "in 18 min", "3 days ago". */
export function formatRelative(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  const diff = t - now;
  const abs = Math.abs(diff);
  const min = Math.round(abs / 60_000);
  const unit = (n: number, w: string) => `${n} ${w}${n === 1 ? "" : "s"}`;
  let text: string;
  if (min < 1) return diff < 0 ? "just now" : "now";
  if (min < 60) text = `${min} min`;
  else if (min < 60 * 24) text = unit(Math.round(min / 60), "hour");
  else text = unit(Math.round(min / 1440), "day");
  return diff < 0 ? `${text} ago` : `in ${text}`;
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function formatNumber(n: number | null | undefined): string {
  return n === null || n === undefined ? "None" : new Intl.NumberFormat("en-GB").format(n);
}

export function formatPercent(n: number | null | undefined): string {
  return n === null || n === undefined ? "None" : `${Math.round(n * 100)}%`;
}

/** `sha256:9c1e4f...` shortened for display; the full value goes in a title. */
export function shortFingerprint(fp: string | null | undefined, chars = 10): string {
  if (!fp) return "none";
  const [algo, hex] = fp.split(":");
  return hex ? `${algo}:${hex.slice(0, chars)}` : fp;
}

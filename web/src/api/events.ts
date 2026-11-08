// A tiny change bus. The transport emits after every successful mutation.
// `useApi` listens so open views reload without each screen wiring it up.
export interface ApiChange {
  op: string;
}

type Listener = (change: ApiChange) => void;
const listeners = new Set<Listener>();

// Work finishes in the background with no way to tell the page, so open views can ask again every few seconds.
// VITE_AUTO_REFRESH_MS sets how often: 2000 by default, 0 for never. The cloud build uses 0, because a page that
// asks every few seconds keeps the server and database from going idle, and that is billed. The Refresh button in
// the top bar asks once instead.
const POLL_MS = Number(import.meta.env.VITE_AUTO_REFRESH_MS ?? 2000);
let timer: ReturnType<typeof setInterval> | null = null;

/** Every open view asks the server again, once. */
export function refreshAll(): void {
  emitApiChange({ op: "http.refresh" });
}

function startPolling(): void {
  if (timer || !(POLL_MS > 0)) return;
  timer = setInterval(() => {
    if (!document.hidden) emitApiChange({ op: "http.poll" });
  }, POLL_MS);
}

export function onApiChange(listener: Listener): () => void {
  listeners.add(listener);
  startPolling();
  return () => {
    listeners.delete(listener);
    if (!listeners.size && timer) {
      clearInterval(timer);
      timer = null;
    }
  };
}

export function emitApiChange(change: ApiChange): void {
  for (const l of [...listeners]) {
    try {
      l(change);
    } catch {
      // a broken listener must not stop the others
    }
  }
}

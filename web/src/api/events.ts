// A tiny change bus. The transport emits after every successful mutation.
// `useApi` listens so open views reload without each screen wiring it up.
export interface ApiChange {
  op: string;
}

type Listener = (change: ApiChange) => void;
const listeners = new Set<Listener>();

// Work finishes in the background with no way to tell the page, so open views ask again every few seconds.
const POLL_MS = 2000;
let timer: ReturnType<typeof setInterval> | null = null;

function startPolling(): void {
  if (timer) return;
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

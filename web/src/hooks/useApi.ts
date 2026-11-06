// Data hooks for screens.
//
// useApi: loads data and reloads it whenever anything changes (any mutation, a
// background re-check finishing, switching person or workspace). Keeps the previous
// data while reloading, so screens don't flicker.
//
//   const { data, error, loading, reload } = useApi(() => api.getCase(caseId), [caseId]);
//
// useMutation: runs an action, tracks pending and the last error, and rethrows so the
// caller can show a GuardList for a 409.
//
//   const confirm = useMutation((fp: string) => api.confirmCheck(id, { fingerprint: fp }));
//   await confirm.run(check.fingerprint);
import { type DependencyList, useCallback, useEffect, useRef, useState } from "react";
import { onApiChange } from "../api/events";

export interface ApiState<T> {
  data: T | undefined;
  error: unknown;
  loading: boolean;
  reload: () => void;
}

export function useApi<T>(load: () => Promise<T>, deps: DependencyList, options: { enabled?: boolean; live?: boolean } = {}): ApiState<T> {
  const { enabled = true, live = true } = options;
  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState<boolean>(enabled);
  const seq = useRef(0);
  const loadRef = useRef(load);
  loadRef.current = load;

  const run = useCallback(() => {
    if (!enabled) return;
    const mine = ++seq.current;
    setLoading(true);
    loadRef
      .current()
      .then((d) => {
        if (mine !== seq.current) return;
        setData(d);
        setError(null);
      })
      .catch((e: unknown) => {
        if (mine !== seq.current) return;
        setError(e);
      })
      .finally(() => {
        if (mine === seq.current) setLoading(false);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, ...deps]);

  useEffect(() => {
    setData(undefined);
    run();
  }, [run]);

  useEffect(() => {
    if (!live || !enabled) return;
    let t: ReturnType<typeof setTimeout> | null = null;
    const off = onApiChange(() => {
      if (t) clearTimeout(t);
      t = setTimeout(run, 120);
    });
    return () => {
      off();
      if (t) clearTimeout(t);
    };
  }, [run, live, enabled]);

  return { data, error, loading, reload: run };
}

export interface MutationState<A extends unknown[], R> {
  run: (...args: A) => Promise<R>;
  pending: boolean;
  error: unknown;
  reset: () => void;
}

export function useMutation<A extends unknown[], R>(fn: (...args: A) => Promise<R>): MutationState<A, R> {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const run = useCallback(async (...args: A) => {
    setPending(true);
    setError(null);
    try {
      return await fnRef.current(...args);
    } catch (e) {
      setError(e);
      throw e;
    } finally {
      setPending(false);
    }
  }, []);
  return { run, pending, error, reset: () => setError(null) };
}

// Toast: brief, non-blocking confirmation that something happened ("Check confirmed").
// Use for outcomes, not for errors that need action (show those inline).
//   const toast = useToast(); toast.success("Promoted to the Decision Maker");
import { createContext, type ReactNode, useCallback, useContext, useMemo, useRef, useState } from "react";
import { Icon, type IconName } from "./Icon";

type Kind = "success" | "info" | "warning" | "error";
interface ToastItem {
  id: number;
  kind: Kind;
  text: string;
}

interface ToastApi {
  show: (text: string, kind?: Kind) => void;
  success: (text: string) => void;
  info: (text: string) => void;
  warning: (text: string) => void;
  error: (text: string) => void;
}

const ToastContext = createContext<ToastApi | null>(null);
const ICON: Record<Kind, IconName> = { success: "check", info: "info", warning: "alert-triangle", error: "alert-octagon" };

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const next = useRef(1);
  const dismiss = useCallback((id: number) => setItems((xs) => xs.filter((x) => x.id !== id)), []);
  const show = useCallback(
    (text: string, kind: Kind = "info") => {
      const id = next.current++;
      setItems((xs) => [...xs.slice(-3), { id, kind, text }]);
      setTimeout(() => dismiss(id), kind === "error" ? 7000 : 4000);
    },
    [dismiss],
  );
  const api = useMemo<ToastApi>(
    () => ({ show, success: (t) => show(t, "success"), info: (t) => show(t, "info"), warning: (t) => show(t, "warning"), error: (t) => show(t, "error") }),
    [show],
  );
  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={`toast toast-${t.kind}`}>
            <Icon name={ICON[t.kind]} />
            <span>{t.text}</span>
            <button type="button" className="toast-close" aria-label="Dismiss" onClick={() => dismiss(t.id)}>
              <Icon name="x" size={14} />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used inside ToastProvider");
  return ctx;
}

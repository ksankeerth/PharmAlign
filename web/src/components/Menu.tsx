// Menu: a button that opens a small popover list (top bar switchers, row actions).
// Closes on outside click, Escape, or choosing an item. Arrow keys move between items.
import { type ReactNode, useEffect, useId, useRef, useState } from "react";

export interface MenuProps {
  /** Renders the trigger; spread the props onto a button. */
  trigger: (props: { "aria-haspopup": "menu"; "aria-expanded": boolean; "aria-controls": string; onClick: () => void; ref: (el: HTMLButtonElement | null) => void }) => ReactNode;
  children: (close: () => void) => ReactNode;
  align?: "left" | "right";
  width?: number;
  label: string;
}

export function Menu({ trigger, children, align = "right", width = 280, label }: MenuProps) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const box = useRef<HTMLDivElement>(null);
  const btn = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        btn.current?.focus();
      }
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        const items = Array.from(box.current?.querySelectorAll<HTMLElement>('[role="menuitem"], [role="menuitemradio"]') ?? []);
        if (!items.length) return;
        e.preventDefault();
        const i = items.indexOf(document.activeElement as HTMLElement);
        const next = e.key === "ArrowDown" ? (i + 1) % items.length : (i - 1 + items.length) % items.length;
        items[next].focus();
      }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    const first = box.current?.querySelector<HTMLElement>('[aria-checked="true"], [role="menuitem"], [role="menuitemradio"]');
    first?.focus();
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const close = () => {
    setOpen(false);
    btn.current?.focus();
  };
  return (
    <div className="menu-root" ref={box}>
      {trigger({
        "aria-haspopup": "menu",
        "aria-expanded": open,
        "aria-controls": id,
        onClick: () => setOpen((o) => !o),
        ref: (el) => {
          btn.current = el;
        },
      })}
      {open ? (
        <div id={id} role="menu" aria-label={label} className={`menu menu-${align}`} style={{ width }}>
          {children(close)}
        </div>
      ) : null}
    </div>
  );
}

export function MenuItem({ children, onSelect, checked, danger, hint }: { children: ReactNode; onSelect: () => void; checked?: boolean; danger?: boolean; hint?: ReactNode }) {
  return (
    <button
      type="button"
      role={checked === undefined ? "menuitem" : "menuitemradio"}
      aria-checked={checked}
      tabIndex={-1}
      className={`menu-item ${danger ? "menu-item-danger" : ""} ${checked ? "menu-item-checked" : ""}`}
      onClick={onSelect}
    >
      <span className="menu-item-main">{children}</span>
      {hint ? <span className="menu-item-hint">{hint}</span> : null}
    </button>
  );
}

export function MenuLabel({ children }: { children: ReactNode }) {
  return <div className="menu-label">{children}</div>;
}

export function MenuDivider() {
  return <div className="menu-divider" role="separator" />;
}

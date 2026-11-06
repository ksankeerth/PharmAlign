// Tabs: switch between panels on one screen (ARIA tablist, arrow keys move focus).
// NavTabs: the same look for route-based tabs, such as the case page sections.
import { type ReactNode, useId, useRef } from "react";
import { NavLink } from "react-router-dom";

export interface TabItem {
  id: string;
  label: ReactNode;
  /** A count or small badge after the label. */
  badge?: ReactNode;
  disabled?: boolean;
}

export interface TabsProps {
  tabs: TabItem[];
  value: string;
  onChange: (id: string) => void;
  /** Render the active panel. */
  children?: ReactNode;
  label?: string;
}

export function Tabs({ tabs, value, onChange, children, label }: TabsProps) {
  const base = useId();
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const onKey = (e: React.KeyboardEvent, i: number) => {
    const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!step) return;
    e.preventDefault();
    const enabled = tabs.map((t, idx) => (t.disabled ? -1 : idx)).filter((x) => x >= 0);
    const pos = enabled.indexOf(i);
    const next = enabled[(pos + step + enabled.length) % enabled.length];
    refs.current[next]?.focus();
    onChange(tabs[next].id);
  };
  return (
    <div className="tabs-block">
      <div role="tablist" aria-label={label} className="tabs">
        {tabs.map((t, i) => (
          <button
            key={t.id}
            ref={(el) => {
              refs.current[i] = el;
            }}
            role="tab"
            type="button"
            id={`${base}-tab-${t.id}`}
            aria-selected={t.id === value}
            aria-controls={`${base}-panel`}
            tabIndex={t.id === value ? 0 : -1}
            disabled={t.disabled}
            className="tab"
            onClick={() => onChange(t.id)}
            onKeyDown={(e) => onKey(e, i)}
          >
            {t.label}
            {t.badge !== undefined ? <span className="tab-badge">{t.badge}</span> : null}
          </button>
        ))}
      </div>
      {children !== undefined ? (
        <div role="tabpanel" id={`${base}-panel`} aria-labelledby={`${base}-tab-${value}`} className="tab-panel">
          {children}
        </div>
      ) : null}
    </div>
  );
}

export interface NavTabItem {
  to: string;
  label: ReactNode;
  badge?: ReactNode;
  end?: boolean;
}

export function NavTabs({ items, label }: { items: NavTabItem[]; label?: string }) {
  return (
    <nav aria-label={label} className="tabs">
      {items.map((t) => (
        <NavLink key={t.to} to={t.to} end={t.end} className={({ isActive }) => `tab ${isActive ? "tab-active" : ""}`}>
          {t.label}
          {t.badge !== undefined ? <span className="tab-badge">{t.badge}</span> : null}
        </NavLink>
      ))}
    </nav>
  );
}

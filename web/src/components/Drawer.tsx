// Drawer: a side panel over the page, for detail that keeps its context visible,
// such as a check's evidence beside the check list, or the Knowledge Assistant.
// Escape or the close button closes it; focus stays inside while it is open.
import { type ReactNode, useRef } from "react";
import { createPortal } from "react-dom";
import { Button } from "./Button";
import { useFocusTrap } from "./useFocusTrap";

export interface DrawerProps {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  subtitle?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  width?: number;
}

export function Drawer({ open, onClose, title, subtitle, children, footer, width = 520 }: DrawerProps) {
  const ref = useRef<HTMLDivElement>(null);
  useFocusTrap(ref, open, onClose);
  if (!open) return null;
  return createPortal(
    <div className="overlay overlay-drawer" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={ref} role="dialog" aria-modal="true" aria-label={typeof title === "string" ? title : undefined} className="drawer" style={{ width: `min(${width}px, 100vw)` }} tabIndex={-1}>
        <div className="drawer-head">
          <div className="stack-sm" style={{ gap: 2 }}>
            <h2>{title}</h2>
            {subtitle ? <div className="muted small">{subtitle}</div> : null}
          </div>
          <Button variant="ghost" icon="x" aria-label="Close" onClick={onClose} />
        </div>
        <div className="drawer-body">{children}</div>
        {footer ? <div className="drawer-foot">{footer}</div> : null}
      </div>
    </div>,
    document.body,
  );
}

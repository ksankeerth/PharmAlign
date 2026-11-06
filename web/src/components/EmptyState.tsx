// EmptyState: when a list or panel has nothing to show. Say why, and offer the next
// step as an action when there is one.
import type { ReactNode } from "react";
import { Icon, type IconName } from "./Icon";

export function EmptyState({ title, children, icon = "inbox", action }: { title: string; children?: ReactNode; icon?: IconName; action?: ReactNode }) {
  return (
    <div className="empty">
      <Icon name={icon} size={22} />
      <div className="empty-title">{title}</div>
      {children ? <div className="empty-text">{children}</div> : null}
      {action ? <div className="empty-action">{action}</div> : null}
    </div>
  );
}

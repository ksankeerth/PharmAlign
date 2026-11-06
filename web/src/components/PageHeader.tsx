// PageHeader: the top of every screen. Breadcrumbs, a title, an optional subtitle or
// meta line, status pills beside the title, and the screen's actions on the right.
import { Children, isValidElement, type ReactElement, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { Icon } from "./Icon";

export interface Crumb {
  label: string;
  to?: string;
}

export interface PageHeaderProps {
  title: ReactNode;
  breadcrumbs?: Crumb[];
  subtitle?: ReactNode;
  /** Pills or badges shown beside the title. */
  status?: ReactNode;
  actions?: ReactNode;
  /** Short id shown before the title, such as a case number. */
  eyebrow?: ReactNode;
}

/** The plain text of a node made of strings and simple wrappers, such as <code>CASE-1042</code>. */
function textOf(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (isValidElement(node)) return Children.toArray((node as ReactElement<{ children?: ReactNode }>).props.children).map(textOf).join("");
  return "";
}

export function PageHeader({ title, breadcrumbs: allCrumbs, subtitle, status, actions, eyebrow }: PageHeaderProps) {
  // The last crumb names this page. When the id above the title says the same, show it once.
  const last = allCrumbs?.[allCrumbs.length - 1];
  const breadcrumbs = last && !last.to && eyebrow && textOf(eyebrow) === last.label ? allCrumbs?.slice(0, -1) : allCrumbs;
  return (
    <header className="page-header">
      {breadcrumbs?.length ? (
        <nav aria-label="Breadcrumb" className="crumbs">
          <ol>
            {breadcrumbs.map((c, i) => (
              <li key={`${c.label}-${i}`}>
                {c.to && i < breadcrumbs.length - 1 ? <Link to={c.to}>{c.label}</Link> : <span aria-current={i === breadcrumbs.length - 1 ? "page" : undefined}>{c.label}</span>}
                {i < breadcrumbs.length - 1 ? <Icon name="chevron-right" size={12} /> : null}
              </li>
            ))}
          </ol>
        </nav>
      ) : null}
      <div className="page-header-main">
        <div className="page-header-titles">
          {eyebrow ? <div className="page-eyebrow">{eyebrow}</div> : null}
          <div className="row page-title-row">
            <h1>{title}</h1>
            {status}
          </div>
          {subtitle ? <div className="page-subtitle">{subtitle}</div> : null}
        </div>
        {actions ? <div className="page-actions">{actions}</div> : null}
      </div>
    </header>
  );
}

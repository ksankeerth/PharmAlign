// Left navigation for each view. Switching person ("View as") switches these sections.
// Each item links to a route in src/routes.tsx.
import type { View } from "../api/types";
import type { IconName } from "../components/Icon";

export interface NavItem {
  to: string;
  label: string;
  icon: IconName;
  /** Match the path exactly (for parents of other items). */
  end?: boolean;
}

export interface NavSection {
  label: string;
  items: NavItem[];
}

const WATCH: NavSection = {
  label: "Foreign watch",
  items: [
    { to: "/watch/notices", label: "Foreign notices", icon: "radar" },
    { to: "/watch/matches", label: "Matches", icon: "link" },
    { to: "/watch/incidents", label: "Incidents", icon: "layers" },
    { to: "/watch/coverage", label: "Coverage", icon: "globe" },
  ],
};

const KNOWLEDGE: NavSection = {
  label: "Knowledge",
  items: [
    { to: "/admin/library", label: "Document library", icon: "book" },
    { to: "/admin/kb-search", label: "Knowledge search", icon: "search" },
  ],
};

const INBOX: NavSection = { label: "Inbox", items: [{ to: "/notifications", label: "Notifications", icon: "bell" }] };

const CASE_NAV: NavSection[] = [
  {
    label: "Review",
    items: [
      { to: "/cases", label: "Case queue", icon: "inbox", end: true },
      { to: "/cases/spot-checks", label: "Spot checks", icon: "list-checks" },
      { to: "/cases/decisions", label: "Decisions", icon: "stamp" },
    ],
  },
  KNOWLEDGE,
  WATCH,
  INBOX,
];

const ADMIN_NAV: NavSection[] = [
  {
    label: "Knowledge",
    items: [
      ...KNOWLEDGE.items,
      { to: "/owner/documents", label: "Document reviews", icon: "file-text" },
      { to: "/owner/adoptions", label: "Adoptions", icon: "book" },
      { to: "/owner/slots", label: "Required slots", icon: "list-checks" },
    ],
  },
  {
    label: "Policy",
    items: [
      { to: "/owner/packs", label: "Packs and versions", icon: "layers" },
      { to: "/owner/gaps", label: "Gaps", icon: "flag" },
    ],
  },
  {
    label: "Operations",
    items: [
      { to: "/admin/agents", label: "Agent health", icon: "activity" },
      { to: "/admin/agent-runs", label: "Agent runs", icon: "cpu" },
      { to: "/admin/watch-sources", label: "Watch sources", icon: "radar" },
      { to: "/admin/simulations", label: "Simulations", icon: "beaker", end: true },
      { to: "/admin/simulations/compare", label: "Governed vs ungoverned", icon: "compare" },
      { to: "/admin/people", label: "People and roles", icon: "users" },
      { to: "/admin/demo", label: "Demo", icon: "database" },
    ],
  },
  {
    label: "Audit",
    items: [
      { to: "/audit", label: "Audit log", icon: "history", end: true },
      { to: "/audit/cases", label: "Case reconstruction", icon: "file-text" },
      { to: "/audit/verify", label: "Log verification", icon: "shield" },
    ],
  },
  INBOX,
];

// The UI has two roles, so the five views share two navigations (see lib/roles.ts).
export const NAVIGATION: Record<View, NavSection[]> = {
  case_officer: CASE_NAV,
  decision_maker: CASE_NAV,
  platform_admin: ADMIN_NAV,
  process_owner: ADMIN_NAV,
  auditor: ADMIN_NAV,
};

/** Where each view lands after switching person. */
export function homeFor(view: View | null | undefined): string {
  if (!view) return "/cases";
  return NAVIGATION[view][0].items[0].to;
}

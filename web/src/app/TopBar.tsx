// The top bar: wordmark, workspace switcher, mode badge, Ask, notifications, View as, and
// a settings menu (theme).
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { Icon, Menu, MenuDivider, MenuItem, MenuLabel, useToast } from "../components";
import { useApi } from "../hooks/useApi";
import { formatRelative } from "../lib/format";
import { VIEW_LABEL } from "../lib/labels";
import type { View } from "../api/types";
import { useAssistant } from "./assistant/AssistantContext";
import { homeFor } from "./navigation";
import { useSession } from "./session";
import { type ThemeChoice, useTheme } from "./theme";

function initials(name: string) {
  return name
    .split(" ")
    .map((p) => p[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
}

const MODE_LABEL: Record<string, string> = { simulation: "Simulation", shadow: "Shadow", live: "Live", draft: "Setup" };

const MODE_TITLE: Record<string, string> = {
  draft: "Setup mode. This country is still being set up, and no cases use it yet.",
  simulation: "Simulation mode. All companies, products and cases are synthetic, and no real records change.",
  shadow: "Shadow mode. Real cases are checked alongside the current process, and nothing is sent to the authority.",
  live: "Live mode. Decisions in this country change real records.",
};

export function Wordmark() {
  return (
    <Link to="/" className="wordmark" aria-label="PharmAlign home">
      <svg width="22" height="22" viewBox="0 0 32 32" aria-hidden="true">
        <rect width="32" height="32" rx="7" fill="var(--accent)" />
        <path d="M9 22V10h7a4.5 4.5 0 0 1 0 9h-7" fill="none" stroke="var(--on-accent)" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
        <path d="M17 22h6" stroke="var(--on-accent)" strokeOpacity="0.7" strokeWidth="2.6" strokeLinecap="round" />
      </svg>
      <span>PharmAlign</span>
    </Link>
  );
}

function NotificationBell() {
  const { me } = useSession();
  const { data } = useApi(() => api.listNotifications({ limit: 50 }), [me?.person_id]);
  const items = data?.items ?? [];
  const unread = items.filter((n) => n.state !== "acknowledged");
  const urgent = unread.some((n) => n.severity === "urgent");
  const navigate = useNavigate();
  return (
    <Menu
      label="Notifications"
      width={360}
      trigger={(p) => (
        <button type="button" className={`topbar-icon-btn ${urgent ? "bell-urgent" : ""}`} aria-label={`Notifications, ${unread.length} unread`} {...p}>
          <Icon name="bell" size={18} />
          {unread.length ? <span className="bell-count num">{unread.length}</span> : null}
        </button>
      )}
    >
      {(close) => (
        <>
          <MenuLabel>
            {unread.length} unread{urgent ? " · urgent waiting" : ""}
          </MenuLabel>
          {items.slice(0, 6).map((n) => (
            <MenuItem
              key={n.id}
              onSelect={() => {
                close();
                navigate(n.link ?? "/notifications");
              }}
              hint={formatRelative(n.created_at)}
            >
              <span className={`notif-dot ${n.state === "acknowledged" ? "notif-read" : `notif-${n.severity}`}`} aria-hidden="true" />
              <span className="truncate" style={{ maxWidth: 230 }}>
                {n.severity === "urgent" && n.state !== "acknowledged" ? <strong>Urgent: </strong> : null}
                {n.title}
              </span>
            </MenuItem>
          ))}
          {!items.length ? <div className="muted small" style={{ padding: "8px 10px" }}>Nothing yet.</div> : null}
          <MenuDivider />
          <MenuItem
            onSelect={() => {
              close();
              navigate("/notifications");
            }}
          >
            All notifications
          </MenuItem>
        </>
      )}
    </Menu>
  );
}

export const ASK_SHORTCUT = /Mac|iPhone|iPad/.test(globalThis.navigator?.platform ?? "") ? "\u2318K" : "Ctrl K";

function AskButton() {
  const { open, setOpen } = useAssistant();
  return (
    <button
      type="button"
      id="ask-button"
      className="ask-btn"
      aria-expanded={open}
      aria-controls="assistant-panel"
      aria-keyshortcuts="Control+K Meta+K"
      title={`Ask the Knowledge Assistant (${ASK_SHORTCUT})`}
      onClick={() => setOpen(!open)}
    >
      <Icon name="message" size={15} />
      <span>Ask</span>
      <kbd aria-hidden="true">{ASK_SHORTCUT}</kbd>
    </button>
  );
}

export function TopBar({ onToggleNav }: { onToggleNav: () => void }) {
  const { me, people, workspaces, workspace, switchPerson, switchWorkspace } = useSession();
  const { theme, setTheme } = useTheme();
  const navigate = useNavigate();
  const toast = useToast();

  const byView = people.reduce<Record<string, typeof people>>((acc, p) => {
    (acc[p.view] ??= []).push(p);
    return acc;
  }, {});
  const order: View[] = ["case_officer", "decision_maker", "process_owner", "platform_admin", "auditor"];

  const pickPerson = async (id: string) => {
    try {
      const m = await switchPerson(id);
      toast.info(`Viewing as ${m.display_name}, ${VIEW_LABEL[m.view]}`);
      // Another person in the same view keeps the screen they are on (a second Decision Maker opens the same package).
      if (m.view !== me?.view) navigate(homeFor(m.view));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not switch person");
    }
  };

  return (
    <header className="topbar">
      <button type="button" className="topbar-icon-btn nav-toggle" aria-label="Open navigation" onClick={onToggleNav}>
        <Icon name="menu" size={18} />
      </button>
      <Wordmark />

      {me?.view === "platform_admin" ? (
        <Menu
          label="Workspaces"
          align="left"
          width={320}
          trigger={(p) => (
            <button type="button" className="ws-switch" {...p}>
              <Icon name="globe" />
              <span className="ws-name truncate">{workspace?.name ?? "No workspace"}</span>
              <Icon name="chevron-down" size={14} />
            </button>
          )}
        >
          {(close) => (
            <>
              <MenuLabel>Country workspaces</MenuLabel>
              {workspaces.map((w) => (
                <MenuItem
                  key={w.id}
                  checked={w.id === workspace?.id}
                  hint={MODE_LABEL[w.mode] ?? w.mode}
                  onSelect={async () => {
                    close();
                    try {
                      await switchWorkspace(w.id);
                      toast.info(`Switched to ${w.name}`);
                      navigate(homeFor(me.view));
                    } catch (e) {
                      toast.error(e instanceof Error ? e.message : "Could not switch workspace");
                    }
                  }}
                >
                  {w.name}
                </MenuItem>
              ))}
              <MenuDivider />
              <MenuItem
                onSelect={() => {
                  close();
                  navigate("/admin/setup?new=1");
                }}
              >
                <Icon name="plus" />
                Set up a country
              </MenuItem>
              <MenuDivider />
              <div className="menu-note">{workspace?.authority_name ?? ""}. No real authority's name or branding is used.</div>
            </>
          )}
        </Menu>
      ) : workspace ? (
        <span className="ws-switch ws-label" title={`${workspace.authority_name}. No real authority's name or branding is used.`}>
          <Icon name="globe" />
          <span className="ws-name truncate">{workspace.name}</span>
        </span>
      ) : null}

      {workspace ? (
        <span className="mode-badge" title={MODE_TITLE[workspace.mode]}>
          <Icon name="beaker" size={13} />
          <span>{MODE_LABEL[workspace.mode] ?? workspace.mode}</span>
          {workspace.mode === "simulation" ? <span className="mode-badge-sub">synthetic data</span> : null}
        </span>
      ) : null}

      <div className="topbar-spacer" />

      <AskButton />
      <NotificationBell />

      <Menu
        label="View as"
        width={300}
        trigger={(p) => (
          <button type="button" className="viewas" {...p} aria-label={`View as: ${me?.display_name ?? ""}. Change person`}>
            <span className="avatar" aria-hidden="true">
              {me ? initials(me.display_name) : "?"}
            </span>
            <span className="viewas-text">
              <span className="viewas-name">{me?.display_name ?? "Loading"}</span>
              <span className="viewas-view">{me ? VIEW_LABEL[me.view] : ""}</span>
            </span>
            <Icon name="chevron-down" size={14} />
          </button>
        )}
      >
        {(close) => (
          <>
            <MenuLabel>View as a demo person</MenuLabel>
            {order
              .filter((v) => byView[v]?.length)
              .map((v) => (
                <div key={v}>
                  <div className="menu-group">{VIEW_LABEL[v]}</div>
                  {byView[v].map((p) => (
                    <MenuItem
                      key={p.person_id}
                      checked={p.person_id === me?.person_id}
                      onSelect={() => {
                        close();
                        void pickPerson(p.person_id);
                      }}
                    >
                      <span className="avatar avatar-sm" aria-hidden="true">
                        {initials(p.display_name)}
                      </span>
                      {p.display_name}
                    </MenuItem>
                  ))}
                </div>
              ))}
            <MenuDivider />
            <div className="menu-note">Each view is one named person, so separation-of-duty rules still apply.</div>
          </>
        )}
      </Menu>

      <Menu
        label="Settings"
        width={240}
        trigger={(p) => (
          <button type="button" className="topbar-icon-btn" aria-label="Settings" {...p}>
            <Icon name="settings" size={18} />
          </button>
        )}
      >
        {(close) => (
          <>
            <MenuLabel>Theme</MenuLabel>
            {(["system", "light", "dark"] as ThemeChoice[]).map((t) => (
              <MenuItem key={t} checked={theme === t} onSelect={() => setTheme(t)}>
                <Icon name={t === "system" ? "monitor" : t === "light" ? "sun" : "moon"} />
                {t === "system" ? "Match system" : t === "light" ? "Light" : "Dark"}
              </MenuItem>
            ))}
            {me?.view === "platform_admin" ? (
              <>
                <MenuDivider />
                <MenuItem
                  onSelect={() => {
                    close();
                    navigate("/admin/demo");
                  }}
                >
                  <Icon name="beaker" />
                  Demo
                </MenuItem>
              </>
            ) : null}
          </>
        )}
      </Menu>
    </header>
  );
}

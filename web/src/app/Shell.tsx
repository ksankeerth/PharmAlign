// The app shell: top bar, left navigation for the current view, and the page outlet.
// In demo mode, `?as=<personId>` on any URL switches person once (handy for links and tests).
import { Suspense, useEffect, useRef, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { CONTRACT_VERSION } from "../api";
import { ErrorState, Icon, OpenAccessNotice, SkeletonLines } from "../components";
import { AssistantDock } from "./assistant/AssistantDock";
import { useAssistant } from "./assistant/AssistantContext";
import { NAVIGATION } from "./navigation";
import { useSession } from "./session";
import { TopBar } from "./TopBar";

function SideNav({ open, onNavigate }: { open: boolean; onNavigate: () => void }) {
  const { view } = useSession();
  const sections = view ? NAVIGATION[view] : [];
  return (
    <nav className={`sidenav ${open ? "sidenav-open" : ""}`} aria-label="Main">
      <div className="sidenav-scroll">
        {sections.map((s) => (
          <div className="nav-section" key={s.label}>
            <div className="nav-section-label">{s.label}</div>
            <ul>
              {s.items.map((it) => (
                <li key={it.to}>
                  <NavLink to={it.to} end={it.end} className={({ isActive }) => `nav-link ${isActive ? "nav-link-active" : ""}`} onClick={onNavigate}>
                    <Icon name={it.icon} />
                    <span>{it.label}</span>
                  </NavLink>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
      <div className="sidenav-foot">
        <div className="row xsmall">
          <Icon name="beaker" size={12} />
          <span>Synthetic data</span>
        </div>
        <p className="xsmall muted">All companies, products, cases and people are fictional.</p>
        <p className="xsmall muted mono">contract {CONTRACT_VERSION}</p>
      </div>
    </nav>
  );
}

export function Shell() {
  const { me, loading, error, switchPerson } = useSession();
  // A server running demo identity lets a link name the person to view as.
  const canViewAs = Boolean(me?.is_demo);
  const [navOpen, setNavOpen] = useState(false);
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const navigate = useNavigate();
  const handledAs = useRef(false);
  const [switching, setSwitching] = useState(Boolean(params.get("as")));

  const assistant = useAssistant();
  useEffect(() => setNavOpen(false), [location.pathname]);

  // Ctrl or Cmd and K opens the Knowledge Assistant from anywhere, and closes it when it already has focus.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey) || e.altKey || e.key.toLowerCase() !== "k") return;
      e.preventDefault();
      const inside = document.activeElement?.closest("#assistant-panel");
      assistant.setOpen(inside ? false : true);
      if (!inside) document.getElementById("as-question")?.focus();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [assistant]);

  useEffect(() => {
    const as = params.get("as");
    if (!as || handledAs.current) {
      setSwitching(false);
      return;
    }
    if (loading) return; // wait to learn whether the server runs demo identity
    if (!canViewAs) {
      setSwitching(false);
      return;
    }
    handledAs.current = true;
    switchPerson(as)
      .catch(() => undefined)
      .finally(() => {
        const next = new URLSearchParams(params);
        next.delete("as");
        setParams(next, { replace: true });
        setSwitching(false);
      });
  }, [params, setParams, switchPerson, canViewAs, loading, navigate]);

  return (
    <div className="shell">
      <a href="#main" className="skip-link">
        Skip to content
      </a>
      <TopBar onToggleNav={() => setNavOpen((o) => !o)} />
      <div className="shell-body">
        <SideNav open={navOpen} onNavigate={() => setNavOpen(false)} />
        {navOpen ? <div className="nav-scrim" onClick={() => setNavOpen(false)} aria-hidden="true" /> : null}
        <main id="main" className="main" tabIndex={-1}>
          <OpenAccessNotice />
          {error ? (
            <ErrorState error={error} />
          ) : loading || switching ? (
            <SkeletonLines lines={6} />
          ) : (
            <Suspense fallback={<SkeletonLines lines={6} />}>
              <Outlet />
            </Suspense>
          )}
        </main>
        <AssistantDock />
      </div>
    </div>
  );
}

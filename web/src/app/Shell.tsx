// The app shell: top bar, left navigation for the current view, and the page outlet.
// In demo mode, `?as=<personId>` on any URL switches person once (handy for links and tests).
import { type RefCallback, Suspense, useEffect, useRef, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { CONTRACT_VERSION } from "../api";
import { ErrorState, Icon, OpenAccessNotice, SkeletonLines } from "../components";
import { AssistantDock } from "./assistant/AssistantDock";
import { useAssistant } from "./assistant/AssistantContext";
import { NAVIGATION } from "./navigation";
import { useSession } from "./session";
import { TopBar } from "./TopBar";
import { hasSeenTour, LIBRARY_PATH, rememberTour, TOUR_STEPS, type TourAnchors, WorkspaceTour } from "./WorkspaceTour";

function SideNav({ open, onNavigate, libraryAnchor }: { open: boolean; onNavigate: (to: string) => void; libraryAnchor?: RefCallback<HTMLAnchorElement> }) {
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
                  <NavLink to={it.to} end={it.end} ref={it.to === LIBRARY_PATH ? libraryAnchor : undefined}
                    className={({ isActive }) => `nav-link ${isActive ? "nav-link-active" : ""}`} onClick={() => onNavigate(it.to)}>
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
  const personId = !loading && !switching && !error ? me?.person_id : undefined;
  const [tourFor, setTourFor] = useState<string | null>(null);
  const [step, setStep] = useState(0);
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const touring = Boolean(personId && tourFor === personId);
  const { target, path: tourPath } = TOUR_STEPS[step];
  const firstStep = touring && target === "library";

  useEffect(() => {
    setStep(0);
    returnFocus.current = null;
    setTourFor(personId && !hasSeenTour(personId) ? personId : null);
  }, [personId]);

  function closeTour() {
    if (personId) rememberTour(personId);
    setTourFor(null);
    const fallback = step > 0 ? anchor : document.getElementById("main");
    (returnFocus.current ?? fallback)?.focus({ preventScroll: true });
  }

  function advanceTour() {
    const next = TOUR_STEPS[step + 1];
    if (!next) {
      returnFocus.current = anchor;
      return closeTour();
    }
    setStep(step + 1);
    setNavOpen(false);
    if (next.path && location.pathname !== next.path) {
      navigate(next.path);
    }
  }

  // A link or browser Back can leave the tour's route between steps.
  const previousPath = useRef(location.pathname);
  useEffect(() => {
    const moved = previousPath.current !== location.pathname;
    previousPath.current = location.pathname;
    if (moved && tourPath && location.pathname !== tourPath && touring && personId) {
      rememberTour(personId);
      setTourFor(null);
    }
  }, [location.pathname, personId, touring, tourPath]);

  const assistant = useAssistant();
  useEffect(() => setNavOpen(false), [location.pathname]);

  // Ctrl or Cmd and K opens the Knowledge Assistant from anywhere, and closes it when it already has focus.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey) || e.altKey || e.key.toLowerCase() !== "k") return;
      if (document.activeElement?.closest('[aria-modal="true"]')) return;
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
    if (!as) {
      setSwitching(false);
      return;
    }
    if (loading || handledAs.current) return; // wait for identity and any pending switch
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
      <TopBar onToggleNav={() => firstStep ? closeTour() : setNavOpen((o) => !o)} onTakeTour={personId ? () => {
        returnFocus.current = document.activeElement as HTMLElement;
        setStep(0);
        setTourFor(personId);
      } : undefined} />
      <div className="shell-body">
        <SideNav open={navOpen || firstStep} libraryAnchor={firstStep ? setAnchor : undefined} onNavigate={(to) => {
          setNavOpen(false);
          if (firstStep && to === LIBRARY_PATH) setStep(1);
          else if (touring) closeTour();
        }} />
        {navOpen || firstStep ? <div className="nav-scrim" onClick={() => { setNavOpen(false); if (firstStep) closeTour(); }} aria-hidden="true" /> : null}
        <main id="main" className="main" tabIndex={-1}>
          <OpenAccessNotice />
          {error ? (
            <ErrorState error={error} />
          ) : loading || switching ? (
            <SkeletonLines lines={6} />
          ) : (
            <Suspense fallback={<SkeletonLines lines={6} />}>
              <Outlet context={{
                category: touring && target === "category" ? setAnchor : undefined,
                status: touring && target === "status" ? setAnchor : undefined,
                journey: touring && target === "journey" ? setAnchor : undefined,
              } satisfies TourAnchors} />
            </Suspense>
          )}
        </main>
        <AssistantDock />
      </div>
      {touring && anchor && (!tourPath || tourPath === location.pathname) ? (
        <WorkspaceTour key={step} step={step} anchor={anchor} onClose={closeTour} onNext={advanceTour} />
      ) : null}
    </div>
  );
}

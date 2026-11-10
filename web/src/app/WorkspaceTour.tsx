import { arrow, autoUpdate, flip, offset, shift, useFloating, type Middleware } from "@floating-ui/react-dom";
import { type RefCallback, useEffect, useLayoutEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { Button } from "../components";
import "./tour.css";

export const LIBRARY_PATH = "/admin/library";

export interface TourAnchors {
  category?: RefCallback<HTMLElement>;
  status?: RefCallback<HTMLSelectElement>;
  journey?: RefCallback<HTMLElement>;
}

const dismissedInTab = new Set<string>();

export function hasSeenTour(personId: string) {
  if (dismissedInTab.has(personId)) return true;
  try {
    return localStorage.getItem(`pharmalign.tour.${personId}`) === "seen";
  } catch {
    return false;
  }
}

export function rememberTour(personId: string) {
  dismissedInTab.add(personId);
  try {
    localStorage.setItem(`pharmalign.tour.${personId}`, "seen");
  } catch {
    // The in-memory flag still remembers the choice while this tab is open.
  }
}

export const TOUR_STEPS = [
  { target: "library", path: null, title: "Explore the document library", text: "Start here to find the laws, guidance and reference documents used in case reviews.", action: "Open library" },
  { target: "category", path: LIBRARY_PATH, title: "Browse by document category", text: "Choose a category to filter the documents below. The number beside each category shows how many sources it contains.", action: "Next" },
  { target: "status", path: LIBRARY_PATH, title: "Check the legal status", text: "Filter by In force to find documents that apply in this workspace.", action: "View case journey" },
  { target: "journey", path: "/cases", title: "Follow an application through review", text: "Choose a stage to see the cases there, from the company's submission to a licence on record.", action: "Done" },
] as const;

// Use the same fixed-viewport measurements as the callout, including after scrolling or resizing.
const spotlight: Middleware = {
  name: "spotlight",
  fn({ rects }) {
    const { x, y, width, height } = rects.reference;
    const padding = 6;
    const left = `${x - padding}px`;
    const right = `${x + width + padding}px`;
    const top = `${y - padding}px`;
    const bottom = `${y + height + padding}px`;
    const viewport = "0 0, 100% 0, 100% 100%, 0 100%, 0 0";
    const cutout = `${left} ${top}, ${right} ${top}, ${right} ${bottom}, ${left} ${bottom}, ${left} ${top}`;
    return { data: { clipPath: `polygon(evenodd, ${viewport}, ${cutout})` } };
  },
};

export function WorkspaceTour({ step, anchor, onNext, onClose }: {
  step: number;
  anchor: HTMLElement;
  onNext: () => void;
  onClose: () => void;
}) {
  const pointer = useRef<HTMLSpanElement>(null);
  const content = TOUR_STEPS[step];
  const showJourney = content.target === "journey";
  const { refs, floatingStyles, middlewareData, placement, isPositioned } = useFloating({
    elements: { reference: anchor },
    placement: showJourney ? "top" : step === 0 ? "right" : "bottom",
    strategy: "fixed",
    whileElementsMounted: autoUpdate,
    middleware: [
      offset(13),
      flip({ padding: 12, fallbackAxisSideDirection: "end" }),
      shift({ padding: 12, crossAxis: true }),
      arrow({ element: pointer, padding: 14 }),
      spotlight,
    ],
  });
  useLayoutEffect(() => {
    anchor.classList.add("tour-target");
    const card = refs.floating.current;
    // A stacked journey can span several screens. Keep its heading and the callout together.
    if (showJourney && card) anchor.style.setProperty("--tour-card-height", `${card.offsetHeight}px`);
    anchor.scrollIntoView({ block: showJourney ? "start" : "center", inline: "nearest", behavior: "instant" });
    card?.focus({ preventScroll: true });
    return () => {
      anchor.classList.remove("tour-target");
      anchor.style.removeProperty("--tour-card-height");
    };
  }, [anchor, refs.floating, showJourney]);

  useEffect(() => {
    const dismiss = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !document.querySelector('[aria-modal="true"]')) onClose();
    };
    document.addEventListener("keydown", dismiss);
    return () => document.removeEventListener("keydown", dismiss);
  }, [onClose]);

  return createPortal(
    <>
      {isPositioned && <div aria-hidden="true" className="tour-backdrop" style={{ clipPath: middlewareData.spotlight?.clipPath }} />}
      <aside
        ref={refs.setFloating}
        style={{ ...floatingStyles, opacity: isPositioned ? 1 : 0 }}
        className="tour-card stack-sm"
        role="dialog"
        aria-labelledby="tour-title"
        aria-describedby="tour-description"
        tabIndex={-1}
        data-side={placement.split("-")[0]}
      >
        <span ref={pointer} className="tour-arrow" aria-hidden="true"
          style={{ left: middlewareData.arrow?.x, top: middlewareData.arrow?.y }} />
        <p className="small muted">Step {step + 1} of {TOUR_STEPS.length}</p>
        <h2 id="tour-title">{content.title}</h2>
        <p id="tour-description">{content.text}</p>
        <div className="row-between">
          <Button size="sm" variant="ghost" onClick={onClose}>Skip tour</Button>
          <Button size="sm" variant="primary" onClick={onNext}>{content.action}</Button>
        </div>
      </aside>
    </>,
    document.body,
  );
}

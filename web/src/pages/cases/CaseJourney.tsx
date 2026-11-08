// The case journey: the steps from a company's submission to a licence on record, with how many cases are at each.
// Written for someone with no regulatory background: one short line per step, and who acts there.
// Choosing a step filters the queue below. It uses only what the queue already loads, so no new API.
import { Icon, type IconName } from "../../components";

export type JourneyState =
  | "intake"
  | "running"
  | "review"
  | "held"
  | "company"
  | "ready"
  | "awaiting"
  | "recording"
  | "recorded"
  | "on_hold"
  | "unknown"
  | "rejected"
  | "closed";

/** One case, as the journey needs it. */
export interface JourneyCase {
  state: JourneyState;
}

type Tone = "plain" | "ai" | "held" | "look" | "pass";

interface Step {
  state: JourneyState;
  title: string;
  who: string;
  icon: IconName;
  text: string;
  tone: Tone;
}

const MAIN: Step[] = [
  { state: "intake", title: "Received", who: "Company", icon: "inbox", text: "The company submits its application.", tone: "plain" },
  { state: "running", title: "AI checks", who: "AI agents", icon: "sparkle", text: "AI reads the file and checks it against the law.", tone: "ai" },
  { state: "review", title: "Officer review", who: "Case officer", icon: "user", text: "An officer reviews each finding and its evidence.", tone: "plain" },
  { state: "ready", title: "Ready to decide", who: "Case officer", icon: "user", text: "Every blocking issue is cleared.", tone: "pass" },
  { state: "awaiting", title: "To be signed", who: "Decision maker", icon: "pen", text: "A senior official signs. The AI never signs.", tone: "look" },
  { state: "recorded", title: "Licence on record", who: "National register", icon: "database", text: "Entered in the medicines register.", tone: "pass" },
];

const SIDE: Step[] = [
  { state: "held", title: "Held", who: "If a requirement fails", icon: "pause", text: "Waits until it is fixed, then is checked again.", tone: "held" },
  { state: "company", title: "Waiting for the company", who: "If something needs explaining", icon: "send", text: "Questions sent. The clock stops until they answer.", tone: "plain" },
];

const AFTER: { state: JourneyState; title: string }[] = [
  { state: "on_hold", title: "Decision on hold" },
  { state: "unknown", title: "Register outcome unknown" },
  { state: "rejected", title: "Register refused the record" },
  { state: "closed", title: "Closed" },
];

interface Props {
  cases: JourneyCase[] | null;
  active: string;
  onPick: (s: JourneyState) => void;
}

function Box({ step, n, active, onPick, num }: { step: Step; n: number | null; active: boolean; onPick: () => void; num?: number }) {
  return (
    <button type="button" className={`cj-box cj-${step.tone}${n ? "" : " cj-empty"}`} aria-pressed={active} onClick={onPick}>
      <span className="cj-title">
        {num ? <span className="cj-num">{num}</span> : null}
        {step.title}
      </span>
      <span className="cj-count">{n ?? "…"}</span>
      <span className="cj-text">{step.text}</span>
      <span className="cj-who">
        <Icon name={step.icon} size={12} />
        {step.who}
      </span>
    </button>
  );
}

export function CaseJourney({ cases, active, onPick }: Props) {
  const n = (s: JourneyState) => (cases ? cases.filter((c) => c.state === s).length : null);
  const pick = (s: JourneyState) => () => onPick(s);
  const after = AFTER.map((a) => ({ ...a, n: n(a.state) ?? 0 })).filter((a) => a.n > 0);
  const sending = n("recording") ?? 0;

  return (
    <section className="cj" aria-labelledby="cj-heading">
      <header className="cj-head">
        <h2 id="cj-heading">How a medicine application moves</h2>
        <p>AI reads and checks. People decide and sign. Choose a step to see its cases.</p>
      </header>

      <ol className="cj-main">
        {MAIN.map((step, i) => (
          <li key={step.state} className="cj-step">
            <Box step={step} n={n(step.state)} active={active === step.state} onPick={pick(step.state)} num={i + 1} />
          </li>
        ))}
      </ol>

      <div className="cj-side">
        <span className="cj-side-label">From officer review</span>
        <ul>
          {SIDE.map((step) => (
            <li key={step.state}>
              <Box step={step} n={n(step.state)} active={active === step.state} onPick={pick(step.state)} />
            </li>
          ))}
        </ul>
      </div>

      <footer className="cj-foot">
        <span>
          <Icon name="shield" size={13} />
          Every step is recorded: who acted, on what evidence, and when.
        </span>
        {sending || after.length ? (
          <span className="cj-after">
            {sending ? <span className="cj-quiet">{sending} being entered in the register</span> : null}
            {after.map((a) => (
              <button key={a.state} type="button" className="cj-chip" aria-pressed={active === a.state} onClick={pick(a.state)}>
                {a.title} <strong>{a.n}</strong>
              </button>
            ))}
          </span>
        ) : null}
      </footer>
    </section>
  );
}

// Panel: a bordered section with an optional heading and actions. The basic building
// block for page content. Callout: a short highlighted note with a tone.
// SyntheticTag: marks company data as synthetic, wherever a company or product appears.
import type { ReactNode } from "react";
import { Icon, type IconName } from "./Icon";
import type { Tone } from "./StatusPill";

export function Panel({ title, actions, children, footer, padded = true, id }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; footer?: ReactNode; padded?: boolean; id?: string }) {
  return (
    <section className="panel" aria-labelledby={title && id ? `${id}-title` : undefined} id={id}>
      {title || actions ? (
        <div className="panel-head">
          {title ? <h2 id={id ? `${id}-title` : undefined}>{title}</h2> : <span />}
          {actions ? <div className="row">{actions}</div> : null}
        </div>
      ) : null}
      <div className={padded ? "panel-body" : "panel-body panel-body-flush"}>{children}</div>
      {footer ? <div className="panel-foot">{footer}</div> : null}
    </section>
  );
}

const CALLOUT_ICON: Record<Tone, IconName> = { pass: "check", look: "eye", held: "pause", error: "alert-octagon", accent: "info", neutral: "info" };

export function Callout({ tone = "neutral", title, children, icon }: { tone?: Tone; title?: ReactNode; children?: ReactNode; icon?: IconName }) {
  return (
    <div className={`callout callout-${tone}`}>
      <Icon name={icon ?? CALLOUT_ICON[tone]} />
      <div>
        {title ? <strong>{title}</strong> : null}
        {children ? <div>{children}</div> : null}
      </div>
    </div>
  );
}

export function SyntheticTag({ label = "Synthetic" }: { label?: string }) {
  return (
    <span className="synthetic-tag" title="Synthetic data for the demo. No real company, product or person.">
      <Icon name="beaker" size={11} />
      {label}
    </span>
  );
}

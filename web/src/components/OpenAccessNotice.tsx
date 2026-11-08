// The first thing a visitor reads, at the top of every screen. The prototype is open to anyone with its link.
import { Callout } from "./Panel";

export function OpenAccessNotice() {
  return (
    <div role="note" aria-label="About this prototype" className="open-access-notice">
      <Callout tone="look" icon="alert-triangle" title="This is a prototype with synthetic data only">
        Every company, product, case and person here is made up. Anyone with this link can view and change the data, so do not enter anything real.
        The data can be reset at any time.
      </Callout>
    </div>
  );
}

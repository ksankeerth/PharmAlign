// ErrorState: a failed load. Renders any thrown value as its problem body, with the
// stable code, and a retry button. A 403 explains that this view may not see it.
// For a refused action (409) inside a form, use GuardList instead.
import { isApiError, toProblem } from "../api/errors";
import { Button } from "./Button";
import { GuardList } from "./GuardList";
import { Icon } from "./Icon";

export function ErrorState({ error, onRetry, title }: { error: unknown; onRetry?: () => void; title?: string }) {
  const p = toProblem(error);
  const forbidden = p.status === 403;
  return (
    <div className={`error-state ${forbidden ? "error-state-forbidden" : ""}`} role="alert">
      <div className="row">
        <Icon name={forbidden ? "lock" : "alert-octagon"} size={18} />
        <strong>{title ?? (forbidden ? "This view can't open this" : p.title)}</strong>
      </div>
      {p.detail ? <p>{p.detail}</p> : null}
      {forbidden ? <p className="muted small">Switch person with "View as" in the top bar to see this screen as another role.</p> : null}
      {isApiError(error) && error.failed.length ? <GuardList guards={error.failed} onlyFailed /> : null}
      <div className="row">
        <code className="xsmall muted">
          {p.status} · {p.code}
        </code>
        {onRetry ? (
          <Button size="sm" icon="refresh" onClick={onRetry}>
            Try again
          </Button>
        ) : null}
      </div>
    </div>
  );
}

// GuardList: shows the guards behind an action. Pass the `failed` list from a 409
// (ApiError.failed) to explain a refusal, or a full list (PromotionCheck.guards,
// SigningPackage.guards) to show what is clear and what still blocks.
// Each guard links to its check when it carries a check id and `checkHref` is given.
import { Link } from "react-router-dom";
import type { GuardResult } from "../api/types";
import { GUARD_LABEL, humanize } from "../lib/labels";
import { Icon } from "./Icon";

export interface GuardListProps {
  guards: GuardResult[];
  title?: string;
  /** Builds a link for a guard tied to a check result. */
  checkHref?: (checkResultId: string) => string;
  /** Hide passing guards (default false). */
  onlyFailed?: boolean;
}

export function GuardList({ guards, title, checkHref, onlyFailed }: GuardListProps) {
  const shown = onlyFailed ? guards.filter((g) => !g.passed) : guards;
  const failed = guards.filter((g) => !g.passed).length;
  return (
    <section className={`guards ${failed ? "guards-failed" : "guards-ok"}`} aria-label={title ?? "Guards"} role={failed ? "alert" : undefined}>
      <div className="guards-head">
        <Icon name={failed ? "lock" : "check"} />
        <strong>{title ?? (failed ? `${failed} guard${failed === 1 ? "" : "s"} failed` : "Every guard passes")}</strong>
      </div>
      <ul>
        {shown.map((g, i) => (
          <li key={`${g.guard}-${g.check_id ?? i}`} className={g.passed ? "guard-pass" : "guard-fail"}>
            <Icon name={g.passed ? "check" : "x"} label={g.passed ? "Passed" : "Failed"} />
            <div>
              <div className="guard-name">{GUARD_LABEL[g.guard] ?? humanize(g.guard)}</div>
              {g.detail ? (
                <div className="guard-detail">
                  {g.detail}
                  {g.check_id && checkHref ? (
                    <>
                      {" "}
                      <Link to={checkHref(g.check_id)}>Open check</Link>
                    </>
                  ) : null}
                </div>
              ) : null}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

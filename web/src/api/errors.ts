// Every failed call throws an ApiError carrying the RFC 9457 problem body.
// A refused action (409) carries a GuardProblem: `failed` lists every guard that failed.
import type { GuardProblem, GuardResult, Problem } from "./types";

export class ApiError extends Error {
  readonly status: number;
  readonly problem: Problem | GuardProblem;

  constructor(problem: Problem | GuardProblem) {
    super(problem.detail ? `${problem.title}: ${problem.detail}` : problem.title);
    this.name = "ApiError";
    this.status = problem.status;
    this.problem = problem;
  }

  /** Stable machine code, such as `guard_failed`, `forbidden_view` or `not_found`. */
  get code(): string {
    return this.problem.code;
  }

  /** True when the server refused the action and listed the failed guards. */
  get isGuardProblem(): boolean {
    return isGuardProblem(this.problem);
  }

  /** Failed guards from a 409, or an empty list. */
  get failed(): GuardResult[] {
    return isGuardProblem(this.problem) ? this.problem.failed : [];
  }

  /** The fingerprint the server holds now, when a stale one was refused. */
  get currentFingerprint(): string | null {
    return isGuardProblem(this.problem) ? (this.problem.current_fingerprint ?? null) : null;
  }
}

export function isGuardProblem(p: Problem | GuardProblem): p is GuardProblem {
  return Array.isArray((p as GuardProblem).failed) && (p as GuardProblem).failed.length > 0;
}

export function isApiError(e: unknown): e is ApiError {
  return e instanceof ApiError;
}

/** Turns any thrown value into a problem body, so error views have one shape to render. */
export function toProblem(e: unknown): Problem | GuardProblem {
  if (e instanceof ApiError) return e.problem;
  const detail = e instanceof Error ? e.message : String(e);
  return {
    type: "about:blank",
    title: "Something went wrong",
    status: 500,
    code: "client_error",
    detail: detail.slice(0, 500) || null,
    run_id: null,
  };
}

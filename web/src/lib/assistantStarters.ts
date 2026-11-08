// The questions the assistant panel offers when it opens on a case. They are read aloud, so each must read as a sentence.
import type { CheckStatus } from "../api/types";

/** "Why ..." for a check, worded for its status ("Why does SAME-01 need a look?", not "Why is SAME-01 look?"). */
export function whyQuestion(checkId: string, status: CheckStatus): string {
  switch (status) {
    case "look":
      return `Why does ${checkId} need a look?`;
    case "person_confirms":
      return `Why does ${checkId} need a person to confirm it?`;
    case "pass":
      return `Why did ${checkId} pass?`;
    case "waived":
      return `Why was ${checkId} waived?`;
    case "skipped":
      return `Why was ${checkId} skipped?`;
    case "error":
      return `Why did ${checkId} end in an error?`;
    case "running":
      return `What is ${checkId} doing now?`;
    default:
      return `Why is ${checkId} ${status}?`; // held, confirmed, pending, stale read naturally
  }
}

/** The three questions about one check. */
export function checkStarters(checkId: string, status: CheckStatus): string[] {
  return [whyQuestion(checkId, status), `Read the pages cited by ${checkId} again`, `Draft a comment on ${checkId}`];
}

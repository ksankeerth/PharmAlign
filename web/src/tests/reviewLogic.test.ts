import { describe, expect, it } from "vitest";
import { isApproving, needsNotes, reviewReadiness } from "../pages/owner/reviewLogic";

describe("document review readiness", () => {
  it("refuses the uploader and a failed quality gate, and asks for notes", () => {
    const own = reviewReadiness({ added_by: "p-priya", quality_status: "pass" }, "p-priya", "approved", "");
    expect(own.find((g) => g.guard === "not_own_upload")?.passed).toBe(false);
    const other = reviewReadiness({ added_by: "p-priya", quality_status: "pass" }, "p-sam", "approved", "");
    expect(other.every((g) => g.passed)).toBe(true);
    const failed = reviewReadiness({ added_by: "p-jordan", quality_status: "fail" }, "p-sam", "approved_with_notes", "ok");
    expect(failed.find((g) => g.guard === "quality_gate_passed")?.passed).toBe(false);
    expect(reviewReadiness({ added_by: "p-jordan", quality_status: "fail" }, "p-sam", "rejected", "garbled").every((g) => g.passed)).toBe(true);
    expect(reviewReadiness({ added_by: "p-jordan", quality_status: "pass" }, "p-sam", "rejected", "  ").find((g) => g.guard === "notes_given")?.passed).toBe(false);
    expect(needsNotes("needs_update")).toBe(true);
    expect(isApproving("approved_with_notes")).toBe(true);
  });
});

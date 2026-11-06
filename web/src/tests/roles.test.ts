import { describe, expect, it } from "vitest";
import { actsAs, roleGroup, roleLabel } from "../lib/roles";

describe("two roles over five views", () => {
  it("groups the five views", () => {
    expect(roleGroup("case_officer")).toBe("case_officer");
    expect(roleGroup("decision_maker")).toBe("case_officer");
    expect(roleGroup("platform_admin")).toBe("platform_admin");
    expect(roleGroup("process_owner")).toBe("platform_admin");
    expect(roleGroup("auditor")).toBe("platform_admin");
  });

  it("shows only two labels", () => {
    expect(roleLabel("decision_maker")).toBe("Case Officer");
    expect(roleLabel("auditor")).toBe("Platform Admin");
    expect(roleLabel(null)).toBe("");
  });

  it("acts inside the group only", () => {
    expect(actsAs("case_officer", "decision_maker")).toBe(true);
    expect(actsAs("platform_admin", "auditor")).toBe(true);
    expect(actsAs("case_officer", "platform_admin")).toBe(false);
    expect(actsAs(null, "case_officer")).toBe(false);
  });
});

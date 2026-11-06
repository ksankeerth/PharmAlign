import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import type { PackDocument, RouteDefinition } from "../api/types";
import { validatePackDocument } from "../domain/packValidation";
import {
  addStep,
  approvalReadiness,
  caseTypeRemovalBlock,
  checkUsage,
  fallbackBlock,
  laneOf,
  locatePath,
  moveStep,
  newCaseType,
  newCheck,
  newRoute,
  orderProblem,
  pathOf,
  removeCheck,
  removeStep,
  routeRemovalBlock,
  setLegalForce,
  setMethod,
  setStepKind,
  stepMoveBlock,
  stepRemovalBlock,
  waiveOptions,
} from "../pages/owner/packEdit";

const SEED = resolve(__dirname, "../../../demo/seed/pack_version.json");

function uae(): PackDocument {
  const rows = JSON.parse(readFileSync(SEED, "utf8")) as { doc: { id: string; document: PackDocument } }[];
  return structuredClone(rows.find((r) => r.doc.id === "pv-ae-4")!.doc.document);
}
const route = (doc: PackDocument, id = "standard"): RouteDefinition => doc.case_types[0].routes.find((r) => r.id === id)!;

describe("locked steps", () => {
  it("submit, door, decide and record can't be removed, with the reason", () => {
    const r = route(uae());
    for (const id of ["submit", "door", "decide", "record"]) expect(stepRemovalBlock(r, id)).toMatch(/locked step/);
    expect(() => removeStep(r, "door")).toThrow(/locked/);
    expect(stepRemovalBlock(r, "quality")).toBeNull();
  });

  it("an unlocked step can be removed and the pack stays valid", () => {
    const doc = uae();
    removeStep(route(doc), "questions");
    expect(route(doc).steps.some((s) => s.id === "questions")).toBe(false);
    expect(validatePackDocument(doc).valid).toBe(true);
  });

  it("a locked step keeps its kind", () => {
    const r = route(uae());
    expect(() => setStepKind(r.steps.find((s) => s.id === "door")!, "work")).toThrow();
    const work = r.steps.find((s) => s.id === "quality")!;
    expect(() => setStepKind(work, "decide")).toThrow();
    setStepKind(work, "question");
    expect(work.agent_autonomy).toBe("suggest");
  });
});

describe("ordering", () => {
  it("door stays before decide, decide before record", () => {
    expect(orderProblem(["door", "decide", "record"])).toBeNull();
    expect(orderProblem(["decide", "door", "record"])).toMatch(/door check must come before/i);
    expect(orderProblem(["door", "record", "decide"])).toMatch(/after a signed decision/i);
  });

  it("blocks a move that breaks the order, and says why", () => {
    const r = route(uae());
    expect(stepMoveBlock(r, "decide", 1)).toMatch(/after a signed decision/);
    expect(() => moveStep(r, "decide", 1)).toThrow();
    expect(stepMoveBlock(r, "submit", -1)).toMatch(/first step/);
    expect(stepMoveBlock(r, "record", 1)).toMatch(/last step/);
  });

  it("allows moving assessment work and keeps ids unique", () => {
    const r = route(uae());
    moveStep(r, "labelling", -1);
    const ids = r.steps.map((s) => s.id);
    expect(ids.indexOf("labelling")).toBeLessThan(ids.indexOf("quality"));
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("a new step lands before the decision", () => {
    const doc = uae();
    const r = route(doc);
    const s = addStep(r, "Inspection review");
    const ids = r.steps.map((x) => x.id);
    expect(ids.indexOf(s.id)).toBeLessThan(ids.indexOf("decide"));
    expect(addStep(r, "Inspection review").id).not.toBe(s.id);
    expect(validatePackDocument(doc).valid).toBe(true);
    expect(() => addStep(r, "Second decision", "decide")).toThrow();
  });
});

describe("lanes", () => {
  it("places each step by who acts", () => {
    const r = route(uae());
    const lane = (id: string) => laneOf(r.steps.find((s) => s.id === id)!);
    expect(lane("submit")).toBe("company");
    expect(lane("door")).toBe("staff");
    expect(lane("quality")).toBe("staff");
    expect(lane("decide")).toBe("signoff");
    expect(lane("record")).toBe("record");
  });
});

describe("routes and case types", () => {
  it("a new route has the four locked steps and validates", () => {
    const doc = uae();
    const ct = doc.case_types[0];
    ct.routes.push(newRoute(ct, "Priority route"));
    expect(validatePackDocument(doc).valid).toBe(true);
    expect(ct.routes.at(-1)!.steps.map((s) => s.kind)).toEqual(["submit", "door", "decide", "record"]);
  });

  it("a case type has at most one fallback route", () => {
    const doc = uae();
    const ct = doc.case_types[0];
    expect(fallbackBlock(ct, "abridged")).toMatch(/already the fallback/);
    ct.routes.push(newRoute(ct, "Another"));
    expect(ct.routes.at(-1)!.reliance_rule).not.toBeNull();
    expect(validatePackDocument(doc).valid).toBe(true);
  });

  it("the fallback route and the last route can't be removed", () => {
    const doc = uae();
    expect(routeRemovalBlock(doc.case_types[0], "standard")).toMatch(/fallback/);
    expect(routeRemovalBlock(doc.case_types[0], "abridged")).toBeNull();
    expect(routeRemovalBlock(doc.case_types[1], "rereview")).toMatch(/at least one route/);
    expect(caseTypeRemovalBlock(doc, "new_registration")).toBeNull();
    const single = { ...doc, case_types: [doc.case_types[0]] };
    expect(caseTypeRemovalBlock(single, "new_registration")).toMatch(/at least one case type/);
  });

  it("a new case type is valid", () => {
    const doc = uae();
    doc.case_types.push(newCaseType(doc, "Variation"));
    expect(validatePackDocument(doc).valid).toBe(true);
  });
});

describe("checks", () => {
  it("binding law can't be waived by a case officer", () => {
    const opts = waiveOptions("binding");
    expect(opts.find((o) => o.value === "case_officer")?.disabled).toBe(true);
    expect(waiveOptions("guidance").every((o) => !o.disabled)).toBe(true);
    const doc = uae();
    const c = doc.checks.find((x) => x.id === "LBL-02")!;
    setLegalForce(c, "binding");
    expect(c.waive_by).toBe("decision_maker");
    c.waive_by = "case_officer";
    expect(validatePackDocument(doc).issues.some((i) => i.rule === "binding_waiver")).toBe(true);
  });

  it("a method change keeps the check consistent", () => {
    const doc = uae();
    const c = doc.checks.find((x) => x.id === "DOOR-01")!;
    expect(c.on_fail).toBe("draft_return_notice");
    setMethod(c, "agent");
    expect(c.on_fail).toBe("hold_for_officer");
    expect(c.profile).toBe("quality");
  });

  it("a new check starts as local practice with a warning, and a used check can't be removed", () => {
    const doc = uae();
    const c = newCheck(doc, "Is the carton legible?");
    doc.checks.push(c);
    const issues = validatePackDocument(doc).issues.filter((i) => i.path === `checks[${c.id}]`);
    expect(issues.map((i) => i.rule)).toContain("local_practice");
    expect(issues.some((i) => i.severity === "error")).toBe(false);
    expect(checkUsage(doc, "QUAL-07").length).toBeGreaterThan(0);
    expect(() => removeCheck(doc, "QUAL-07")).toThrow(/Used by/);
    removeCheck(doc, c.id);
    expect(doc.checks.some((x) => x.id === c.id)).toBe(false);
  });
});

describe("paths", () => {
  it("maps a validation path to a place in the editor", () => {
    expect(locatePath("checks[QUAL-07]")).toEqual({ tab: "checks", checkId: "QUAL-07" });
    expect(locatePath("case_types[new_registration].routes[abridged].steps[door]")).toEqual({
      tab: "workflow",
      caseTypeId: "new_registration",
      routeId: "abridged",
      stepId: "door",
      reliance: undefined,
    });
    expect(locatePath("case_types[a].routes[b].reliance_rule")).toMatchObject({ routeId: "b", reliance: true });
    expect(locatePath("case_types[a]")).toMatchObject({ tab: "workflow", caseTypeId: "a" });
    expect(locatePath("final_escalation_holder")).toEqual({ tab: "country", field: "final_escalation_holder" });
    expect(locatePath("notification_rules[0]").tab).toBe("notifications");
  });

  it("round-trips", () => {
    for (const p of ["checks[X]", "case_types[a].routes[b].steps[c]", "case_types[a].routes[b].reliance_rule", "case_types[a].routes[b]"]) expect(pathOf(locatePath(p))).toBe(p);
  });
});

describe("approval readiness", () => {
  it("the author is refused, a second person is not", () => {
    const sam = approvalReadiness({ state: "simulated", created_by: "p-sam" }, "p-sam", 0);
    expect(sam.find((g) => g.guard === "not_own_change")?.passed).toBe(false);
    const priya = approvalReadiness({ state: "simulated", created_by: "p-sam" }, "p-priya", 0);
    expect(priya.every((g) => g.passed)).toBe(true);
    const draft = approvalReadiness({ state: "draft", created_by: "p-sam" }, "p-priya", 2);
    expect(draft.filter((g) => !g.passed).map((g) => g.guard)).toEqual(["pack_simulated", "pack_valid"]);
  });
});

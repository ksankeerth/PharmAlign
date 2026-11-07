// Pure editing rules for a country pack: which steps are locked, which moves keep the
// order the server demands, which autonomy a step kind allows, and how a validation
// path maps to a place in the editor. No React here, so every rule has a test.
import type { AgentAutonomy, CaseTypeDefinition, CheckDefinition, PackDocument, RelianceRule, RouteDefinition, StepKind, WorkflowStep } from "../../api/types";

/** Steps the platform requires on every route. They can be renamed, never removed. */
export const LOCKED_KINDS: readonly StepKind[] = ["submit", "door", "decide", "record"];

export const KIND_LABEL: Record<StepKind, string> = {
  submit: "Company submits",
  door: "Door check",
  work: "Assessment work",
  wait: "Wait for the company",
  question: "Question to the company",
  decide: "Decision",
  record: "Government record",
};

export type Lane = "company" | "staff" | "signoff" | "record";
export const LANES: { id: Lane; label: string; hint: string }[] = [
  { id: "company", label: "Company", hint: "The applicant acts" },
  { id: "staff", label: "Authority staff", hint: "Officers and assessors" },
  { id: "signoff", label: "Sign-off", hint: "A named person signs" },
  { id: "record", label: "Government record", hint: "The register is updated" },
];

export function isLocked(step: Pick<WorkflowStep, "locked">): boolean {
  return step.locked === true;
}

/** The swimlane a step sits in: who acts. */
export function laneOf(step: Pick<WorkflowStep, "kind" | "role">): Lane {
  if (step.kind === "record") return "record";
  if (step.kind === "decide" || step.role === "decision_maker" || step.role === "committee_secretary") return "signoff";
  if (step.kind === "submit" || step.kind === "wait" || step.role === "applicant_contact") return "company";
  return "staff";
}

/** Autonomy a step kind allows. A decision is always signed by a person; a question to a company is only suggested. */
export function autonomyOptions(kind: StepKind): AgentAutonomy[] {
  if (kind === "decide") return ["person_signs"];
  if (kind === "question") return ["off", "suggest"];
  if (kind === "submit" || kind === "wait") return ["off"];
  return ["off", "suggest", "do_and_report"];
}

export const AUTONOMY_LABEL: Record<AgentAutonomy, string> = {
  off: "Off",
  suggest: "Suggest",
  do_and_report: "Do and report",
  person_signs: "Person signs",
};

export const AUTONOMY_HINT: Record<AgentAutonomy, string> = {
  off: "No agent involvement.",
  suggest: "The agent drafts; a person decides what to use.",
  do_and_report: "The agent does the work and reports what it did.",
  person_signs: "A named person signs. No agent can act in their place.",
};

/** Kinds a person may pick when adding or changing an unlocked step. */
export const FREE_KINDS: StepKind[] = ["work", "question", "wait"];

// ---------------------------------------------------------------- order

/** Why this order of step kinds is not allowed, or null. */
export function orderProblem(kinds: StepKind[]): string | null {
  const door = kinds.indexOf("door");
  const decide = kinds.indexOf("decide");
  const record = kinds.indexOf("record");
  if (door >= 0 && decide >= 0 && door > decide) return "The door check must come before the decision.";
  if (decide >= 0 && record >= 0 && decide > record) return "The record can only be updated after a signed decision.";
  return null;
}

export function stepRemovalBlock(route: RouteDefinition, stepId: string): string | null {
  const step = route.steps.find((s) => s.id === stepId);
  if (!step) return "That step is not on this route.";
  if (isLocked(step)) return `${KIND_LABEL[step.kind]} is a locked step. It can be renamed but not removed.`;
  if (route.steps.length <= 1) return "A route needs at least one step.";
  return null;
}

export function stepMoveBlock(route: RouteDefinition, stepId: string, dir: -1 | 1): string | null {
  const i = route.steps.findIndex((s) => s.id === stepId);
  if (i < 0) return "That step is not on this route.";
  const j = i + dir;
  if (j < 0) return "This is already the first step.";
  if (j >= route.steps.length) return "This is already the last step.";
  const kinds = route.steps.map((s) => s.kind);
  [kinds[i], kinds[j]] = [kinds[j], kinds[i]];
  return orderProblem(kinds);
}

export function moveStep(route: RouteDefinition, stepId: string, dir: -1 | 1): void {
  const block = stepMoveBlock(route, stepId, dir);
  if (block) throw new Error(block);
  const i = route.steps.findIndex((s) => s.id === stepId);
  const [s] = route.steps.splice(i, 1);
  route.steps.splice(i + dir, 0, s);
}

export function removeStep(route: RouteDefinition, stepId: string): void {
  const block = stepRemovalBlock(route, stepId);
  if (block) throw new Error(block);
  route.steps = route.steps.filter((s) => s.id !== stepId);
}

export function slugify(text: string, fallback = "item"): string {
  const s = text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 40);
  return /^[a-z]/.test(s) ? s : s ? `x_${s}` : fallback;
}

export function uniqueId(base: string, taken: Iterable<string>): string {
  const used = new Set(taken);
  if (!used.has(base)) return base;
  for (let n = 2; ; n++) if (!used.has(`${base}_${n}`)) return `${base}_${n}`;
}

export function newStep(route: RouteDefinition, name: string, kind: StepKind = "work"): WorkflowStep {
  return {
    id: uniqueId(slugify(name, "step"), route.steps.map((s) => s.id)),
    name,
    kind,
    role: kind === "question" ? "case_manager" : kind === "wait" ? "applicant_contact" : "assessor",
    discipline: null,
    deadline_days: null,
    day_type: "working",
    only_if: null,
    agent_autonomy: kind === "work" ? "suggest" : kind === "question" ? "suggest" : "off",
    check_ids: [],
    source: "template",
    legal_basis: null,
    locked: false,
  };
}

/** Adds a step before the decision, so review work never lands after signing. */
export function addStep(route: RouteDefinition, name: string, kind: StepKind = "work"): WorkflowStep {
  if (LOCKED_KINDS.includes(kind)) throw new Error("Door, decision, submit and record steps are fixed on every route.");
  const step = newStep(route, name, kind);
  const at = route.steps.findIndex((s) => s.kind === "decide");
  route.steps.splice(at >= 0 ? at : route.steps.length, 0, step);
  return step;
}

/** Applies a field change to a step, keeping autonomy valid for its kind. */
export function setStepKind(step: WorkflowStep, kind: StepKind): void {
  if (isLocked(step) || LOCKED_KINDS.includes(kind)) throw new Error("A locked step keeps its kind.");
  step.kind = kind;
  if (!autonomyOptions(kind).includes(step.agent_autonomy)) step.agent_autonomy = autonomyOptions(kind)[0];
}

// ---------------------------------------------------------------- routes and case types

export function newRoute(ct: CaseTypeDefinition, label: string): RouteDefinition {
  const id = uniqueId(slugify(label, "route"), ct.routes.map((r) => r.id));
  const mk = (sid: string, name: string, kind: StepKind, role: string, autonomy: AgentAutonomy, days: number | null = null): WorkflowStep => ({
    id: sid,
    name,
    kind,
    role,
    discipline: null,
    deadline_days: days,
    day_type: "working",
    only_if: null,
    agent_autonomy: autonomy,
    check_ids: [],
    source: "template",
    legal_basis: null,
    locked: LOCKED_KINDS.includes(kind),
  });
  return {
    id,
    label,
    enabled: true,
    status_note: "proposed",
    reliance_rule: ct.routes.some((r) => r.reliance_rule === null) ? defaultRelianceRule() : null,
    published_time: null,
    steps: [
      mk("submit", "Company submits", "submit", "applicant_contact", "off"),
      mk("door", "Door check", "door", "intake_officer", "do_and_report", 5),
      mk("decide", "Decision", "decide", "decision_maker", "person_signs", 5),
      mk("record", "Update the government record", "record", "case_manager", "do_and_report"),
    ],
  };
}

export function newCaseType(doc: PackDocument, label: string): CaseTypeDefinition {
  const id = uniqueId(slugify(label, "case_type"), doc.case_types.map((c) => c.id));
  const ct: CaseTypeDefinition = { id, label, routes: [] };
  const route = newRoute(ct, "Standard route");
  route.status_note = null;
  route.reliance_rule = null;
  ct.routes.push(route);
  return ct;
}

export function defaultRelianceRule(): RelianceRule {
  return { trusted_regulators: [], match: "any", max_age_months: 36, product_types: ["generic"], must_be_identical: true, source: null, local_practice: true };
}

export function routeRemovalBlock(ct: CaseTypeDefinition, routeId: string): string | null {
  if (ct.routes.length <= 1) return "A case type needs at least one route.";
  const r = ct.routes.find((x) => x.id === routeId);
  if (r && r.reliance_rule === null && ct.routes.filter((x) => x.reliance_rule === null).length === 1 && ct.routes.length > 1) {
    return "This is the fallback route. Make another route the fallback first, so a file that matches no rule still has a route.";
  }
  return null;
}

export function caseTypeRemovalBlock(doc: PackDocument, caseTypeId: string): string | null {
  return doc.case_types.length <= 1 || !doc.case_types.some((c) => c.id === caseTypeId) ? "A pack needs at least one case type." : null;
}

/** The fallback route is the one with no reliance rule. At most one per case type. */
export function fallbackBlock(ct: CaseTypeDefinition, routeId: string): string | null {
  const other = ct.routes.find((r) => r.id !== routeId && r.reliance_rule === null);
  return other ? `${other.label} is already the fallback route. A case type has at most one.` : null;
}

// ---------------------------------------------------------------- checks

/** Who may waive a check, given its legal force. Binding law can't be waived by a case officer. */
export function waiveOptions(force: CheckDefinition["legal_force"]): { value: CheckDefinition["waive_by"]; label: string; disabled: boolean; reason?: string }[] {
  const binding = force === "binding";
  return [
    { value: "case_officer", label: "Case officer", disabled: binding, reason: binding ? "Binding law can't be waived by a case officer." : undefined },
    { value: "decision_maker", label: "Decision maker", disabled: false },
    { value: "nobody", label: "Nobody", disabled: false },
  ];
}

export function setLegalForce(check: CheckDefinition, force: CheckDefinition["legal_force"]): void {
  check.legal_force = force;
  if (force === "binding" && check.waive_by === "case_officer") check.waive_by = "decision_maker";
}

export function setMethod(check: CheckDefinition, method: CheckDefinition["method"]): void {
  check.method = method;
  if (method !== "agent") check.profile = null;
  if (method === "agent" && !check.profile) check.profile = "quality";
  if (method !== "code" && check.on_fail === "draft_return_notice") check.on_fail = "hold_for_officer";
  if (method === "agent" || method === "person") check.rule = check.rule ?? null;
}

export function newCheck(doc: PackDocument, question: string): CheckDefinition {
  const taken = (doc.checks ?? []).map((c) => c.id);
  let n = taken.length + 1;
  let id = `CHK-${String(n).padStart(2, "0")}`;
  while (taken.includes(id)) id = `CHK-${String(++n).padStart(2, "0")}`;
  return {
    id,
    version: 1,
    question,
    source: null,
    local_practice: true,
    supporting_sources: [],
    legal_force: "guidance",
    case_types: [],
    product_types: [],
    routes: [],
    stage: "knowledge",
    reads: [],
    method: "person",
    profile: null,
    extract: [],
    rule: null,
    may_pass_alone: false,
    severity: "warning",
    on_fail: "hold_for_officer",
    waive_by: "case_officer",
  };
}

/** Steps (as `case type / route / step`) that use a check. */
export function checkUsage(doc: PackDocument, checkId: string): string[] {
  const out: string[] = [];
  for (const ct of doc.case_types) for (const r of ct.routes) for (const s of r.steps) if ((s.check_ids ?? []).includes(checkId)) out.push(`${ct.label} / ${r.label} / ${s.name}`);
  return out;
}

export function removeCheck(doc: PackDocument, checkId: string): void {
  const used = checkUsage(doc, checkId);
  if (used.length) throw new Error(`Used by ${used.length} step${used.length === 1 ? "" : "s"}. Detach it first.`);
  doc.checks = (doc.checks ?? []).filter((c) => c.id !== checkId);
}

// ---------------------------------------------------------------- where a validation path points

export type EditorTab = "workflow" | "checks" | "country" | "notifications" | "json";
export interface Location {
  tab: EditorTab;
  caseTypeId?: string;
  routeId?: string;
  stepId?: string;
  checkId?: string;
  /** The reliance rule of the route, not a step. */
  reliance?: boolean;
  /** A top-level field name, such as `final_escalation_holder`. */
  field?: string;
}

const COUNTRY_FIELDS = new Set(["day_counting", "holidays", "languages", "decision_body", "licence_validity_years", "hot_climate_gap", "source_precedence", "final_escalation_holder", "severity_options", "severity_floors", "watch_change_types", "recommendation_options", "spot_check_rate", "country_code"]);

/** Maps an issue path such as `case_types[x].routes[y].steps[z]` to a place in the editor. */
export function locatePath(path: string): Location {
  const check = /^checks\[([^\]]+)\]/.exec(path);
  if (check) return { tab: "checks", checkId: check[1] };
  const ct = /^case_types\[([^\]]+)\](?:\.routes\[([^\]]+)\](?:\.steps\[([^\]]+)\]|(\.reliance_rule))?)?/.exec(path);
  if (ct) return { tab: "workflow", caseTypeId: ct[1], routeId: ct[2], stepId: ct[3], reliance: Boolean(ct[4]) || undefined };
  if (path.startsWith("notification_rules")) return { tab: "notifications" };
  const top = path.split(/[.[]/)[0];
  if (COUNTRY_FIELDS.has(top)) return { tab: "country", field: top };
  return { tab: "json" };
}

export function pathOf(loc: Location): string {
  if (loc.checkId) return `checks[${loc.checkId}]`;
  if (loc.caseTypeId) {
    let p = `case_types[${loc.caseTypeId}]`;
    if (loc.routeId) p += `.routes[${loc.routeId}]`;
    if (loc.routeId && loc.stepId) p += `.steps[${loc.stepId}]`;
    else if (loc.routeId && loc.reliance) p += ".reliance_rule";
    return p;
  }
  return loc.field ?? "";
}

/** True when two documents differ in any saved field. */
export function isDirty(a: PackDocument, b: PackDocument): boolean {
  return JSON.stringify(a) !== JSON.stringify(b);
}

/** Pack approval readiness, shown before anyone presses Approve. Same rules the server enforces. */
export interface Readiness {
  guard: string;
  passed: boolean;
  detail: string;
}
export function approvalReadiness(pack: { state: string; created_by: string }, personId: string | null | undefined, errorCount: number): Readiness[] {
  const own = pack.created_by === personId;
  return [
    { guard: "not_own_change", passed: !own, detail: own ? "You wrote this change. A second Process Owner must approve it." : "Approved by someone other than the author." },
    {
      guard: "pack_simulated",
      passed: pack.state === "simulated",
      detail: pack.state === "simulated" ? "Simulated before approval." : pack.state === "draft" ? "Run a simulation before approval." : `The version is already ${pack.state}.`,
    },
    { guard: "pack_valid", passed: errorCount === 0, detail: errorCount === 0 ? "Validation passes." : `${errorCount} validation error${errorCount === 1 ? "" : "s"}.` },
  ];
}

// Validates a pack document with the same rules the contract's model enforces, plus
// warnings a Process Owner should see. Pure, so the editor can also call it locally.
import type { PackDocument, PackIssue, PackValidation, RequestBody } from "../api/types";

const NUMBERS = /\d|\b(months?|days?|temperature|humidity|mg|ml|%)\b/i;

export function validatePackDocument(doc: PackDocument, knownDocIds?: Set<string>): PackValidation {
  const issues: PackIssue[] = [];
  const err = (path: string, rule: string, message: string) => issues.push({ path, rule, severity: "error", message });
  const warn = (path: string, rule: string, message: string) => issues.push({ path, rule, severity: "warning", message });

  const checks = doc.checks ?? [];
  const ids = checks.map((c) => c.id);
  const dup = ids.filter((id, i) => ids.indexOf(id) !== i);
  for (const id of new Set(dup)) err(`checks[${id}]`, "unique_check_ids", `Check id ${id} is used more than once.`);

  for (const c of checks) {
    const p = `checks[${c.id}]`;
    if (!c.source && !c.local_practice) err(p, "source_required", `${c.id} needs a source, or must be marked local practice.`);
    if (c.method === "typed" && (!(c.extract ?? []).length || !c.rule)) err(p, "typed_needs_rule", `${c.id} is typed, so it needs values to extract and a rule.`);
    if (c.method === "code" && !c.rule) err(p, "code_needs_rule", `${c.id} is a code check, so it needs a rule.`);
    if (c.method === "agent" && (!c.source || !c.profile)) err(p, "agent_needs_source_and_profile", `${c.id} is an agent check, so it needs a source and a profile.`);
    if (c.legal_force === "binding" && c.waive_by === "case_officer") err(p, "binding_waiver", `${c.id} is binding law, so a case officer can't waive it.`);
    if (c.on_fail === "draft_return_notice" && c.method !== "code") err(p, "return_notice_code_only", `Only a code check can draft a return notice (${c.id}).`);
    if (c.method === "agent" && NUMBERS.test(c.question)) warn(p, "prefer_typed", `${c.id} asks about numbers or units. A typed check lets code compare them.`);
    if (c.severity === "blocking" && c.may_pass_alone && (c.method === "agent" || c.method === "typed"))
      warn(p, "blocking_passes_alone", `${c.id} is blocking and may pass without a person.`);
    if (c.source && knownDocIds && !knownDocIds.has(c.source.doc_id)) err(p, "unknown_source", `${c.id} cites ${c.source.doc_id}, which is not in the library.`);
    if (c.local_practice) warn(p, "local_practice", `${c.id} is local practice with no published source. It will be counted as a gap signal.`);
  }

  if (!doc.final_escalation_holder) err("final_escalation_holder", "escalation_holder_required", "Name a final escalation holder for urgent alerts.");

  for (const ct of doc.case_types) {
    const fallbacks = ct.routes.filter((r) => r.reliance_rule === null || r.reliance_rule === undefined);
    if (fallbacks.length > 1) err(`case_types[${ct.id}]`, "one_fallback_route", `${ct.label} has ${fallbacks.length} fallback routes; at most one is allowed.`);
    for (const r of ct.routes) {
      const p = `case_types[${ct.id}].routes[${r.id}]`;
      const kinds = r.steps.map((s) => s.kind);
      for (const needed of ["door", "decide", "record"] as const) if (!kinds.includes(needed)) err(p, "locked_steps", `${r.label} must have a ${needed} step.`);
      if (kinds.includes("door") && kinds.includes("decide") && kinds.indexOf("door") > kinds.indexOf("decide")) err(p, "door_before_decide", "A door check must come before the decision.");
      if (kinds.includes("decide") && kinds.includes("record") && kinds.indexOf("decide") > kinds.indexOf("record")) err(p, "decide_before_record", "The record can only update after a signed decision.");
      const stepIds = r.steps.map((s) => s.id);
      if (new Set(stepIds).size !== stepIds.length) err(p, "unique_step_ids", "Step ids must be unique within a route.");
      if (r.reliance_rule && !r.reliance_rule.source && !r.reliance_rule.local_practice) err(`${p}.reliance_rule`, "source_required", "A reliance rule needs a source, or must be marked local practice.");
      if (r.reliance_rule?.local_practice && r.status_note !== "proposed") warn(p, "label_proposed", `${r.label} has no published source. Label it proposed.`);
      for (const s of r.steps) {
        const sp = `${p}.steps[${s.id}]`;
        if (s.kind === "decide" && s.agent_autonomy !== "person_signs") err(sp, "decision_person_signs", `${s.name}: a decision step always ends with a person signing.`);
        if (s.kind === "question" && (s.agent_autonomy === "do_and_report" || s.agent_autonomy === "person_signs"))
          err(sp, "question_suggest_only", `${s.name}: an agent can only suggest what goes to a company.`);
        const missing = (s.check_ids ?? []).filter((id) => !ids.includes(id));
        if (missing.length) err(sp, "unknown_checks", `${s.name} refers to unknown checks: ${missing.join(", ")}.`);
      }
    }
  }
  return { valid: !issues.some((i) => i.severity === "error"), issues };
}

type RawPackDocument = RequestBody<"put_api_v1_pack_versions_version_id">["document"];

/** Fills the contract's defaults, so a saved draft has every field present. */
export function normalizePackDocument(raw: RawPackDocument): PackDocument {
  const doc = structuredClone(raw);
  return {
    ...doc,
    holidays: doc.holidays ?? [],
    checks: (doc.checks ?? []).map((c) => ({ ...c, case_types: c.case_types ?? [], product_types: c.product_types ?? [], routes: c.routes ?? [], reads: c.reads ?? [], extract: (c.extract ?? []).map((f) => ({ ...f, look_in: f.look_in ?? [] })) })),
    case_types: doc.case_types.map((ct) => ({
      ...ct,
      routes: ct.routes.map((r) => ({
        ...r,
        reliance_rule: r.reliance_rule ? { ...r.reliance_rule } : null,
        steps: r.steps.map((s) => ({ ...s, check_ids: s.check_ids ?? [] })),
      })),
    })),
    notification_rules: (doc.notification_rules ?? []).map((n) => ({ ...n, channels: n.channels ?? ["in_app"], languages: n.languages ?? [], escalate_to: n.escalate_to ?? [] })),
    severity_floors: doc.severity_floors ?? [],
    watch_change_types: doc.watch_change_types ?? [],
    recommendation_options: doc.recommendation_options ?? [],
  } as PackDocument;
}

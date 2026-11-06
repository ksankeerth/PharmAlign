// Case types, then routes, then steps. The swimlane shows who acts at each step;
// the panel below edits the chosen step. Locked steps and ordering rules are enforced
// here and the reason is always shown beside the blocked action.
import { useEffect, useMemo, useState } from "react";
import type { AgentAutonomy, RouteDefinition, StepKind, WorkflowStep } from "../../../api/types";
import { Button, Callout, Checkbox, Field, Panel, Pill, Select, TextInput } from "../../../components";
import { ROLE_LABEL } from "../../../lib/labels";
import {
  addStep,
  AUTONOMY_HINT,
  AUTONOMY_LABEL,
  autonomyOptions,
  caseTypeRemovalBlock,
  defaultRelianceRule,
  fallbackBlock,
  FREE_KINDS,
  isLocked,
  KIND_LABEL,
  LANES,
  laneOf,
  moveStep,
  newCaseType,
  newRoute,
  removeStep,
  routeRemovalBlock,
  setStepKind,
  stepMoveBlock,
  stepRemovalBlock,
} from "../packEdit";
import { ChipsEditor } from "./ChipsEditor";
import type { EditorProps } from "./editorTypes";
import { NumF, SelectF, TextF, Why } from "./fields";
import { Swimlane } from "./Swimlane";

const ROLE_OPTIONS = Object.entries(ROLE_LABEL).map(([value, label]) => ({ value, label }));
const DISCIPLINES = ["quality", "labelling", "safety", "clinical", "inspection"];

export function WorkflowTab(props: EditorProps) {
  const { doc, update, readOnly, focus, issues } = props;
  const [ctId, setCtId] = useState(doc.case_types[0]?.id ?? "");
  const [routeId, setRouteId] = useState(doc.case_types[0]?.routes[0]?.id ?? "");
  const [stepId, setStepId] = useState<string | null>(null);
  const [revealReliance, setRevealReliance] = useState(false);

  useEffect(() => {
    if (!focus?.caseTypeId) return;
    const ct = doc.case_types.find((c) => c.id === focus.caseTypeId);
    if (!ct) return;
    setCtId(ct.id);
    const r = ct.routes.find((x) => x.id === focus.routeId) ?? ct.routes[0];
    setRouteId(r.id);
    setStepId(focus.stepId && r.steps.some((s) => s.id === focus.stepId) ? focus.stepId : null);
    setRevealReliance(Boolean(focus.reliance));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focus]);

  const ct = doc.case_types.find((c) => c.id === ctId) ?? doc.case_types[0];
  const route = ct?.routes.find((r) => r.id === routeId) ?? ct?.routes[0];
  const step = route?.steps.find((s) => s.id === stepId) ?? null;
  const [newCt, setNewCt] = useState("");
  const [newRt, setNewRt] = useState("");
  const [addName, setAddName] = useState("");
  const [addKind, setAddKind] = useState<StepKind>("work");
  const [addErr, setAddErr] = useState<string | null>(null);

  const editRoute = (fn: (r: RouteDefinition) => void) =>
    update((d) => {
      const r = d.case_types.find((c) => c.id === ct.id)?.routes.find((x) => x.id === route.id);
      if (r) fn(r);
    });
  const editStep = (fn: (s: WorkflowStep) => void) =>
    editRoute((r) => {
      const s = r.steps.find((x) => x.id === stepId);
      if (s) fn(s);
    });

  if (!ct || !route) return <Callout tone="held" title="This pack has no case types">Add a case type to start.</Callout>;

  const routeIssues = issues.filter((i) => i.path.startsWith(`case_types[${ct.id}].routes[${route.id}]`) || i.path === `case_types[${ct.id}]`);
  const rmRoute = routeRemovalBlock(ct, route.id);
  const rmCt = caseTypeRemovalBlock(doc, ct.id);

  return (
    <div className="ow-stack">
      <Panel title="Case types and routes">
        <div className="ow-pickers">
          <div>
            <div className="ow-seg-label" id="ct-label">Case type</div>
            <div className="ow-seg" role="group" aria-labelledby="ct-label">
              {doc.case_types.map((c) => (
                <button key={c.id} type="button" aria-pressed={c.id === ct.id} onClick={() => { setCtId(c.id); setRouteId(c.routes[0].id); setStepId(null); }}>
                  {c.label}
                </button>
              ))}
            </div>
          </div>
          <div>
            <div className="ow-seg-label" id="rt-label">Route</div>
            <div className="ow-seg" role="group" aria-labelledby="rt-label">
              {ct.routes.map((r) => (
                <button key={r.id} type="button" aria-pressed={r.id === route.id} onClick={() => { setRouteId(r.id); setStepId(null); }}>
                  {r.label}
                  {r.status_note ? <Pill tone="look" size="sm">{r.status_note}</Pill> : null}
                  {!r.enabled ? <Pill tone="neutral" size="sm">Off</Pill> : null}
                </button>
              ))}
            </div>
          </div>
        </div>
        {readOnly ? null : (
          <div className="row" style={{ marginTop: 12 }}>
            <TextInput aria-label="New route name" placeholder="New route name" value={newRt} onChange={(e) => setNewRt(e.target.value)} style={{ maxWidth: 200 }} />
            <Button size="sm" icon="plus" disabled={!newRt.trim()} onClick={() => { const r = newRoute(ct, newRt.trim()); update((d) => d.case_types.find((c) => c.id === ct.id)?.routes.push(r)); setRouteId(r.id); setNewRt(""); setStepId(null); }}>
              Add route
            </Button>
            <TextInput aria-label="New case type name" placeholder="New case type name" value={newCt} onChange={(e) => setNewCt(e.target.value)} style={{ maxWidth: 200 }} />
            <Button size="sm" icon="plus" disabled={!newCt.trim()} onClick={() => { const c = newCaseType(doc, newCt.trim()); update((d) => d.case_types.push(c)); setCtId(c.id); setRouteId(c.routes[0].id); setNewCt(""); setStepId(null); }}>
              Add case type
            </Button>
          </div>
        )}
      </Panel>

      <Panel
        title={`Workflow: ${route.label}`}
        actions={
          routeIssues.length ? <Pill tone={routeIssues.some((i) => i.severity === "error") ? "held" : "look"} icon="alert-triangle">{routeIssues.length} to look at</Pill> : <Pill tone="pass" icon="check">No issues</Pill>
        }
      >
        <div className="ow-stack">
          <Swimlane route={route} selectedId={stepId} onSelect={setStepId} />
          <ol className="ow-order" aria-label="Steps in order">
            {route.steps.map((s, i) => (
              <li key={s.id} aria-current={s.id === stepId}>
                <span className="muted tabular" style={{ width: 20 }}>{i + 1}</span>
                <button type="button" className="btn btn-ghost btn-sm ow-grow" style={{ justifyContent: "flex-start" }} onClick={() => setStepId(s.id)}>
                  {s.name}
                </button>
                <span className="muted small">{LANES.find((l) => l.id === laneOf(s))?.label}</span>
                {isLocked(s) ? <Pill tone="neutral" size="sm" icon="lock">Locked</Pill> : null}
              </li>
            ))}
          </ol>
          {readOnly ? null : (
            <div>
              <div className="row">
                <TextInput aria-label="New step name" placeholder="New step name" value={addName} onChange={(e) => { setAddName(e.target.value); setAddErr(null); }} style={{ maxWidth: 240 }} />
                <Select aria-label="New step kind" value={addKind} onChange={(e) => setAddKind(e.target.value as StepKind)} options={FREE_KINDS.map((k) => ({ value: k, label: KIND_LABEL[k] }))} style={{ maxWidth: 220 }} />
                <Button
                  size="sm"
                  icon="plus"
                  disabled={!addName.trim()}
                  onClick={() => {
                    try {
                      let created: WorkflowStep | null = null;
                      editRoute((r) => { created = addStep(r, addName.trim(), addKind); });
                      setAddName("");
                      if (created) setStepId((created as WorkflowStep).id);
                    } catch (e) {
                      setAddErr((e as Error).message);
                    }
                  }}
                >
                  Add step
                </Button>
              </div>
              <p className="muted xsmall" style={{ margin: "4px 0 0" }}>A new step is placed before the decision. Door, decision and record steps are fixed on every route.</p>
              {addErr ? <Why>{addErr}</Why> : null}
            </div>
          )}
        </div>
      </Panel>

      {step ? (
        <StepPanel {...props} route={route} step={step} editStep={editStep} editRoute={editRoute} onRemoved={() => setStepId(null)} />
      ) : (
        <Callout tone="neutral" title="Choose a step">Select a step in the diagram to change its name, role, deadline, condition, agent autonomy and checks.</Callout>
      )}

      <RoutePanel {...props} ctLabel={ct.label} route={route} editRoute={editRoute} reveal={revealReliance} rmRoute={rmRoute} rmCt={rmCt} onRemoveRoute={() => { update((d) => { const c = d.case_types.find((x) => x.id === ct.id); if (c) c.routes = c.routes.filter((r) => r.id !== route.id); }); setRouteId(ct.routes.find((r) => r.id !== route.id)?.id ?? ""); setStepId(null); }} onRemoveCt={() => { update((d) => { d.case_types = d.case_types.filter((c) => c.id !== ct.id); }); const next = doc.case_types.find((c) => c.id !== ct.id); if (next) { setCtId(next.id); setRouteId(next.routes[0].id); } setStepId(null); }} ct={ct} />
    </div>
  );
}

function StepPanel({ doc, readOnly, route, step, editStep, editRoute, onRemoved, issues }: EditorProps & { route: RouteDefinition; step: WorkflowStep; editStep: (fn: (s: WorkflowStep) => void) => void; editRoute: (fn: (r: RouteDefinition) => void) => void; onRemoved: () => void }) {
  const locked = isLocked(step);
  const removeBlock = stepRemovalBlock(route, step.id);
  const earlier = stepMoveBlock(route, step.id, -1);
  const later = stepMoveBlock(route, step.id, 1);
  const options = autonomyOptions(step.kind);
  const stepIssues = issues.filter((i) => i.path.endsWith(`.steps[${step.id}]`) && i.path.includes(`routes[${route.id}]`));
  const roleOptions = ROLE_OPTIONS.some((o) => o.value === step.role) ? ROLE_OPTIONS : [...ROLE_OPTIONS, { value: step.role, label: step.role }];
  const related = (doc.notification_rules ?? []).filter((n) => n.audience.includes(step.role));
  const checks = doc.checks ?? [];
  const toggleCheck = (id: string) => editStep((s) => { const cur = s.check_ids ?? []; s.check_ids = cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]; });
  return (
    <Panel
      id="step-panel"
      title={`Step: ${step.name}`}
      actions={
        <>
          {locked ? <Pill tone="neutral" icon="lock">Locked: {KIND_LABEL[step.kind]}</Pill> : <Pill tone="neutral">{KIND_LABEL[step.kind]}</Pill>}
          <Pill tone="neutral">{step.source === "published" ? "Published source" : "Template"}</Pill>
        </>
      }
    >
      {stepIssues.map((i) => (
        <div key={i.rule + i.path} style={{ marginBottom: 8 }}>
          <Callout tone={i.severity === "error" ? "held" : "look"} title={i.rule.replaceAll("_", " ")}>{i.message}</Callout>
        </div>
      ))}
      <div className="ow-fields">
        <TextF label="Name" value={step.name} disabled={readOnly} onChange={(v) => editStep((s) => { s.name = v; })} hint={locked ? "A locked step can be renamed." : undefined} required />
        <SelectF label="Role" value={step.role} disabled={readOnly} options={roleOptions} onChange={(v) => editStep((s) => { s.role = v; })} />
        <Field label="Discipline" hint="Optional. Sign-off type or assessment discipline.">
          {(id, d) => (
            <TextInput id={id} aria-describedby={d} list="ow-disciplines" disabled={readOnly} value={step.discipline ?? ""} onChange={(e) => editStep((s) => { s.discipline = e.target.value.trim() ? e.target.value.toLowerCase().replace(/[^a-z0-9_-]+/g, "_") : null; })} />
          )}
        </Field>
        <datalist id="ow-disciplines">{DISCIPLINES.map((d) => <option key={d} value={d} />)}</datalist>
        <SelectF
          label="Kind"
          value={step.kind}
          disabled={readOnly || locked}
          options={locked ? [{ value: step.kind, label: KIND_LABEL[step.kind] }] : FREE_KINDS.map((k) => ({ value: k, label: KIND_LABEL[k] }))}
          onChange={(v) => editStep((s) => setStepKind(s, v as StepKind))}
          hint={locked ? "A locked step keeps its kind." : undefined}
        />
        <NumF label="Deadline (days)" value={step.deadline_days} disabled={readOnly} onChange={(v) => editStep((s) => { s.deadline_days = v; })} hint="Leave empty for no deadline." />
        <SelectF label="Day type" value={step.day_type} disabled={readOnly} options={[{ value: "working", label: "Working days (country calendar)" }, { value: "calendar", label: "Calendar days" }]} onChange={(v) => editStep((s) => { s.day_type = v; })} />
        <TextF label="Condition" wide value={step.only_if} disabled={readOnly} placeholder="Always applies" hint="Optional. For example: only for oral liquids." onChange={(v) => editStep((s) => { s.only_if = v.trim() ? v : null; })} />
      </div>

      <div className="ow-section">Agent autonomy</div>
      <div className="ow-autonomy" role="radiogroup" aria-label="Agent autonomy">
        {(["off", "suggest", "do_and_report", "person_signs"] as AgentAutonomy[]).map((a) => {
          const allowed = options.includes(a);
          return (
            <label className="ow-radio" key={a}>
              <input type="radio" name={`autonomy-${step.id}`} value={a} checked={step.agent_autonomy === a} disabled={readOnly || !allowed} onChange={() => editStep((s) => { s.agent_autonomy = a; })} />
              <span>
                {AUTONOMY_LABEL[a]}
                <small>{AUTONOMY_HINT[a]}</small>
              </span>
            </label>
          );
        })}
      </div>
      {step.kind === "decide" ? <Why>A decision always ends with a person signing, so autonomy is fixed.</Why> : null}
      {step.kind === "question" ? <Why>An agent can only suggest what goes to a company. It never sends or decides.</Why> : null}
      {(step.kind === "submit" || step.kind === "wait") && !readOnly ? <Why>The company acts here, so an agent has nothing to do.</Why> : null}

      <div className="ow-section">Checks attached ({(step.check_ids ?? []).length})</div>
      {checks.length === 0 ? (
        <p className="muted small">This pack has no checks yet. Define them on the Checks tab.</p>
      ) : (
        <div className="ow-checklist" role="group" aria-label="Checks attached to this step">
          {checks.map((c) => (
            <Checkbox key={c.id} checked={(step.check_ids ?? []).includes(c.id)} disabled={readOnly} onChange={() => toggleCheck(c.id)} label={<><code>{c.id}</code> {c.question}</>} />
          ))}
        </div>
      )}

      <div className="ow-section">Notifications</div>
      {related.length ? (
        <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
          {related.map((n) => (
            <li key={n.event}>
              <code>{n.event}</code> reaches the {ROLE_LABEL[step.role as keyof typeof ROLE_LABEL] ?? step.role}
              {n.remind_before_hours ? `, reminder ${n.remind_before_hours} h before` : ""}
              {n.ack_within_hours ? `, acknowledge within ${n.ack_within_hours} h` : ""}.
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted small">No notification rule names this role as audience. Rules are set for the whole pack on the Notifications tab.</p>
      )}

      {readOnly ? null : (
        <>
          <div className="ow-section">Order and removal</div>
          <div className="ow-actions">
            <Button size="sm" icon="chevron-left" disabled={Boolean(earlier)} onClick={() => editRoute((r) => moveStep(r, step.id, -1))} aria-describedby="why-earlier">
              Move earlier
            </Button>
            <Button size="sm" iconRight="chevron-right" disabled={Boolean(later)} onClick={() => editRoute((r) => moveStep(r, step.id, 1))} aria-describedby="why-later">
              Move later
            </Button>
            <Button size="sm" variant="danger" icon="x" disabled={Boolean(removeBlock)} onClick={() => { editRoute((r) => removeStep(r, step.id)); onRemoved(); }} aria-describedby="why-remove">
              Remove step
            </Button>
          </div>
          <div id="why-earlier">{earlier ? <Why>Can't move earlier: {earlier}</Why> : null}</div>
          <div id="why-later">{later ? <Why>Can't move later: {later}</Why> : null}</div>
          <div id="why-remove">{removeBlock ? <Why>Can't remove: {removeBlock}</Why> : null}</div>
        </>
      )}
    </Panel>
  );
}

function RoutePanel({ readOnly, ct, ctLabel, route, editRoute, reveal, rmRoute, rmCt, onRemoveRoute, onRemoveCt, docOptions, issues }: EditorProps & { ct: EditorProps["doc"]["case_types"][number]; ctLabel: string; route: RouteDefinition; editRoute: (fn: (r: RouteDefinition) => void) => void; reveal: boolean; rmRoute: string | null; rmCt: string | null; onRemoveRoute: () => void; onRemoveCt: () => void }) {
  const rule = route.reliance_rule;
  const fallbackWhy = fallbackBlock(ct, route.id);
  const ruleIssues = issues.filter((i) => i.path.startsWith(`case_types[${ct.id}].routes[${route.id}]`) && !i.path.includes(".steps["));
  const regulatorTotal = rule?.trusted_regulators.length ?? 0;
  const [open, setOpen] = useState(true);
  useEffect(() => { if (reveal) setOpen(true); }, [reveal]);
  const docList = useMemo(() => docOptions.map((d) => <option key={d.id} value={d.id}>{d.title}</option>), [docOptions]);
  return (
    <Panel
      id="route-panel"
      title={`Route: ${route.label}`}
      actions={
        <Button size="sm" variant="ghost" iconRight={open ? "chevron-up" : "chevron-down"} onClick={() => setOpen(!open)} aria-expanded={open}>
          {open ? "Hide" : "Show"}
        </Button>
      }
    >
      {open ? (
        <>
          {ruleIssues.map((i) => (
            <div key={i.rule + i.path} style={{ marginBottom: 8 }}>
              <Callout tone={i.severity === "error" ? "held" : "look"} title={i.rule.replaceAll("_", " ")}>{i.message}</Callout>
            </div>
          ))}
          <div className="ow-fields">
            <TextF label="Route name" value={route.label} disabled={readOnly} onChange={(v) => editRoute((r) => { r.label = v; })} required />
            <TextF label="Label" value={route.status_note} disabled={readOnly} placeholder="None" hint={'Use "proposed" when no published source describes the route.'} onChange={(v) => editRoute((r) => { r.status_note = v.trim() ? v : null; })} />
            <TextF label="Published time" wide value={route.published_time} disabled={readOnly} placeholder="None" onChange={(v) => editRoute((r) => { r.published_time = v.trim() ? v : null; })} />
            <div className="ow-wide">
              <Checkbox checked={route.enabled} disabled={readOnly} onChange={(e) => editRoute((r) => { r.enabled = e.target.checked; })} label="Route is on" hint="A route that is off is never offered to new cases." />
            </div>
          </div>

          <div className="ow-section" id="reliance">Reliance rule</div>
          <Checkbox
            checked={rule === null}
            disabled={readOnly || (rule !== null && Boolean(fallbackWhy))}
            onChange={(e) => editRoute((r) => { r.reliance_rule = e.target.checked ? null : defaultRelianceRule(); })}
            label="Fallback route (no reliance rule)"
            hint="Used when no other route's rule matches. A case type has at most one."
          />
          {rule !== null && fallbackWhy && !readOnly ? <Why>Can't make this the fallback: {fallbackWhy}</Why> : null}
          {rule ? (
            <div className="ow-fields" style={{ marginTop: 12 }}>
              <div className="ow-wide">
                <Field label="Trusted regulators" hint="Regulators whose approval the company may rely on.">
                  {() => <ChipsEditor label="Trusted regulators" values={rule.trusted_regulators} readOnly={readOnly} onChange={(v) => editRoute((r) => { if (r.reliance_rule) r.reliance_rule.trusted_regulators = v; })} emptyText="Add at least one regulator" />}
                </Field>
              </div>
              <SelectF
                label="Approvals needed"
                value={rule.match}
                disabled={readOnly}
                options={[{ value: "any", label: "Any one of the regulators" }, { value: "all", label: `All ${regulatorTotal || ""} regulators`.replace("  ", " ") }]}
                onChange={(v) => editRoute((r) => { if (r.reliance_rule) r.reliance_rule.match = v; })}
              />
              <NumF label="Age limit (months)" value={rule.max_age_months} disabled={readOnly} hint="Empty means no limit." onChange={(v) => editRoute((r) => { if (r.reliance_rule) r.reliance_rule.max_age_months = v; })} />
              <div className="ow-wide">
                <Field label="Product types" hint="Slugs such as generic or generic_oral_liquid.">
                  {() => <ChipsEditor label="Product types" values={rule.product_types} readOnly={readOnly} onChange={(v) => editRoute((r) => { if (r.reliance_rule) r.reliance_rule.product_types = v; })} emptyText="Add at least one product type" />}
                </Field>
              </div>
              <div className="ow-wide">
                <Checkbox checked={rule.must_be_identical} disabled={readOnly} onChange={(e) => editRoute((r) => { if (r.reliance_rule) r.reliance_rule.must_be_identical = e.target.checked; })} label="The product must be identical to the reference approval" hint="Shelf life, strength and site are compared by code." />
              </div>
              <div className="ow-wide">
                <Checkbox checked={rule.local_practice} disabled={readOnly} onChange={(e) => editRoute((r) => { if (r.reliance_rule) r.reliance_rule.local_practice = e.target.checked; })} label="Local practice" hint="No published source. A gap signal is counted." />
              </div>
              <TextF label="Source document" value={rule.source?.doc_id} disabled={readOnly} list="ow-doc-options" placeholder="Library document id" onChange={(v) => editRoute((r) => { if (r.reliance_rule) r.reliance_rule.source = v.trim() ? { doc_id: v.trim(), clause: r.reliance_rule.source?.clause ?? null } : null; })} />
              <TextF label="Clause" value={rule.source?.clause} disabled={readOnly || !rule.source} placeholder="Section or article" onChange={(v) => editRoute((r) => { if (r.reliance_rule?.source) r.reliance_rule.source.clause = v.trim() ? v : null; })} />
              <datalist id="ow-doc-options">{docList}</datalist>
            </div>
          ) : (
            <p className="muted small" style={{ marginTop: 8 }}>This is the fallback route. Files that match no reliance rule take it.</p>
          )}

          {readOnly ? null : (
            <>
              <div className="ow-section">Remove</div>
              <div className="ow-actions">
                <Button size="sm" variant="danger" disabled={Boolean(rmRoute)} onClick={onRemoveRoute}>Remove route</Button>
                <Button size="sm" variant="danger" disabled={Boolean(rmCt)} onClick={onRemoveCt}>Remove case type {ctLabel}</Button>
              </div>
              {rmRoute ? <Why>Can't remove the route: {rmRoute}</Why> : null}
              {rmCt ? <Why>Can't remove the case type: {rmCt}</Why> : null}
            </>
          )}
        </>
      ) : null}
    </Panel>
  );
}

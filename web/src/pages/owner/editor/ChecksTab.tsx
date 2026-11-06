// Check definitions: the question, where it comes from, how it is answered, and what
// happens when it fails. Every check needs a source or a "local practice" label.
import { useEffect, useMemo, useState } from "react";
import type { CheckDefinition } from "../../../api/types";
import { Button, Callout, Checkbox, EmptyState, Field, Panel, Pill, SeverityBadge, TextInput } from "../../../components";
import { checkUsage, newCheck, removeCheck, setLegalForce, setMethod, waiveOptions } from "../packEdit";
import { ChipsEditor } from "./ChipsEditor";
import type { EditorProps } from "./editorTypes";
import { AreaF, NumF, SelectF, TextF, Why } from "./fields";

const METHOD_LABEL: Record<CheckDefinition["method"], string> = { code: "Code", typed: "Typed", agent: "Agent", person: "Person" };
const METHOD_HINT: Record<CheckDefinition["method"], string> = {
  code: "A rule that code evaluates. No model involved.",
  typed: "An agent extracts named values; code compares them with the rule.",
  agent: "An agent reads the file against the cited source and explains.",
  person: "A person answers. The case waits for them.",
};
const STAGES = ["safe_to_read", "complete", "route", "same_product", "knowledge"];

export function ChecksTab({ doc, update, readOnly, issues, focus, docOptions }: EditorProps) {
  const checks = doc.checks ?? [];
  const [id, setId] = useState<string | null>(focus?.checkId ?? checks[0]?.id ?? null);
  const [question, setQuestion] = useState("");
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { if (focus?.checkId) setId(focus.checkId); }, [focus]);
  const check = checks.find((c) => c.id === id) ?? null;
  const edit = (fn: (c: CheckDefinition) => void) =>
    update((d) => {
      const c = d.checks?.find((x) => x.id === id);
      if (c) fn(c);
    });
  const mine = issues.filter((i) => i.path === `checks[${id}]`);
  const used = useMemo(() => (check ? checkUsage(doc, check.id) : []), [doc, check]);

  return (
    <div className="ow-two-col">
      <Panel title={`Checks (${checks.length})`} padded={false}>
        <ul className="ow-list" aria-label="Checks">
          {checks.map((c) => {
            const has = issues.filter((i) => i.path === `checks[${c.id}]`);
            return (
              <li key={c.id}>
                <button type="button" aria-current={c.id === id} onClick={() => setId(c.id)}>
                  <span className="row">
                    <span className="ow-list-title">{c.id}</span>
                    <Pill size="sm" tone="neutral">{METHOD_LABEL[c.method]}</Pill>
                    {has.some((i) => i.severity === "error") ? <Pill size="sm" tone="held" icon="alert-octagon">Error</Pill> : has.length ? <Pill size="sm" tone="look" icon="alert-triangle">Note</Pill> : null}
                  </span>
                  <span className="ow-list-sub">{c.question}</span>
                </button>
              </li>
            );
          })}
        </ul>
        {readOnly ? null : (
          <div className="panel-foot">
            <div className="row">
              <TextInput aria-label="New check question" placeholder="Question for a new check" value={question} onChange={(e) => setQuestion(e.target.value)} style={{ flex: 1, minWidth: 120 }} />
              <Button size="sm" icon="plus" disabled={!question.trim()} onClick={() => { const c = newCheck(doc, question.trim()); update((d) => { d.checks = [...(d.checks ?? []), c]; }); setId(c.id); setQuestion(""); }}>
                Add check
              </Button>
            </div>
          </div>
        )}
      </Panel>

      {check ? (
        <Panel
          id="check-panel"
          title={`${check.id}`}
          actions={<><SeverityBadge severity={check.severity} /><Pill tone="neutral">{METHOD_LABEL[check.method]}</Pill>{check.local_practice ? <Pill tone="look">Local practice</Pill> : null}</>}
        >
          {mine.map((i) => (
            <div key={i.rule} style={{ marginBottom: 8 }}>
              <Callout tone={i.severity === "error" ? "held" : "look"} title={i.rule.replaceAll("_", " ")}>{i.message}</Callout>
            </div>
          ))}
          <div className="ow-fields">
            <AreaF label="Question" value={check.question} disabled={readOnly} rows={2} onChange={(v) => edit((c) => { c.question = v; })} />
            <NumF label="Version" min={1} value={check.version} disabled={readOnly} hint="Raise it when the meaning of the check changes." onChange={(v) => edit((c) => { c.version = Math.max(1, v ?? 1); })} />
            <TextF label="Stage" value={check.stage} list="ow-stages" disabled={readOnly} onChange={(v) => edit((c) => { c.stage = v; })} />
            <datalist id="ow-stages">{STAGES.map((s) => <option key={s} value={s} />)}</datalist>
          </div>

          <div className="ow-section">Where it comes from</div>
          <div className="ow-fields">
            <TextF label="Source document" value={check.source?.doc_id} list="ow-check-docs" disabled={readOnly} placeholder="Library document id" onChange={(v) => edit((c) => { c.source = v.trim() ? { doc_id: v.trim(), clause: c.source?.clause ?? null } : null; })} />
            <TextF label="Clause" value={check.source?.clause} disabled={readOnly || !check.source} onChange={(v) => edit((c) => { if (c.source) c.source.clause = v.trim() ? v : null; })} />
            <datalist id="ow-check-docs">{docOptions.map((d) => <option key={d.id} value={d.id}>{d.title}</option>)}</datalist>
            <div className="ow-wide">
              <Checkbox checked={check.local_practice} disabled={readOnly} onChange={(e) => edit((c) => { c.local_practice = e.target.checked; })} label="Local practice" hint="No published source. It is counted as a gap signal." />
            </div>
            <SelectF label="Legal force" value={check.legal_force} disabled={readOnly} options={[{ value: "binding", label: "Binding law" }, { value: "guidance", label: "Guidance" }, { value: "none", label: "None" }]} onChange={(v) => edit((c) => setLegalForce(c, v))} />
          </div>

          <div className="ow-section">Scope (empty means all)</div>
          <div className="ow-fields">
            <Field label="Case types">{() => <ChipsEditor label="Case types" values={check.case_types ?? []} readOnly={readOnly} emptyText="All" onChange={(v) => edit((c) => { c.case_types = v; })} />}</Field>
            <Field label="Routes">{() => <ChipsEditor label="Routes" values={check.routes ?? []} readOnly={readOnly} emptyText="All" onChange={(v) => edit((c) => { c.routes = v; })} />}</Field>
            <Field label="Product types">{() => <ChipsEditor label="Product types" values={check.product_types ?? []} readOnly={readOnly} emptyText="All" onChange={(v) => edit((c) => { c.product_types = v; })} />}</Field>
            <Field label="Reads (where to look first)">{() => <ChipsEditor label="Reads" values={check.reads ?? []} readOnly={readOnly} emptyText="Anywhere" onChange={(v) => edit((c) => { c.reads = v; })} />}</Field>
          </div>

          <div className="ow-section">How it is answered</div>
          <div className="ow-fields">
            <SelectF label="Method" value={check.method} disabled={readOnly} hint={METHOD_HINT[check.method]} options={(Object.keys(METHOD_LABEL) as CheckDefinition["method"][]).map((m) => ({ value: m, label: METHOD_LABEL[m] }))} onChange={(v) => edit((c) => setMethod(c, v))} />
            {check.method === "agent" ? (
              <SelectF label="Agent profile" value={check.profile ?? "quality"} disabled={readOnly} options={[{ value: "quality", label: "Quality" }, { value: "safety", label: "Safety" }, { value: "label_language", label: "Label and language" }]} onChange={(v) => edit((c) => { c.profile = v; })} />
            ) : null}
            {check.method === "code" || check.method === "typed" ? (
              <AreaF label="Rule" mono rows={3} value={check.rule} disabled={readOnly} hint="Evaluated by code, never by the model." onChange={(v) => edit((c) => { c.rule = v.trim() ? v : null; })} />
            ) : null}
          </div>

          {check.method === "typed" ? (
            <>
              <div className="ow-section">Values to extract ({(check.extract ?? []).length})</div>
              <div className="ow-stack">
                {(check.extract ?? []).map((f, i) => (
                  <div className="ow-fields ow-fields-3" key={i} style={{ paddingBottom: 12, borderBottom: "1px solid var(--line)" }}>
                    <TextF label="Key" value={f.key} disabled={readOnly} onChange={(v) => edit((c) => { c.extract![i].key = v; })} />
                    <SelectF label="Type" value={f.value_type} disabled={readOnly} options={[{ value: "number", label: "Number" }, { value: "text", label: "Text" }, { value: "date", label: "Date" }, { value: "boolean", label: "Yes or no" }]} onChange={(v) => edit((c) => { c.extract![i].value_type = v; })} />
                    <TextF label="Unit" value={f.unit} disabled={readOnly} onChange={(v) => edit((c) => { c.extract![i].unit = v.trim() ? v : null; })} />
                    <TextF label="Description" wide value={f.description} disabled={readOnly} onChange={(v) => edit((c) => { c.extract![i].description = v; })} />
                    <div className="ow-wide">
                      <Field label="Look in">{() => <ChipsEditor label={`Look in for ${f.key}`} values={f.look_in ?? []} readOnly={readOnly} onChange={(v) => edit((c) => { c.extract![i].look_in = v; })} />}</Field>
                    </div>
                    {readOnly ? null : (
                      <div className="ow-wide">
                        <Button size="sm" variant="ghost" icon="x" onClick={() => edit((c) => { c.extract = c.extract!.filter((_, k) => k !== i); })}>Remove value {f.key}</Button>
                      </div>
                    )}
                  </div>
                ))}
                {readOnly ? null : (
                  <div>
                    <Button size="sm" icon="plus" onClick={() => edit((c) => { c.extract = [...(c.extract ?? []), { key: `value_${(c.extract?.length ?? 0) + 1}`, description: "", value_type: "number", unit: null, look_in: [] }]; })}>Add value</Button>
                  </div>
                )}
              </div>
            </>
          ) : null}

          <div className="ow-section">Outcome</div>
          <div className="ow-fields">
            <SelectF label="Severity" value={check.severity} disabled={readOnly} options={[{ value: "blocking", label: "Blocking" }, { value: "warning", label: "Warning" }, { value: "info", label: "Info" }]} onChange={(v) => edit((c) => { c.severity = v; })} />
            <SelectF
              label="On fail"
              value={check.on_fail}
              disabled={readOnly}
              options={[
                { value: "hold_for_officer", label: "Hold for an officer" },
                { value: "look", label: "Ask a person to look" },
                { value: "draft_return_notice", label: "Draft a return notice (code checks only)", disabled: check.method !== "code" },
              ]}
              onChange={(v) => edit((c) => { c.on_fail = v; })}
            />
            <div className="ow-wide">
              <Checkbox checked={check.may_pass_alone} disabled={readOnly} onChange={(e) => edit((c) => { c.may_pass_alone = e.target.checked; })} label="May pass without a person" hint="Only sensible for code checks. A blocking agent check that passes alone is flagged." />
            </div>
            <div className="ow-wide">
              <Field label="Who may waive">
                {(fid) => (
                  <div className="ow-autonomy" role="radiogroup" aria-label="Who may waive" id={fid}>
                    {waiveOptions(check.legal_force).map((o) => (
                      <label className="ow-radio" key={o.value}>
                        <input type="radio" name="waive" checked={check.waive_by === o.value} disabled={readOnly || o.disabled} onChange={() => edit((c) => { c.waive_by = o.value; })} />
                        <span>{o.label}</span>
                      </label>
                    ))}
                  </div>
                )}
              </Field>
              {check.legal_force === "binding" ? <Why>Binding law can't be waived by a case officer. Choose a decision maker, or nobody.</Why> : null}
            </div>
          </div>

          <div className="ow-section">Used by</div>
          {used.length ? <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>{used.map((u) => <li key={u}>{u}</li>)}</ul> : <p className="muted small">No step uses this check yet. Attach it to a step on the Workflow tab.</p>}
          {readOnly ? null : (
            <div style={{ marginTop: 12 }}>
              <Button size="sm" variant="danger" disabled={used.length > 0} onClick={() => { try { update((d) => removeCheck(d, check.id)); setId(checks.find((c) => c.id !== check.id)?.id ?? null); setErr(null); } catch (e) { setErr((e as Error).message); } }}>
                Remove check
              </Button>
              {used.length ? <Why>Can't remove: used by {used.length} step{used.length === 1 ? "" : "s"}. Detach it from each step first.</Why> : null}
              {err ? <Why>{err}</Why> : null}
            </div>
          )}
        </Panel>
      ) : (
        <Panel><EmptyState title="No checks yet" icon="list-checks">Add a check with a question, then attach it to a step.</EmptyState></Panel>
      )}
    </div>
  );
}

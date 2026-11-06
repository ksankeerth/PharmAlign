// Country settings: how days are counted, who decides, which sources win, and the
// vocabularies the rest of the pack uses.
import { useEffect, useRef, useState } from "react";
import type { PackDocument } from "../../../api/types";
import { Button, Callout, Checkbox, Field, Panel, TextInput } from "../../../components";
import { formatDate } from "../../../lib/format";
import { slugify } from "../packEdit";
import { ChipsEditor } from "./ChipsEditor";
import type { EditorProps } from "./editorTypes";
import { NumF, SelectF, TextF } from "./fields";

const PRECEDENCE: { id: PackDocument["source_precedence"][number]; label: string; hint: string }[] = [
  { id: "authority_level", label: "Authority level", hint: "Binding law (level 1) beats guidance and context." },
  { id: "in_force", label: "In force", hint: "A source in force beats a superseded or draft one." },
  { id: "newer", label: "Newer", hint: "Of two equal sources, the newer wins." },
];

export function CountryTab({ doc, update, readOnly, issues, focus }: EditorProps) {
  const [holiday, setHoliday] = useState("");
  const top = issues.filter((i) => !i.path.startsWith("checks[") && !i.path.startsWith("case_types["));
  const escalation = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (focus?.field === "final_escalation_holder") escalation.current?.querySelector("input")?.focus();
  }, [focus]);
  const precedence = doc.source_precedence ?? [];
  const movePrec = (i: number, d: -1 | 1) => update((x) => { const a = x.source_precedence; const j = i + d; if (j < 0 || j >= a.length) return; [a[i], a[j]] = [a[j], a[i]]; });
  const togglePrec = (id: (typeof PRECEDENCE)[number]["id"]) => update((x) => { x.source_precedence = x.source_precedence.includes(id) ? x.source_precedence.filter((p) => p !== id) : [...x.source_precedence, id]; });
  return (
    <div className="ow-stack">
      {top.map((i) => (
        <Callout key={i.rule + i.path} tone={i.severity === "error" ? "held" : "look"} title={i.rule.replaceAll("_", " ")}>{i.message}</Callout>
      ))}
      <Panel title="Days, language and decision">
        <div className="ow-fields">
          <TextF label="Country code" value={doc.country_code} disabled onChange={() => undefined} hint="Set when the workspace was created." />
          <SelectF label="Day counting" value={doc.day_counting} disabled={readOnly} hint="Which days count as working days for deadlines." options={[{ value: "mon_fri", label: "Monday to Friday" }, { value: "sun_thu", label: "Sunday to Thursday" }, { value: "calendar", label: "Every day" }]} onChange={(v) => update((d) => { d.day_counting = v; })} />
          <div className="ow-wide">
            <Field label={`Holidays (${doc.holidays?.length ?? 0})`} hint="Days that never count as working days.">
              {() => (
                <div className="ow-chips" role="group" aria-label="Holidays">
                  {[...(doc.holidays ?? [])].sort().map((h) => (
                    <span className="ow-chip" key={h}>
                      {formatDate(h)}
                      {readOnly ? null : <button type="button" aria-label={`Remove ${formatDate(h)}`} onClick={() => update((d) => { d.holidays = (d.holidays ?? []).filter((x) => x !== h); })}>×</button>}
                    </span>
                  ))}
                  {readOnly ? null : (
                    <>
                      <TextInput type="date" aria-label="Holiday date" value={holiday} onChange={(e) => setHoliday(e.target.value)} style={{ width: 160 }} />
                      <Button size="sm" icon="plus" disabled={!holiday || (doc.holidays ?? []).includes(holiday)} onClick={() => { update((d) => { d.holidays = [...(d.holidays ?? []), holiday]; }); setHoliday(""); }}>Add holiday</Button>
                    </>
                  )}
                </div>
              )}
            </Field>
          </div>
          <div className="ow-wide">
            <Field label="Languages" hint="Language codes such as en or ar. Notifications and notices use these.">
              {() => <ChipsEditor label="Languages" values={doc.languages} readOnly={readOnly} onChange={(v) => update((d) => { d.languages = v; })} emptyText="At least one" />}
            </Field>
          </div>
          <TextF label="Decision body" value={doc.decision_body} disabled={readOnly} onChange={(v) => update((d) => { d.decision_body = v; })} required />
          <NumF label="Licence validity (years)" min={1} value={doc.licence_validity_years} disabled={readOnly} hint="Empty means a licence does not expire." onChange={(v) => update((d) => { d.licence_validity_years = v; })} />
          <SelectF
            label="Hot-climate gap"
            value={doc.hot_climate_gap}
            disabled={readOnly}
            hint="When stability data was not generated at the country's storage conditions."
            options={[{ value: "ask", label: "Ask the company" }, { value: "commit", label: "Accept a commitment" }, { value: "block", label: "Block the file" }, { value: "none", label: "Do not check" }]}
            onChange={(v) => update((d) => { d.hot_climate_gap = v; })}
          />
          <div ref={escalation}>
            <TextF label="Final escalation holder" value={doc.final_escalation_holder} disabled={readOnly} hint="Role that receives an urgent alert nobody acknowledged." onChange={(v) => update((d) => { d.final_escalation_holder = slugify(v, ""); })} required />
          </div>
          <NumF label="Spot-check rate (%)" min={0} max={100} step={1} value={Math.round((doc.spot_check_rate ?? 0) * 100)} disabled={readOnly} hint="Share of finished cases a second person samples." onChange={(v) => update((d) => { d.spot_check_rate = Math.min(1, Math.max(0, (v ?? 0) / 100)); })} />
        </div>
      </Panel>

      <Panel title="Source precedence">
        <p className="muted small" style={{ marginTop: 0 }}>When two sources disagree, they are compared in this order.</p>
        <ol className="ow-order">
          {precedence.map((p, i) => {
            const def = PRECEDENCE.find((x) => x.id === p)!;
            return (
              <li key={p}>
                <span className="muted tabular" style={{ width: 20 }}>{i + 1}</span>
                <span className="ow-grow"><strong>{def.label}</strong> <span className="muted small">{def.hint}</span></span>
                {readOnly ? null : (
                  <>
                    <Button size="sm" variant="ghost" icon="arrow-up" aria-label={`Move ${def.label} earlier`} disabled={i === 0} onClick={() => movePrec(i, -1)} />
                    <Button size="sm" variant="ghost" icon="arrow-down" aria-label={`Move ${def.label} later`} disabled={i === precedence.length - 1} onClick={() => movePrec(i, 1)} />
                  </>
                )}
              </li>
            );
          })}
        </ol>
        {readOnly ? null : (
          <div className="row" style={{ marginTop: 8 }}>
            {PRECEDENCE.filter((p) => !precedence.includes(p.id)).map((p) => (
              <Checkbox key={p.id} checked={false} onChange={() => togglePrec(p.id)} label={`Use ${p.label.toLowerCase()}`} />
            ))}
            {precedence.length > 1 ? precedence.map((p) => <Button key={p} size="sm" variant="ghost" onClick={() => togglePrec(p)}>{`Stop using ${PRECEDENCE.find((x) => x.id === p)!.label.toLowerCase()}`}</Button>) : null}
          </div>
        )}
      </Panel>

      <Panel title="Watching foreign changes">
        <div className="ow-fields">
          <div className="ow-wide">
            <Field label="Severity levels" hint="Used by notices and notifications, lowest first.">
              {() => <ChipsEditor label="Severity levels" values={doc.severity_options} readOnly={readOnly} onChange={(v) => update((d) => { d.severity_options = v; })} />}
            </Field>
          </div>
          <div className="ow-wide">
            <Field label="Kinds of foreign change to watch">
              {() => <ChipsEditor label="Kinds of foreign change" values={doc.watch_change_types ?? []} readOnly={readOnly} onChange={(v) => update((d) => { d.watch_change_types = v; })} />}
            </Field>
          </div>
          <div className="ow-wide">
            <Field label="Minimum severity by kind of change" hint="A suspension is never below urgent, however a notice is scored.">
              {() => (
                <div className="ow-stack">
                  {(doc.severity_floors ?? []).map((f, i) => (
                    <div className="row" key={i}>
                      <SelectF label="Change" value={f.change_type} disabled={readOnly} options={[...new Set([f.change_type, ...(doc.watch_change_types ?? [])])].map((v) => ({ value: v, label: v }))} onChange={(v) => update((d) => { d.severity_floors[i].change_type = v; })} />
                      <SelectF label="At least" value={f.floor} disabled={readOnly} options={[...new Set([f.floor, ...doc.severity_options])].map((v) => ({ value: v, label: v }))} onChange={(v) => update((d) => { d.severity_floors[i].floor = v; })} />
                      {readOnly ? null : <Button size="sm" variant="ghost" icon="x" aria-label={`Remove floor for ${f.change_type}`} onClick={() => update((d) => { d.severity_floors = d.severity_floors.filter((_, k) => k !== i); })} />}
                    </div>
                  ))}
                  {readOnly ? null : (
                    <div>
                      <Button size="sm" icon="plus" onClick={() => update((d) => { d.severity_floors = [...(d.severity_floors ?? []), { change_type: d.watch_change_types?.[0] ?? "other", floor: d.severity_options[0] ?? "info" }]; })}>Add floor</Button>
                    </div>
                  )}
                </div>
              )}
            </Field>
          </div>
          <div className="ow-wide">
            <Field label="Recommendation options" hint="What the case review may recommend.">
              {() => <ChipsEditor label="Recommendation options" values={doc.recommendation_options ?? []} readOnly={readOnly} onChange={(v) => update((d) => { d.recommendation_options = v; })} />}
            </Field>
          </div>
        </div>
      </Panel>
    </div>
  );
}

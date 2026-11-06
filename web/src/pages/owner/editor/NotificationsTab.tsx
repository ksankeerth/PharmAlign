// Notification rules for the whole pack: who hears about an event, how, and who it
// escalates to when nobody acknowledges.
import { useState } from "react";
import { Button, Checkbox, EmptyState, Field, Panel, TextInput } from "../../../components";
import { humanize } from "../../../lib/labels";
import { slugify } from "../packEdit";
import { ChipsEditor } from "./ChipsEditor";
import type { EditorProps } from "./editorTypes";
import { NumF } from "./fields";

const CHANNELS = ["in_app", "email", "sms"] as const;
const CHANNEL_LABEL = { in_app: "In the app", email: "Email", sms: "Text message" };

export function NotificationsTab({ doc, update, readOnly, issues }: EditorProps) {
  const rules = doc.notification_rules ?? [];
  const [event, setEvent] = useState("");
  const problems = issues.filter((i) => i.path.startsWith("notification_rules"));
  return (
    <div className="ow-stack">
      {rules.length === 0 ? (
        <Panel><EmptyState title="No notification rules" icon="bell">Without a rule, nobody is told when a case is assigned or ready to sign.</EmptyState></Panel>
      ) : null}
      {rules.map((r, i) => (
        <Panel
          key={`${r.event}-${i}`}
          title={humanize(r.event)}
          actions={readOnly ? null : <Button size="sm" variant="ghost" icon="x" onClick={() => update((d) => { d.notification_rules = d.notification_rules.filter((_, k) => k !== i); })}>Remove rule</Button>}
        >
          <div className="ow-fields">
            <div className="ow-wide">
              <Field label="Who is told (roles)">{() => <ChipsEditor label={`Audience for ${r.event}`} values={r.audience} readOnly={readOnly} onChange={(v) => update((d) => { d.notification_rules[i].audience = v; })} emptyText="Add a role" />}</Field>
            </div>
            <div className="ow-wide">
              <div className="field-label" style={{ marginBottom: 4 }}>Channels</div>
              <div className="row">
                {CHANNELS.map((c) => (
                  <Checkbox key={c} label={CHANNEL_LABEL[c]} checked={r.channels.includes(c)} disabled={readOnly || (r.channels.length === 1 && r.channels[0] === c)} onChange={(e) => update((d) => { const n = d.notification_rules[i]; n.channels = e.target.checked ? [...n.channels, c] : n.channels.filter((x) => x !== c); })} />
                ))}
              </div>
            </div>
            <div className="ow-wide">
              <Field label="Languages" hint="Empty means the country's languages.">{() => <ChipsEditor label={`Languages for ${r.event}`} values={r.languages} readOnly={readOnly} onChange={(v) => update((d) => { d.notification_rules[i].languages = v; })} />}</Field>
            </div>
            <NumF label="Remind before (hours)" value={r.remind_before_hours} disabled={readOnly} onChange={(v) => update((d) => { d.notification_rules[i].remind_before_hours = v; })} />
            <NumF label="Acknowledge within (hours)" value={r.ack_within_hours} disabled={readOnly} onChange={(v) => update((d) => { d.notification_rules[i].ack_within_hours = v; })} />
            <div className="ow-wide">
              <Field label="Escalate to" hint="Told in turn when nobody acknowledges in time.">{() => <ChipsEditor label={`Escalate ${r.event} to`} values={r.escalate_to} readOnly={readOnly} onChange={(v) => update((d) => { d.notification_rules[i].escalate_to = v; })} />}</Field>
            </div>
          </div>
        </Panel>
      ))}
      {problems.map((p) => <p key={p.path + p.rule} className="ow-inline-err">{p.message}</p>)}
      {readOnly ? null : (
        <div className="row">
          <TextInput aria-label="New notification event" placeholder="Event, such as case_assigned" value={event} onChange={(e) => setEvent(e.target.value)} style={{ maxWidth: 260 }} />
          <Button size="sm" icon="plus" disabled={!slugify(event, "")} onClick={() => { update((d) => { d.notification_rules = [...(d.notification_rules ?? []), { event: slugify(event, "event"), audience: [], channels: ["in_app"], languages: [], remind_before_hours: null, ack_within_hours: null, escalate_to: [] }]; }); setEvent(""); }}>Add rule</Button>
        </div>
      )}
    </div>
  );
}

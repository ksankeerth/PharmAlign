// Add a person: name, email, view and title, optionally filled from a JSON file. Saving hands the new
// person back so the page can open the role-grant dialog for them.
import { useState } from "react";
import { api } from "../../api";
import type { Person, RoleType, View } from "../../api/types";
import { ConfirmModal, Field, FillFromJson, Select, TextInput } from "../../components";
import { pickFields, type FieldMap } from "../../lib/fillJson";
import { VIEW_LABEL } from "../../lib/labels";

/** The role a view's person usually needs first, as the demo people hold it. A Case Officer is a Case Manager. */
export const VIEW_ROLE: Record<View, RoleType> = {
  case_officer: "case_manager",
  decision_maker: "decision_maker",
  process_owner: "process_owner",
  platform_admin: "platform_admin",
  auditor: "auditor",
};

const VIEWS = Object.keys(VIEW_LABEL) as View[];
const viewOptions = VIEWS.map((v) => ({ value: v, label: VIEW_LABEL[v] }));
const FIELDS: FieldMap = { display_name: "string", email: "string", view: "string", title: "string" };

export function AddPersonDialog({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (person: Person, view: View) => void }) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [view, setView] = useState<View>("case_officer");
  const [title, setTitle] = useState("");

  const fill = (item: unknown) => {
    const f = pickFields(item, FIELDS);
    if (typeof f.display_name === "string") setName(f.display_name);
    if (typeof f.email === "string") setEmail(f.email);
    if (typeof f.view === "string" && (VIEWS as string[]).includes(f.view)) setView(f.view as View);
    if (typeof f.title === "string") setTitle(f.title);
  };

  return (
    <ConfirmModal
      open={open}
      onClose={onClose}
      title="Add a person"
      confirmLabel="Add person"
      summary={
        <div className="stack-sm">
          <div>
            <FillFromJson pick={{ key: "people" }} onFill={fill} />
          </div>
          <Field label="Display name" required>{(id) => <TextInput id={id} value={name} onChange={(e) => setName(e.target.value)} />}</Field>
          <Field label="Email" hint="Optional.">{(id, d) => <TextInput id={id} aria-describedby={d} type="email" value={email} onChange={(e) => setEmail(e.target.value)} />}</Field>
          <Field label="View" required hint="The screens this person sees.">{(id, d) => <Select id={id} aria-describedby={d} value={view} onChange={(e) => setView(e.target.value as View)} options={viewOptions} />}</Field>
          <Field label="Title" hint="Optional. Defaults to the name of the view.">{(id, d) => <TextInput id={id} aria-describedby={d} value={title} onChange={(e) => setTitle(e.target.value)} />}</Field>
        </div>
      }
      onConfirm={async () => {
        if (!name.trim()) throw new Error("Enter a display name.");
        const person = await api.createPerson({ display_name: name.trim(), email: email.trim() || null, view, title: title.trim() || null });
        onCreated(person, view);
      }}
    />
  );
}

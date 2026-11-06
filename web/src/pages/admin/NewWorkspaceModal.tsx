// New country workspace: the form for every WorkspaceCreate field, with Fill from a JSON file.
// Checks the fields before sending, shows the server's problem if it refuses, and calls onCreated
// with the new workspace.
import { useEffect, useState } from "react";
import { api } from "../../api";
import { toProblem } from "../../api/errors";
import type { RequestBody, Workspace } from "../../api/types";
import { Button, Field, FillFromJson, Modal, Select, TextInput } from "../../components";
import { pickFields, type FieldMap } from "../../lib/fillJson";
import { useSession } from "../../app/session";

type WorkspaceCreate = RequestBody<"post_api_v1_workspaces">;
type DayCounting = WorkspaceCreate["day_counting"];
type Template = WorkspaceCreate["template"];

const DAY_COUNTING: { value: DayCounting; label: string }[] = [
  { value: "mon_fri", label: "Monday to Friday" },
  { value: "sun_thu", label: "Sunday to Thursday" },
  { value: "calendar", label: "Calendar days" },
];
const TEMPLATES: { value: Template; label: string }[] = [
  { value: "platform", label: "Platform template" },
  { value: "region", label: "Regional template" },
  { value: "copy", label: "Copy of another workspace" },
];

const FIELDS: FieldMap = {
  name: "string",
  country_code: "string",
  authority_name: "string",
  languages: "string[]",
  time_zone: "string",
  day_counting: "string",
  data_location: "string",
  template: "string",
  region: "string",
  copy_from_workspace_id: "string",
};

const COUNTRY_CODE = /^[A-Z]{2,3}$/;
const LANGUAGE = /^[a-z]{2}(-[A-Z]{2})?$/;
const REGION = /^[a-z][a-z0-9_-]{0,63}$/;

interface Form {
  name: string;
  countryCode: string;
  authority: string;
  languages: string;
  timeZone: string;
  dayCounting: DayCounting;
  dataLocation: string;
  template: Template;
  region: string;
  copyFrom: string;
}

const EMPTY: Form = {
  name: "",
  countryCode: "",
  authority: "",
  languages: "",
  timeZone: "",
  dayCounting: "mon_fri",
  dataLocation: "",
  template: "platform",
  region: "",
  copyFrom: "",
};

type Errors = Partial<Record<keyof Form, string>>;

const splitLanguages = (text: string) =>
  text
    .split(",")
    .map((l) => l.trim())
    .filter(Boolean);

function validate(f: Form): Errors {
  const e: Errors = {};
  const text = (key: "name" | "authority" | "timeZone" | "dataLocation", label: string) => {
    const v = f[key].trim();
    if (!v) e[key] = `${label} is required.`;
    else if (v.length > 500) e[key] = `${label} can be at most 500 characters.`;
  };
  text("name", "Name");
  if (!COUNTRY_CODE.test(f.countryCode.trim())) e.countryCode = "Use 2 or 3 capital letters, such as OM.";
  text("authority", "Authority name");
  const langs = splitLanguages(f.languages);
  if (langs.length === 0) e.languages = "Enter at least one language.";
  else if (langs.some((l) => !LANGUAGE.test(l))) e.languages = "Use codes such as en or ar-SA, separated by commas.";
  text("timeZone", "Time zone");
  text("dataLocation", "Data location");
  if (f.template === "region" && !REGION.test(f.region.trim())) e.region = "Use lowercase letters, digits, - or _, starting with a letter.";
  if (f.template === "copy" && !f.copyFrom) e.copyFrom = "Choose the workspace to copy.";
  return e;
}

function toBody(f: Form): WorkspaceCreate {
  return {
    name: f.name.trim(),
    country_code: f.countryCode.trim(),
    authority_name: f.authority.trim(),
    languages: splitLanguages(f.languages),
    time_zone: f.timeZone.trim(),
    day_counting: f.dayCounting,
    data_location: f.dataLocation.trim(),
    template: f.template,
    region: f.template === "region" ? f.region.trim() : null,
    copy_from_workspace_id: f.template === "copy" ? f.copyFrom : null,
  };
}

/** The form after a file fills it. Fields the file lacks keep what was typed. */
function filled(prev: Form, value: unknown): Form {
  const v = pickFields(value, FIELDS);
  const str = (k: string, old: string) => (typeof v[k] === "string" ? (v[k] as string) : old);
  const one = <T extends string>(k: string, allowed: { value: T }[], old: T): T => {
    const x = v[k];
    return allowed.find((a) => a.value === x)?.value ?? old;
  };
  return {
    name: str("name", prev.name),
    countryCode: str("country_code", prev.countryCode).toUpperCase(),
    authority: str("authority_name", prev.authority),
    languages: Array.isArray(v.languages) ? (v.languages as string[]).join(", ") : prev.languages,
    timeZone: str("time_zone", prev.timeZone),
    dayCounting: one("day_counting", DAY_COUNTING, prev.dayCounting),
    dataLocation: str("data_location", prev.dataLocation),
    template: one("template", TEMPLATES, prev.template),
    region: str("region", prev.region),
    copyFrom: str("copy_from_workspace_id", prev.copyFrom),
  };
}

export function NewWorkspaceModal({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (created: Workspace) => void | Promise<void> }) {
  const { workspaces } = useSession();
  const [form, setForm] = useState<Form>(EMPTY);
  const [errors, setErrors] = useState<Errors>({});
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<ReturnType<typeof toProblem> | null>(null);

  useEffect(() => {
    if (open) {
      setForm(EMPTY);
      setErrors({});
      setProblem(null);
      setBusy(false);
    }
  }, [open]);

  const set = <K extends keyof Form>(key: K, value: Form[K]) => setForm((f) => ({ ...f, [key]: value }));

  const submit = async () => {
    const found = validate(form);
    setErrors(found);
    setProblem(null);
    if (Object.keys(found).length) return;
    setBusy(true);
    try {
      const created = await api.createWorkspace(toBody(form));
      await onCreated(created);
      onClose();
    } catch (e) {
      setProblem(toProblem(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="New country workspace"
      size="lg"
      busy={busy}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button variant="primary" onClick={submit} loading={busy}>
            Create workspace
          </Button>
        </>
      }
    >
      <div className="stack">
        <div>
          <FillFromJson
            onFill={(v) => {
              setForm((f) => filled(f, v));
              setErrors({});
              setProblem(null);
            }}
          />
        </div>
        <div className="adm-opts">
          <Field label="Name" required error={errors.name} hint="The name shown in the workspace menu.">
            {(id, d) => <TextInput id={id} aria-describedby={d} value={form.name} onChange={(e) => set("name", e.target.value)} placeholder="For example Bahrain" data-autofocus />}
          </Field>
          <Field label="Country code" required error={errors.countryCode} hint="Two or three capital letters.">
            {(id, d) => <TextInput id={id} aria-describedby={d} value={form.countryCode} onChange={(e) => set("countryCode", e.target.value.toUpperCase())} maxLength={3} placeholder="For example BH" />}
          </Field>
          <div className="span-2">
            <Field label="Authority name" required error={errors.authority}>
              {(id, d) => <TextInput id={id} aria-describedby={d} value={form.authority} onChange={(e) => set("authority", e.target.value)} placeholder="The medicines authority of the country" />}
            </Field>
          </div>
          <Field label="Languages" required error={errors.languages} hint="Separate with commas, such as en, ar.">
            {(id, d) => <TextInput id={id} aria-describedby={d} value={form.languages} onChange={(e) => set("languages", e.target.value)} placeholder="en, ar" />}
          </Field>
          <Field label="Time zone" required error={errors.timeZone} hint="For example Asia/Bahrain.">
            {(id, d) => <TextInput id={id} aria-describedby={d} value={form.timeZone} onChange={(e) => set("timeZone", e.target.value)} />}
          </Field>
          <Field label="Day counting" hint="How working days are counted for deadlines.">
            {(id, d) => <Select id={id} aria-describedby={d} value={form.dayCounting} onChange={(e) => set("dayCounting", e.target.value as DayCounting)} options={DAY_COUNTING} />}
          </Field>
          <Field label="Data location" required error={errors.dataLocation} hint="Where this country's data is kept.">
            {(id, d) => <TextInput id={id} aria-describedby={d} value={form.dataLocation} onChange={(e) => set("dataLocation", e.target.value)} />}
          </Field>
          <Field label="Template" hint="What the new workspace starts from.">
            {(id, d) => <Select id={id} aria-describedby={d} value={form.template} onChange={(e) => set("template", e.target.value as Template)} options={TEMPLATES} />}
          </Field>
          {form.template === "region" ? (
            <Field label="Region" required error={errors.region} hint="The regional template to start from.">
              {(id, d) => <TextInput id={id} aria-describedby={d} value={form.region} onChange={(e) => set("region", e.target.value)} placeholder="For example gcc" />}
            </Field>
          ) : null}
          {form.template === "copy" ? (
            <Field label="Copy from" required error={errors.copyFrom}>
              {(id, d) => (
                <Select
                  id={id}
                  aria-describedby={d}
                  value={form.copyFrom}
                  onChange={(e) => set("copyFrom", e.target.value)}
                  options={[{ value: "", label: "Choose a workspace" }, ...workspaces.map((w) => ({ value: w.id, label: w.name }))]}
                />
              )}
            </Field>
          ) : null}
        </div>
        {problem ? (
          <div className="callout callout-held" role="alert">
            <strong>{problem.title}</strong>
            {problem.detail ? <div>{problem.detail}</div> : null}
          </div>
        ) : null}
      </div>
    </Modal>
  );
}

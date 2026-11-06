// Shared pieces of the case work screens: labels for methods and record states, names
// for people and agents, evidence anchors, the "what you read" fingerprint, refusals
// and the action dialog. Imported by every page in this folder (it also brings the CSS).
import "./cases.css";
import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, isApiError, toProblem } from "../../api";
import type { CheckMethod, Decision, DossierDocument, Proposal } from "../../api/types";
import { useSession } from "../../app/session";
import { Button, Checkbox, Field, FingerprintNote, GuardList, Icon, type IconName, Modal, Pill, TextArea, type Tone } from "../../components";
import { useApi } from "../../hooks/useApi";

// ---------------------------------------------------------------- labels

export const METHOD: Record<CheckMethod, { label: string; icon: IconName; hint: string }> = {
  code: { label: "Code", icon: "settings", hint: "A rule evaluated by code. No model involved." },
  typed: { label: "Typed", icon: "list-checks", hint: "An agent extracts typed values with quotes. The verdict comes from a rule in code." },
  agent: { label: "Agent", icon: "cpu", hint: "An agent reads the evidence and reports a finding." },
  person: { label: "Person", icon: "user", hint: "A person decides." },
};

export function MethodTag({ method }: { method: CheckMethod }) {
  const m = METHOD[method];
  return (
    <span className="cw-method" title={m.hint}>
      <Icon name={m.icon} size={13} />
      {m.label}
    </span>
  );
}

export const RECOMMENDATION_TONE: Record<string, Tone> = {
  proceed: "pass",
  hold_ask_company: "look",
  hold_officer_review: "look",
  change_route: "look",
  return_incomplete: "held",
};

const RECORD: Record<Decision["record_status"], { tone: Tone; icon: IconName; label: string }> = {
  not_needed: { tone: "neutral", icon: "check", label: "No record needed" },
  queued: { tone: "neutral", icon: "clock", label: "Queued" },
  sent: { tone: "accent", icon: "send", label: "Sent" },
  acknowledged: { tone: "pass", icon: "check-double", label: "Acknowledged" },
  unknown: { tone: "look", icon: "alert-triangle", label: "Outcome unknown" },
  rejected: { tone: "held", icon: "x", label: "Rejected" },
};

export function RecordStatusPill({ status, size = "md" }: { status: Decision["record_status"] | Proposal["state"]; size?: "sm" | "md" }) {
  const r = RECORD[status];
  return (
    <Pill tone={r.tone} icon={r.icon} size={size}>
      {r.label}
    </Pill>
  );
}

const DECISION_STATE: Record<Decision["state"], { tone: Tone; icon: IconName; label: string }> = {
  signed: { tone: "pass", icon: "stamp", label: "Signed" },
  on_hold: { tone: "held", icon: "pause", label: "On hold" },
  reconfirmed: { tone: "pass", icon: "check-double", label: "Reconfirmed" },
  revoked: { tone: "neutral", icon: "ban", label: "Revoked" },
};

export function DecisionStatePill({ state, size = "md" }: { state: Decision["state"]; size?: "sm" | "md" }) {
  const s = DECISION_STATE[state];
  return (
    <Pill tone={s.tone} icon={s.icon} size={size}>
      {s.label}
    </Pill>
  );
}

export const DECISION_TYPE: Record<Decision["type"], string> = {
  grant: "Grant",
  refusal: "Refusal",
  close: "Close",
  licence_action: "Licence action",
};

export const LEGAL_EFFECT: Record<Decision["legal_effect"], string> = {
  legal_act: "Legal act",
  record_only: "Record only",
  demonstration_only: "Demonstration only",
};

// ---------------------------------------------------------------- names

const AGENT_NAMES: Record<string, string> = {
  coordinator: "Coordinator",
  sameness: "Sameness",
  challenger: "Challenger",
  "checklist-assessor": "Assessor",
  pipeline: "Pipeline",
  rule: "Rule",
  gate: "Gate",
  watch: "Foreign watch",
  connector: "Connector",
  intake: "Intake",
  notifications: "Notifications",
};

/** Turns person and agent ids into names. Falls back to the id. */
export function useNames() {
  const { people } = useSession();
  return useMemo(() => {
    const byId = new Map(people.map((p) => [p.person_id, p.display_name]));
    return (id: string | null | undefined): string => {
      if (!id) return "Nobody";
      return byId.get(id) ?? AGENT_NAMES[id] ?? id;
    };
  }, [people]);
}

// ---------------------------------------------------------------- evidence anchors

export type AnchorKind = "dossier" | "reference" | "notice" | "clause";

export interface ParsedAnchor {
  kind: AnchorKind;
  doc: string;
  version: string;
  position: string;
  page: number | null;
}

export function parseAnchor(anchor: string): ParsedAnchor {
  const [doc = "", version = "", position = ""] = anchor.split(":");
  const page = /^p\d+$/.test(position) ? Number(position.slice(1)) : null;
  const kind: AnchorKind = doc.startsWith("d-") ? "dossier" : doc.startsWith("ref-doc") ? "reference" : /^n-\d/.test(doc) ? "notice" : "clause";
  return { kind, doc, version, position, page };
}

/** Where an anchor points, in words. */
export function describeSource(anchor: string, docs?: DossierDocument[]): string {
  const a = parseAnchor(anchor);
  if (a.kind === "dossier") {
    const name = docs?.find((d) => d.id === a.doc)?.filename ?? a.doc;
    return a.page ? `${name}, page ${a.page}` : name;
  }
  if (a.kind === "reference") return a.page ? `Reference approval, page ${a.page}` : "Reference approval";
  if (a.kind === "notice") return `Foreign notice ${a.doc}`;
  return `${a.doc}, ${a.position.startsWith("c") ? `passage ${Number(a.position.slice(1))}` : a.position}`;
}

export function evidenceHref(caseId: string, documentId: string, page: number): string {
  return `/cases/${caseId}/evidence?doc=${encodeURIComponent(documentId)}&page=${page}`;
}

export function useCaseDocs(caseId: string) {
  return useApi(() => api.listCaseDocuments(caseId), [caseId], { live: false });
}

/** Titles and legal status of the library documents pinned for a case, by document id. */
export function useSourceTitles(snapshotId: string | undefined) {
  const { data } = useApi(() => (snapshotId ? api.getSnapshot(snapshotId) : Promise.resolve(null)), [snapshotId], { live: false });
  return useMemo(() => {
    const byDoc = new Map((data?.items ?? []).map((i) => [i.document_version_id.replace(/-v\d+$/, ""), i]));
    return (docId: string) => byDoc.get(docId) ?? null;
  }, [data]);
}

// ---------------------------------------------------------------- what the person read

/**
 * Holds the fingerprint a person read, so an action sends that one and not whatever a
 * background reload brought in. If the evidence changes, the action is refused rather
 * than quietly applied to something the person never saw.
 */
export function useReadFingerprint(current: string | undefined, key: string) {
  const [read, setRead] = useState<{ key: string; fingerprint: string } | null>(null);
  useEffect(() => {
    if (current && (!read || read.key !== key)) setRead({ key, fingerprint: current });
  }, [current, key, read]);
  const fingerprint = read && read.key === key ? read.fingerprint : current;
  const accept = useCallback(() => {
    if (current) setRead({ key, fingerprint: current });
  }, [current, key]);
  return { fingerprint: fingerprint ?? "", stale: Boolean(fingerprint && current && fingerprint !== current), accept };
}

// ---------------------------------------------------------------- refusals

/** An action the server refused: every failed guard, and what changed if a fingerprint went stale. */
export function Refusal({
  error,
  read,
  checkHref,
  onReload,
  reloadLabel = "Reload and read again",
  onDismiss,
}: {
  error: unknown;
  /** The fingerprint the person acted on. */
  read?: string;
  checkHref?: (id: string) => string;
  onReload?: () => void;
  reloadLabel?: string;
  onDismiss?: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
  }, [error]);
  const problem = toProblem(error);
  const current = isApiError(error) ? error.currentFingerprint : null;
  const failed = isApiError(error) ? error.failed : [];
  return (
    <div className="stack-sm" ref={ref}>
      {failed.length ? (
        <GuardList guards={failed} title={`${problem.title}: ${failed.length} guard${failed.length === 1 ? "" : "s"} failed`} checkHref={checkHref} />
      ) : (
        <div className="callout callout-held" role="alert">
          <Icon name="alert-octagon" />
          <div>
            <strong>{problem.title}</strong>
            {problem.detail ? <div>{problem.detail}</div> : null}
          </div>
        </div>
      )}
      {read && current && read !== current ? <FingerprintNote fingerprint={read} current={current} /> : null}
      <div className="row">
        {onReload ? (
          <Button size="sm" icon="refresh" variant="primary" onClick={onReload}>
            {reloadLabel}
          </Button>
        ) : null}
        {onDismiss ? (
          <Button size="sm" variant="ghost" onClick={onDismiss}>
            Dismiss
          </Button>
        ) : null}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- action dialog

export interface ActionDialogProps {
  open: boolean;
  onClose: () => void;
  title: string;
  summary?: ReactNode;
  confirmLabel: string;
  variant?: "primary" | "danger";
  /** Shows a text box. `required` keeps the action disabled until it is filled. */
  text?: { label: string; required?: boolean; hint?: ReactNode; initial?: string; rows?: number };
  acknowledge?: string;
  /** Extra fields under the text box. */
  children?: ReactNode;
  /** Extra condition for enabling the action. */
  canConfirm?: boolean;
  onConfirm: (text: string) => Promise<unknown>;
  /** A refusal with guards is handed here and the dialog closes, so the page can explain it. */
  onRefused?: (error: unknown) => void;
}

/** Runs one action. Refusals either go to `onRefused`, or are shown in the dialog. */
export function ActionDialog(props: ActionDialogProps) {
  // Mounted only while open, so each opening starts with a clean form.
  return props.open ? <ActionDialogBody {...props} /> : null;
}

function ActionDialogBody({ open, onClose, title, summary, confirmLabel, variant = "primary", text, acknowledge, children, canConfirm = true, onConfirm, onRefused }: ActionDialogProps) {
  const [value, setValue] = useState(text?.initial ?? "");
  const [ack, setAck] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const ready = canConfirm && !busy && (!text?.required || value.trim().length > 0) && (!acknowledge || ack);
  const submit = async () => {
    if (!ready) return;
    setBusy(true);
    setError(null);
    try {
      await onConfirm(value.trim());
      onClose();
    } catch (e) {
      if (onRefused && isApiError(e) && e.failed.length) {
        onRefused(e);
        onClose();
      } else {
        setError(e);
        setBusy(false);
      }
    }
  };
  const problem = error ? toProblem(error) : null;

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      busy={busy}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button variant={variant} onClick={submit} disabled={!ready} loading={busy}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      <div className="stack">
        {summary ? <div className="confirm-summary">{summary}</div> : null}
        {text ? (
          <Field label={text.label} required={text.required} hint={text.hint}>
            {(id, describedBy) => <TextArea id={id} aria-describedby={describedBy} rows={text.rows ?? 3} value={value} onChange={(e) => setValue(e.target.value)} data-autofocus />}
          </Field>
        ) : null}
        {children}
        {acknowledge ? <Checkbox checked={ack} onChange={(e) => setAck(e.target.checked)} label={acknowledge} /> : null}
        {problem ? (
          isApiError(error) && error.failed.length ? (
            <GuardList guards={error.failed} title={problem.title} />
          ) : (
            <div className="callout callout-held" role="alert">
              <Icon name="alert-octagon" />
              <div>
                <strong>{problem.title}</strong>
                {problem.detail ? <div>{problem.detail}</div> : null}
              </div>
            </div>
          )
        ) : null}
      </div>
    </Modal>
  );
}

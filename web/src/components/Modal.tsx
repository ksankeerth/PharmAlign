// Modal: a centred dialog. ConfirmModal adds a built-in confirm step for actions that
// change a case or a setting: the person reviews a summary, optionally types a reason
// and ticks an acknowledgement, then confirms. Never use window.confirm.
//
//   <ConfirmModal open={open} onClose={close} title="Promote CASE-1043"
//     summary={<KeyValue items={...} />} confirmLabel="Promote" requireReason
//     onConfirm={async (reason) => api.promoteCase(id, { fingerprint, reason })} />
// If onConfirm throws an ApiError with failed guards, they are shown in the dialog.
import { type ReactNode, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { isApiError, toProblem } from "../api/errors";
import { Button, type ButtonVariant } from "./Button";
import { GuardList } from "./GuardList";
import { Checkbox, Field, TextArea, TextInput } from "./Field";
import { useFocusTrap } from "./useFocusTrap";

export interface ModalProps {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  size?: "sm" | "md" | "lg";
  /** Prevent closing by Escape or backdrop, while an action runs. */
  busy?: boolean;
}

export function Modal({ open, onClose, title, children, footer, size = "md", busy }: ModalProps) {
  const ref = useRef<HTMLDivElement>(null);
  const close = () => {
    if (!busy) onClose();
  };
  useFocusTrap(ref, open, close);
  if (!open) return null;
  return createPortal(
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && close()}>
      <div ref={ref} role="dialog" aria-modal="true" aria-label={typeof title === "string" ? title : undefined} className={`modal modal-${size}`} tabIndex={-1}>
        <div className="modal-head">
          <h2>{title}</h2>
          <Button variant="ghost" icon="x" aria-label="Close" onClick={close} disabled={busy} />
        </div>
        <div className="modal-body">{children}</div>
        {footer ? <div className="modal-foot">{footer}</div> : null}
      </div>
    </div>,
    document.body,
  );
}

export interface ConfirmModalProps {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  /** What will happen, shown before the person confirms. */
  summary: ReactNode;
  confirmLabel: string;
  variant?: Extract<ButtonVariant, "primary" | "danger">;
  /** Ask for a reason; passed to onConfirm. */
  requireReason?: boolean;
  reasonLabel?: string;
  /** A statement the person must tick, such as "I have read the evidence". */
  acknowledge?: string;
  /** A word the person must type before confirming, for an action that can't be undone. */
  typeToConfirm?: string;
  onConfirm: (reason: string) => Promise<unknown> | unknown;
  /** Called after onConfirm succeeds, before closing. */
  onDone?: () => void;
}

export function ConfirmModal({ open, onClose, title, summary, confirmLabel, variant = "primary", requireReason, reasonLabel = "Reason", acknowledge, typeToConfirm, onConfirm, onDone }: ConfirmModalProps) {
  const [reason, setReason] = useState("");
  const [ack, setAck] = useState(false);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  // Clear on close, not on open: an effect that runs after opening can wipe what was typed in the first moments.
  useEffect(() => {
    if (!open) {
      setReason("");
      setAck(false);
      setTyped("");
      setError(null);
      setBusy(false);
    }
  }, [open]);

  const ready = (!requireReason || reason.trim().length > 0) && (!acknowledge || ack) && (!typeToConfirm || typed.trim() === typeToConfirm) && !busy;
  const submit = async () => {
    if (!ready) return;
    setBusy(true);
    setError(null);
    try {
      await onConfirm(reason.trim());
      onDone?.();
      onClose();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
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
        <div className="confirm-summary">{summary}</div>
        {requireReason ? (
          <Field label={reasonLabel} required>
            {(id) => <TextArea id={id} value={reason} onChange={(e) => setReason(e.target.value)} rows={3} data-autofocus />}
          </Field>
        ) : null}
        {acknowledge ? <Checkbox checked={ack} onChange={(e) => setAck(e.target.checked)} label={acknowledge} /> : null}
        {typeToConfirm ? (
          <Field label={`Type ${typeToConfirm} to confirm`} required>
            {(id) => <TextInput id={id} value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" spellCheck={false} />}
          </Field>
        ) : null}
        {problem ? (
          isApiError(error) && error.failed.length ? (
            <GuardList guards={error.failed} title={problem.title} />
          ) : (
            <div className="callout callout-held" role="alert">
              <strong>{problem.title}</strong>
              {problem.detail ? <div>{problem.detail}</div> : null}
            </div>
          )
        ) : null}
      </div>
    </Modal>
  );
}

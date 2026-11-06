// Form fields. Field wraps any control with a label, hint and error, and wires the
// ids for accessibility. TextInput, TextArea, Select and Checkbox are styled controls.
//
//   <Field label="Reason" hint="Saved in the audit log" error={err}>
//     {(id) => <TextArea id={id} value={v} onChange={...} />}
//   </Field>
import { forwardRef, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes, useId } from "react";

export interface FieldProps {
  label: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  required?: boolean;
  children: (id: string, describedBy: string | undefined) => ReactNode;
}

export function Field({ label, hint, error, required, children }: FieldProps) {
  const id = useId();
  const hintId = hint ? `${id}-hint` : undefined;
  const errId = error ? `${id}-err` : undefined;
  const describedBy = [hintId, errId].filter(Boolean).join(" ") || undefined;
  return (
    <div className={`field ${error ? "field-invalid" : ""}`}>
      <label htmlFor={id} className="field-label">
        {label}
        {required ? <span className="field-required" aria-hidden="true"> *</span> : null}
      </label>
      {children(id, describedBy)}
      {hint ? (
        <div id={hintId} className="field-hint">
          {hint}
        </div>
      ) : null}
      {error ? (
        <div id={errId} className="field-error" role="alert">
          {error}
        </div>
      ) : null}
    </div>
  );
}

export const TextInput = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function TextInput({ className, ...rest }, ref) {
  return <input ref={ref} className={`input ${className ?? ""}`} {...rest} />;
});

export const TextArea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement> & { "data-autofocus"?: boolean }>(function TextArea({ className, ...rest }, ref) {
  return <textarea ref={ref} className={`input textarea ${className ?? ""}`} {...rest} />;
});

export interface SelectOption {
  value: string;
  label: string;
  disabled?: boolean;
}

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement> & { options: SelectOption[]; placeholder?: string }>(function Select(
  { className, options, placeholder, ...rest },
  ref,
) {
  return (
    <select ref={ref} className={`input select ${className ?? ""}`} {...rest}>
      {placeholder ? (
        <option value="" disabled>
          {placeholder}
        </option>
      ) : null}
      {options.map((o) => (
        <option key={o.value} value={o.value} disabled={o.disabled}>
          {o.label}
        </option>
      ))}
    </select>
  );
});

export function Checkbox({ label, hint, ...rest }: InputHTMLAttributes<HTMLInputElement> & { label: ReactNode; hint?: ReactNode }) {
  return (
    <label className="checkbox">
      <input type="checkbox" {...rest} />
      <span>
        {label}
        {hint ? <span className="field-hint"> {hint}</span> : null}
      </span>
    </label>
  );
}

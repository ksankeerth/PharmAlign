// Small labelled controls so the editor forms stay short.
import type { ReactNode } from "react";
import { Field, Select, type SelectOption, TextArea, TextInput } from "../../../components";

export function TextF({ label, value, onChange, hint, disabled, error, list, placeholder, required, wide }: { label: ReactNode; value: string | null | undefined; onChange: (v: string) => void; hint?: ReactNode; disabled?: boolean; error?: ReactNode; list?: string; placeholder?: string; required?: boolean; wide?: boolean }) {
  return (
    <div className={wide ? "ow-wide" : undefined}>
      <Field label={label} hint={hint} error={error} required={required}>
        {(id, d) => <TextInput id={id} aria-describedby={d} value={value ?? ""} disabled={disabled} list={list} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />}
      </Field>
    </div>
  );
}

/** A number that may be empty (null). */
export function NumF({ label, value, onChange, hint, disabled, min = 0, max, step, error }: { label: ReactNode; value: number | null | undefined; onChange: (v: number | null) => void; hint?: ReactNode; disabled?: boolean; min?: number; max?: number; step?: number; error?: ReactNode }) {
  return (
    <Field label={label} hint={hint} error={error}>
      {(id, d) => (
        <TextInput
          id={id}
          aria-describedby={d}
          type="number"
          inputMode="decimal"
          min={min}
          max={max}
          step={step}
          disabled={disabled}
          value={value ?? ""}
          onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
        />
      )}
    </Field>
  );
}

export function SelectF<T extends string>({ label, value, onChange, options, hint, disabled, wide, error }: { label: ReactNode; value: T; onChange: (v: T) => void; options: SelectOption[]; hint?: ReactNode; disabled?: boolean; wide?: boolean; error?: ReactNode }) {
  return (
    <div className={wide ? "ow-wide" : undefined}>
      <Field label={label} hint={hint} error={error}>
        {(id, d) => <Select id={id} aria-describedby={d} value={value} disabled={disabled} options={options} onChange={(e) => onChange(e.target.value as T)} />}
      </Field>
    </div>
  );
}

export function AreaF({ label, value, onChange, hint, disabled, rows = 3, mono, error }: { label: ReactNode; value: string | null | undefined; onChange: (v: string) => void; hint?: ReactNode; disabled?: boolean; rows?: number; mono?: boolean; error?: ReactNode }) {
  return (
    <div className="ow-wide">
      <Field label={label} hint={hint} error={error}>
        {(id, d) => <TextArea id={id} aria-describedby={d} rows={rows} value={value ?? ""} disabled={disabled} className={mono ? "mono" : undefined} onChange={(e) => onChange(e.target.value)} />}
      </Field>
    </div>
  );
}

export function Why({ children }: { children: ReactNode }) {
  return children ? (
    <p className="ow-why" role="note">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="icon" aria-hidden="true">
        <rect x="3" y="11" width="18" height="11" rx="2" />
        <path d="M7 11V7a5 5 0 0 1 10 0v4" />
      </svg>
      <span>{children}</span>
    </p>
  ) : null;
}

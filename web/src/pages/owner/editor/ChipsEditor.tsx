// A list of short values shown as removable chips, with an input to add more.
import { useState } from "react";
import { Button, Icon, TextInput } from "../../../components";

export function ChipsEditor({
  values,
  onChange,
  label,
  placeholder = "Add and press Enter",
  readOnly,
  list,
  emptyText = "None",
}: {
  values: string[];
  onChange: (next: string[]) => void;
  label: string;
  placeholder?: string;
  readOnly?: boolean;
  /** id of a <datalist> to suggest from. */
  list?: string;
  emptyText?: string;
}) {
  const [text, setText] = useState("");
  const add = () => {
    const v = text.trim();
    if (!v || values.includes(v)) {
      setText("");
      return;
    }
    onChange([...values, v]);
    setText("");
  };
  return (
    <div className="ow-chips" role="group" aria-label={label}>
      {values.length === 0 && readOnly ? <span className="muted small">{emptyText}</span> : null}
      {values.map((v) => (
        <span className="ow-chip" key={v}>
          {v}
          {readOnly ? null : (
            <button type="button" aria-label={`Remove ${v}`} onClick={() => onChange(values.filter((x) => x !== v))}>
              <Icon name="x" size={12} />
            </button>
          )}
        </span>
      ))}
      {readOnly ? null : (
        <>
          <TextInput
            value={text}
            list={list}
            placeholder={values.length ? "Add" : placeholder}
            aria-label={`Add to ${label}`}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                add();
              }
            }}
            onBlur={add}
          />
          {values.length === 0 ? <span className="muted xsmall">{emptyText}</span> : null}
          <Button size="sm" variant="ghost" icon="plus" aria-label={`Add to ${label}`} onClick={add} disabled={!text.trim()} />
        </>
      )}
    </div>
  );
}

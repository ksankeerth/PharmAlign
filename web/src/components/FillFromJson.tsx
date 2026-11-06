// FillFromJson: a button that fills a form from a JSON file, for demo files and bulk entry.
// It reads the file, parses it and calls onFill(value). With `pick`, a file that holds several
// items opens a short list to choose one, and onFill gets that item.
//
//   <FillFromJson onFill={(v) => setForm(pickFields(v, FIELDS))} />
//   <FillFromJson pick={{ key: "people" }} onFill={(item) => setForm(pickFields(item, FIELDS))} />
import { type ChangeEvent, useRef, useState } from "react";
import { itemsOf, labelOf, parseJson, type JsonObject } from "../lib/fillJson";
import { Button } from "./Button";
import { Modal } from "./Modal";
import { useToast } from "./Toast";

export interface FillFromJsonProps {
  onFill: (value: unknown) => void;
  /** Choose one item when the file holds several. `key` names the list in a wrapper object. */
  pick?: { key?: string; label?: (item: JsonObject) => string };
}

export function FillFromJson({ onFill, pick }: FillFromJsonProps) {
  const toast = useToast();
  const input = useRef<HTMLInputElement>(null);
  const [choices, setChoices] = useState<JsonObject[] | null>(null);

  const onFile = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = ""; // so the same file can be chosen again
    if (!file) return;
    let text: string;
    try {
      text = await file.text();
    } catch {
      toast.error("That file could not be read.");
      return;
    }
    const parsed = parseJson(text);
    if (!parsed.ok) {
      toast.error(parsed.error);
      return;
    }
    if (!pick) {
      onFill(parsed.value);
      return;
    }
    const items = itemsOf(parsed.value, pick.key);
    if (items.length === 0) toast.error("That file has nothing to fill from.");
    else if (items.length === 1) onFill(items[0]);
    else setChoices(items);
  };

  const choose = (item: JsonObject) => {
    setChoices(null);
    onFill(item);
  };

  return (
    <>
      <Button size="sm" variant="secondary" icon="upload" onClick={() => input.current?.click()}>
        Fill from a JSON file
      </Button>
      <input ref={input} type="file" accept=".json,application/json" hidden tabIndex={-1} aria-hidden="true" onChange={onFile} />
      <Modal open={choices !== null} onClose={() => setChoices(null)} title="Choose one to fill from" size="sm">
        <div className="stack-sm">
          {choices?.map((item, i) => (
            <Button key={i} onClick={() => choose(item)}>
              {pick?.label ? pick.label(item) : labelOf(item, `Item ${i + 1}`)}
            </Button>
          ))}
        </div>
      </Modal>
    </>
  );
}

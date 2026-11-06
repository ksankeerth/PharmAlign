// FingerprintNote: shows the fingerprint an action will be tied to (what the person
// read), with a copy button. Pass `current` to show that something changed since the
// person read it, as returned in a 409's current_fingerprint.
import { useState } from "react";
import { shortFingerprint } from "../lib/format";
import { Icon } from "./Icon";

export interface FingerprintNoteProps {
  fingerprint: string;
  /** The server's current fingerprint, when it differs (stale). */
  current?: string | null;
  label?: string;
}

export function FingerprintNote({ fingerprint, current, label = "Fingerprint of what you read" }: FingerprintNoteProps) {
  const [copied, setCopied] = useState(false);
  const stale = Boolean(current && current !== fingerprint);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(fingerprint);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // clipboard blocked: nothing to do
    }
  };
  return (
    <div className={`fingerprint ${stale ? "fingerprint-stale" : ""}`}>
      <Icon name={stale ? "alert-triangle" : "hash"} />
      <div className="fingerprint-text">
        <div className="xsmall muted">{stale ? "Changed since you read it" : label}</div>
        <code title={fingerprint}>{shortFingerprint(fingerprint, 16)}</code>
        {stale ? (
          <div className="xsmall">
            Now <code title={current ?? ""}>{shortFingerprint(current, 16)}</code>. Reload and review before acting.
          </div>
        ) : null}
      </div>
      <button type="button" className="btn btn-ghost btn-sm btn-icon" onClick={copy} aria-label="Copy fingerprint">
        <Icon name={copied ? "check" : "copy"} />
      </button>
    </div>
  );
}

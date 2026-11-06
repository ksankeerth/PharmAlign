// A comment the assistant drafted. It stays a draft until the person edits it and posts it as themselves.
import { useState } from "react";
import { api } from "../../api";
import { Button, Callout, ErrorState, Field, TextArea, useToast } from "../../components";

export function DraftComment({ caseId, checkResultId, text }: { caseId: string; checkResultId: string | null; text: string }) {
  const toast = useToast();
  const [value, setValue] = useState(text);
  const [state, setState] = useState<"draft" | "posting" | "posted" | "dismissed">("draft");
  const [error, setError] = useState<unknown>(null);
  if (state === "dismissed") return <p className="small muted">Draft dismissed. Nothing was posted.</p>;
  if (state === "posted") return <Callout tone="pass" title="Posted by you">Your comment is now an open review thread, and blocks promotion until it is resolved.</Callout>;
  const post = async () => {
    setState("posting");
    setError(null);
    try {
      await api.createThread(caseId, { target: checkResultId ? { type: "check", ref: checkResultId } : { type: "case", ref: caseId }, text: value.trim() });
      setState("posted");
      toast.success("Comment posted");
    } catch (e) {
      setError(e);
      setState("draft");
    }
  };
  return (
    <Callout tone="accent" title="Draft comment. Not posted" icon="edit">
      <div className="stack-sm">
        <span className="small">The assistant can't post. Edit it if you want, then post it as yourself.</span>
        <Field label="Comment text">{(id) => <TextArea id={id} rows={4} value={value} onChange={(e) => setValue(e.target.value)} />}</Field>
        {error ? <ErrorState error={error} /> : null}
        <div className="row">
          <Button size="sm" variant="primary" icon="send" onClick={post} loading={state === "posting"} disabled={!value.trim()}>
            Post as me
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setState("dismissed")}>
            Dismiss
          </Button>
        </div>
      </div>
    </Callout>
  );
}

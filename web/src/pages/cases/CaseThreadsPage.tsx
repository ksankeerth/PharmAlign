// Review threads, like a pull request: comments on a check, page, clause or the case.
// Reply, resolve, or ask the agent to re-check with the comment as context.
// Open threads block promotion, and the page says so.
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../../api";
import type { CheckResult, Comment, Thread, ThreadTarget } from "../../api/types";
import { useSession } from "../../app/session";
import { Button, Callout, EmptyState, ErrorState, Field, Icon, Panel, Pill, Select, SkeletonLines, TextArea, TextInput, useToast } from "../../components";
import { useApi } from "../../hooks/useApi";
import { formatDateTime, formatRelative } from "../../lib/format";
import { ActionDialog, describeSource, evidenceHref, parseAnchor, useCaseDocs, useNames } from "./shared";
import { actsAs } from "../../lib/roles";

function CommentRow({ comment, names }: { comment: Comment; names: (id: string) => string }) {
  const agent = comment.author_type === "agent";
  return (
    <li className="cw-comment">
      <span className={`cw-avatar ${agent ? "cw-avatar-agent" : ""}`} aria-hidden="true">
        <Icon name={agent ? "cpu" : "user"} size={13} />
      </span>
      <div>
        <div className="cw-comment-meta">
          <strong>{names(comment.author_id)}</strong>
          {agent ? (
            <Pill tone="accent" size="sm">
              Agent
            </Pill>
          ) : null}
          <time dateTime={comment.at} title={formatDateTime(comment.at)}>
            {formatRelative(comment.at)}
          </time>
        </div>
        <p>{comment.text}</p>
      </div>
    </li>
  );
}

function TargetLabel({ target, caseId, checks }: { target: ThreadTarget; caseId: string; checks: CheckResult[] }) {
  const docs = useCaseDocs(caseId);
  if (target.type === "check") {
    const c = checks.find((x) => x.id === target.ref);
    return (
      <span className="row">
        <Icon name="list-checks" size={13} />
        <Link to={`/cases/${caseId}/checks/${target.ref}`}>{c ? `${c.check_id} ${c.name}` : target.ref}</Link>
      </span>
    );
  }
  if (target.type === "page") {
    const a = parseAnchor(target.ref);
    return (
      <span className="row">
        <Icon name="file-text" size={13} />
        {a.kind === "dossier" && a.page ? <Link to={evidenceHref(caseId, a.doc, a.page)}>{describeSource(target.ref, docs.data?.items)}</Link> : <span>{target.ref}</span>}
      </span>
    );
  }
  if (target.type === "clause") {
    return (
      <span className="row">
        <Icon name="book" size={13} />
        <span>Clause {target.ref}</span>
      </span>
    );
  }
  return (
    <span className="row">
      <Icon name="inbox" size={13} />
      <span>The case</span>
    </span>
  );
}

function ThreadCard({ thread, checks, canAct, names }: { thread: Thread; checks: CheckResult[]; canAct: boolean; names: (id: string) => string }) {
  const toast = useToast();
  const [reply, setReply] = useState("");
  const [busy, setBusy] = useState<"reply" | "recheck" | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [resolving, setResolving] = useState(false);
  const check = thread.target.type === "check" ? checks.find((c) => c.id === thread.target.ref) : undefined;
  const canRecheck = check && (check.method === "agent" || check.method === "typed" || check.status === "error") && check.status !== "running";
  const open = thread.status === "open";

  const send = async () => {
    setBusy("reply");
    setError(null);
    try {
      await api.addComment(thread.id, { text: reply.trim() });
      setReply("");
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  };
  const recheck = async () => {
    if (!check) return;
    setBusy("recheck");
    setError(null);
    try {
      if (reply.trim()) {
        await api.addComment(thread.id, { text: reply.trim() });
        setReply("");
      }
      await api.recheckCheck(check.id, { thread_id: thread.id });
      toast.info(`Re-checking ${check.check_id}. The agent replies here when it finishes.`);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  };

  return (
    <article className={`cw-thread ${open ? "cw-thread-open" : ""}`} aria-label={`Thread on ${thread.target.type}`}>
      <div className="cw-thread-head">
        {open ? (
          <Pill tone="look" icon="message" size="sm">
            Open
          </Pill>
        ) : (
          <Pill tone="neutral" icon="check" size="sm">
            Resolved
          </Pill>
        )}
        <TargetLabel target={thread.target} caseId={thread.case_id} checks={checks} />
        <span className="muted xsmall">
          {thread.comments.length} comment{thread.comments.length === 1 ? "" : "s"}
        </span>
        {open && thread.blocks_promotion ? (
          <span className="xsmall row">
            <Icon name="lock" size={12} />
            Blocks promotion
          </span>
        ) : null}
        {!open && thread.resolved_by ? (
          <span className="xsmall muted">
            Resolved by {names(thread.resolved_by)} {thread.resolved_at ? formatRelative(thread.resolved_at) : ""}
          </span>
        ) : null}
      </div>
      <ol className="cw-comments">
        {thread.comments.map((c) => (
          <CommentRow key={c.id} comment={c} names={names} />
        ))}
      </ol>
      {open && canAct ? (
        <div className="cw-thread-reply">
          <Field label="Reply">{(id) => <TextArea id={id} rows={2} value={reply} onChange={(e) => setReply(e.target.value)} />}</Field>
          {error ? <ErrorState error={error} /> : null}
          <div className="row">
            <Button size="sm" variant="primary" icon="send" disabled={!reply.trim()} loading={busy === "reply"} onClick={send}>
              Reply
            </Button>
            {canRecheck ? (
              <Button size="sm" icon="refresh" loading={busy === "recheck"} onClick={recheck} title="The agent runs again with this thread as context.">
                Ask the agent to re-check
              </Button>
            ) : null}
            <Button size="sm" icon="check" onClick={() => setResolving(true)}>
              Resolve
            </Button>
          </div>
        </div>
      ) : null}
      <ActionDialog
        open={resolving}
        onClose={() => setResolving(false)}
        title="Resolve this thread"
        confirmLabel="Resolve"
        text={{ label: "Closing note (optional)" }}
        onConfirm={async (note) => {
          await api.resolveThread(thread.id, { note: note || null });
          toast.success("Thread resolved");
        }}
      />
    </article>
  );
}

function NewThread({ caseId, checks, onDone }: { caseId: string; checks: CheckResult[]; onDone: () => void }) {
  const toast = useToast();
  const [type, setType] = useState<ThreadTarget["type"]>("case");
  const [checkRef, setCheckRef] = useState(checks[0]?.id ?? "");
  const [ref, setRef] = useState("");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const target = type === "case" ? caseId : type === "check" ? checkRef : ref.trim();
  const ready = text.trim() && target && !busy;
  return (
    <Panel title="New thread">
      <form
        className="stack-sm"
        onSubmit={async (e) => {
          e.preventDefault();
          if (!ready) return;
          setBusy(true);
          setError(null);
          try {
            await api.createThread(caseId, { target: { type, ref: target }, text: text.trim() });
            toast.success("Thread opened. It blocks promotion until resolved.");
            onDone();
          } catch (err) {
            setError(err);
          } finally {
            setBusy(false);
          }
        }}
      >
        <div className="grid-2">
          <Field label="On">
            {(id) => (
              <Select
                id={id}
                value={type}
                onChange={(e) => setType(e.target.value as ThreadTarget["type"])}
                options={[
                  { value: "case", label: "The case" },
                  { value: "check", label: "A check" },
                  { value: "page", label: "A dossier page" },
                  { value: "clause", label: "A clause" },
                ]}
              />
            )}
          </Field>
          {type === "check" ? (
            <Field label="Check">{(id) => <Select id={id} value={checkRef} onChange={(e) => setCheckRef(e.target.value)} options={checks.map((c) => ({ value: c.id, label: `${c.check_id} ${c.name}` }))} />}</Field>
          ) : type === "page" ? (
            <Field label="Page anchor" hint="Such as d-1042-17:v1:p41">
              {(id, describedBy) => <TextInput id={id} aria-describedby={describedBy} value={ref} onChange={(e) => setRef(e.target.value)} />}
            </Field>
          ) : type === "clause" ? (
            <Field label="Clause" hint="Such as E-S25, 3.2.6.1">
              {(id, describedBy) => <TextInput id={id} aria-describedby={describedBy} value={ref} onChange={(e) => setRef(e.target.value)} />}
            </Field>
          ) : null}
        </div>
        <Field label="Comment" required>
          {(id) => <TextArea id={id} rows={3} value={text} onChange={(e) => setText(e.target.value)} />}
        </Field>
        {error ? <ErrorState error={error} /> : null}
        <div className="row">
          <Button type="submit" variant="primary" icon="message" disabled={!ready} loading={busy}>
            Open thread
          </Button>
          <Button variant="ghost" onClick={onDone}>
            Cancel
          </Button>
        </div>
      </form>
    </Panel>
  );
}

export default function CaseThreadsPage() {
  const { caseId = "" } = useParams();
  const { view } = useSession();
  const names = useNames();
  const threads = useApi(() => api.listThreads(caseId, { limit: 200 }), [caseId]);
  const checks = useApi(() => api.listCaseChecks(caseId), [caseId]);
  const [adding, setAdding] = useState(false);
  const canAct = actsAs(view, "case_officer") || actsAs(view, "decision_maker");

  if (threads.error) return <ErrorState error={threads.error} onRetry={threads.reload} />;
  if (!threads.data) return <SkeletonLines lines={6} />;
  const items = threads.data.items;
  const open = items.filter((t) => t.status === "open");
  const resolved = items.filter((t) => t.status === "resolved");
  const checkItems = checks.data?.items ?? [];

  return (
    <div className="stack">
      {open.length ? (
        <Callout tone="look" icon="lock" title={`${open.length} open thread${open.length === 1 ? "" : "s"} block${open.length === 1 ? "s" : ""} promotion`}>
          Every thread must be resolved by a person before this case can be promoted.
        </Callout>
      ) : (
        <Callout tone="pass" title="No open threads">
          Review comments are not blocking promotion.
        </Callout>
      )}
      <div className="row-between">
        <span className="small muted num">
          {open.length} open, {resolved.length} resolved
        </span>
        {canAct && !adding ? (
          <Button icon="plus" onClick={() => setAdding(true)}>
            New thread
          </Button>
        ) : null}
      </div>
      {adding ? <NewThread caseId={caseId} checks={checkItems} onDone={() => setAdding(false)} /> : null}
      {items.length === 0 && !adding ? (
        <EmptyState title="No comments yet" icon="message">
          Comment on a check, a dossier page, a clause or the case.
        </EmptyState>
      ) : null}
      {open.map((t) => (
        <ThreadCard key={t.id} thread={t} checks={checkItems} canAct={canAct} names={names} />
      ))}
      {resolved.length ? (
        <details className="cw-disclosure" open={open.length === 0}>
          <summary>
            {resolved.length} resolved thread{resolved.length === 1 ? "" : "s"}
          </summary>
          <div className="stack-sm">
            {resolved.map((t) => (
              <ThreadCard key={t.id} thread={t} checks={checkItems} canAct={canAct} names={names} />
            ))}
          </div>
        </details>
      ) : null}
    </div>
  );
}

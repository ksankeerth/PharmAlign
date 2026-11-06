// Questions to the company: drafted by an agent or a person. A person edits, sends
// (which sets a reply deadline and stops the clock) and closes them. Anything not
// closed blocks promotion.
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../../api";
import type { CheckResult, Question } from "../../api/types";
import { useSession } from "../../app/session";
import { Button, Callout, Checkbox, EmptyState, ErrorState, Field, Modal, Panel, Pill, SkeletonLines, TextArea, TextInput, type Tone, useToast, type IconName } from "../../components";
import { useApi } from "../../hooks/useApi";
import { formatDate, formatRelative } from "../../lib/format";
import { ActionDialog, Refusal, useNames } from "./shared";

const STATUS: Record<Question["status"], { tone: Tone; icon: IconName; label: string }> = {
  draft: { tone: "look", icon: "edit", label: "Draft, not sent" },
  sent: { tone: "accent", icon: "send", label: "Sent, waiting for a reply" },
  answered: { tone: "pass", icon: "check", label: "Answered" },
  closed: { tone: "neutral", icon: "check", label: "Closed" },
};

function dateInDays(days: number): string {
  const d = new Date(Date.now() + days * 86_400_000);
  return d.toISOString().slice(0, 10);
}

function SendDialog({ question, onClose, onRefused }: { question: Question; onClose: () => void; onRefused: (e: unknown) => void }) {
  const toast = useToast();
  const [due, setDue] = useState(dateInDays(14));
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    setBusy(true);
    try {
      await api.sendQuestion(question.id, { reply_due_at: new Date(`${due}T23:59:00Z`).toISOString() });
      toast.success("Sent to the company. The clock stopped.");
    } catch (e) {
      onRefused(e);
    } finally {
      setBusy(false);
      onClose();
    }
  };
  return (
    <Modal
      open
      onClose={onClose}
      title="Send to the company"
      busy={busy}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button variant="primary" icon="send" onClick={submit} loading={busy} disabled={!due}>
            Send
          </Button>
        </>
      }
    >
      <div className="stack">
        <p className="small cw-letter">{Object.values(question.texts)[0]}</p>
        <Field label="Reply deadline" required hint="Sending stops the review clock until the company replies or a person closes the question.">
          {(id, describedBy) => <TextInput id={id} type="date" aria-describedby={describedBy} value={due} onChange={(e) => setDue(e.target.value)} />}
        </Field>
      </div>
    </Modal>
  );
}

function QuestionCard({ question, caseId, checks, canAct, names, onRefused }: { question: Question; caseId: string; checks: CheckResult[]; canAct: boolean; names: (id: string | null) => string; onRefused: (e: unknown) => void }) {
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [texts, setTexts] = useState(question.texts);
  const [busy, setBusy] = useState(false);
  const [dialog, setDialog] = useState<"send" | "close" | null>(null);
  const [error, setError] = useState<unknown>(null);
  const s = STATUS[question.status];
  const entries = Object.entries(question.texts);
  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.updateQuestion(question.id, { texts });
      setEditing(false);
      toast.success("Draft saved");
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <article className="cw-question-card" aria-label={`Question ${question.id}`}>
      <div className="cw-question-head">
        <Pill tone={s.tone} icon={s.icon} size="sm">
          {s.label}
        </Pill>
        <code className="xsmall">{question.id}</code>
        <Pill tone="neutral" icon={question.drafted_by === "agent" ? "cpu" : "user"} size="sm">
          Drafted by {question.drafted_by === "agent" ? "an agent" : "a person"}
        </Pill>
        {question.clock_stopped ? (
          <Pill tone="neutral" icon="pause" size="sm">
            Clock stopped
          </Pill>
        ) : null}
        {question.check_ids.length ? (
          <span className="xsmall row">
            About:
            {question.check_ids.map((id) => {
              const c = checks.find((x) => x.check_id === id);
              return c ? (
                <Link key={id} to={`/cases/${caseId}/checks/${c.id}`}>
                  {id}
                </Link>
              ) : (
                <span key={id}>{id}</span>
              );
            })}
          </span>
        ) : null}
      </div>
      <div className="cw-question-body">
        {editing ? (
          Object.entries(texts).map(([lang, text]) => (
            <Field key={lang} label={`Text (${lang})`}>
              {(id) => <TextArea id={id} rows={6} value={text} onChange={(e) => setTexts({ ...texts, [lang]: e.target.value })} />}
            </Field>
          ))
        ) : (
          entries.map(([lang, text]) => (
            <div key={lang}>
              {entries.length > 1 ? <div className="xsmall muted">{lang}</div> : null}
              <p className="cw-letter">{text}</p>
            </div>
          ))
        )}
        {question.status !== "draft" && question.sent_at ? (
          <div className="xsmall muted">
            Sent by {names(question.sent_by)} {formatRelative(question.sent_at)}
            {question.reply_due_at ? `. Reply due ${formatDate(question.reply_due_at)} (${formatRelative(question.reply_due_at)}).` : "."}
          </div>
        ) : null}
        {error ? <ErrorState error={error} /> : null}
        {canAct && question.status !== "closed" ? (
          <div className="row">
            {question.status === "draft" && !editing ? (
              <>
                <Button size="sm" variant="primary" icon="send" onClick={() => setDialog("send")}>
                  Send
                </Button>
                <Button
                  size="sm"
                  icon="edit"
                  onClick={() => {
                    setTexts(question.texts);
                    setEditing(true);
                  }}
                >
                  Edit
                </Button>
              </>
            ) : null}
            {editing ? (
              <>
                <Button size="sm" variant="primary" onClick={save} loading={busy}>
                  Save draft
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>
                  Cancel
                </Button>
              </>
            ) : (
              <Button size="sm" icon="x" onClick={() => setDialog("close")}>
                Close
              </Button>
            )}
          </div>
        ) : null}
      </div>
      {dialog === "send" ? <SendDialog question={question} onClose={() => setDialog(null)} onRefused={onRefused} /> : null}
      <ActionDialog
        open={dialog === "close"}
        onClose={() => setDialog(null)}
        title="Close this question"
        confirmLabel="Close question"
        summary={<p className="small" style={{ margin: 0 }}>Closing it stops it blocking promotion, and restarts the clock if nothing else is with the company.</p>}
        text={{ label: "Reason", required: true, hint: "For example: answered in sequence 3, or the company stayed silent past the deadline." }}
        onConfirm={async (reason) => {
          await api.closeQuestion(question.id, { reason });
          toast.success("Question closed");
        }}
        onRefused={onRefused}
      />
    </article>
  );
}

function NewQuestion({ caseId, checks, onDone }: { caseId: string; checks: CheckResult[]; onDone: () => void }) {
  const toast = useToast();
  const [selected, setSelected] = useState<string[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  return (
    <Panel title="New question">
      <form
        className="stack-sm"
        onSubmit={async (e) => {
          e.preventDefault();
          if (!text.trim()) return;
          setBusy(true);
          setError(null);
          try {
            await api.createQuestion(caseId, { check_ids: selected, texts: { en: text.trim() } });
            toast.success("Draft saved. A person still has to send it.");
            onDone();
          } catch (err) {
            setError(err);
          } finally {
            setBusy(false);
          }
        }}
      >
        <Field label="Question for the company" required>
          {(id) => <TextArea id={id} rows={5} value={text} onChange={(e) => setText(e.target.value)} />}
        </Field>
        <fieldset className="cw-radio-group">
          <legend>About these checks</legend>
          <div className="cw-checkgrid">
            {checks.map((c) => (
              <Checkbox
                key={c.id}
                checked={selected.includes(c.check_id)}
                onChange={(e) => setSelected(e.target.checked ? [...selected, c.check_id] : selected.filter((x) => x !== c.check_id))}
                label={`${c.check_id} ${c.name}`}
              />
            ))}
          </div>
        </fieldset>
        {error ? <ErrorState error={error} /> : null}
        <div className="row">
          <Button type="submit" variant="primary" disabled={!text.trim()} loading={busy}>
            Save draft
          </Button>
          <Button variant="ghost" onClick={onDone}>
            Cancel
          </Button>
        </div>
      </form>
    </Panel>
  );
}

export default function CaseQuestionsPage() {
  const { caseId = "" } = useParams();
  const { view } = useSession();
  const names = useNames();
  const questions = useApi(() => api.listQuestions(caseId, { limit: 200 }), [caseId]);
  const checks = useApi(() => api.listCaseChecks(caseId), [caseId]);
  const [adding, setAdding] = useState(false);
  const [refusal, setRefusal] = useState<unknown>(null);
  const canAct = view === "case_officer";

  if (questions.error) return <ErrorState error={questions.error} onRetry={questions.reload} />;
  if (!questions.data) return <SkeletonLines lines={6} />;
  const order: Question["status"][] = ["draft", "sent", "answered", "closed"];
  const items = [...questions.data.items].sort((a, b) => order.indexOf(a.status) - order.indexOf(b.status));
  const open = items.filter((q) => q.status !== "closed");
  const stopped = items.some((q) => q.clock_stopped);

  return (
    <div className="stack">
      {open.length ? (
        <Callout tone="look" icon="lock" title={`${open.length} open question${open.length === 1 ? "" : "s"} block${open.length === 1 ? "s" : ""} promotion`}>
          A question must be closed before the case can be promoted. {stopped ? "The review clock is stopped while the company has a question." : "A draft does not stop the clock until a person sends it."}
        </Callout>
      ) : null}
      {refusal ? <Refusal error={refusal} onDismiss={() => setRefusal(null)} /> : null}
      <div className="row-between">
        <span className="small muted">Agents draft. A person always sends.</span>
        {canAct && !adding ? (
          <Button icon="plus" onClick={() => setAdding(true)}>
            New question
          </Button>
        ) : null}
      </div>
      {adding ? <NewQuestion caseId={caseId} checks={checks.data?.items ?? []} onDone={() => setAdding(false)} /> : null}
      {items.length === 0 && !adding ? (
        <EmptyState title="No questions" icon="send">
          Nothing has been asked of the company on this case.
        </EmptyState>
      ) : null}
      {items.map((q) => (
        <QuestionCard key={q.id} question={q} caseId={caseId} checks={checks.data?.items ?? []} canAct={canAct} names={names} onRefused={setRefusal} />
      ))}
    </div>
  );
}

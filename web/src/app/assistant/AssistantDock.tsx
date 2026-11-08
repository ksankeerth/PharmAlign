// The Knowledge Assistant's side panel. Anyone can ask the knowledge base from any page. On a case page it can also
// answer about that case, with a switch between the two. It explains and never decides: nothing here changes a status.
import { type KeyboardEvent, useEffect, useRef, useState } from "react";
import { api } from "../../api";
import type { AssistantEvent } from "../../api/types";
import { Button, Callout, ErrorState, Icon, Pill, Select, TextArea } from "../../components";
import { useApi } from "../../hooks/useApi";
import { checkStarters } from "../../lib/assistantStarters";
import { CHECK_STATUS_LABEL } from "../../lib/labels";
import { useSession } from "../session";
import { type ChatKey, type Turn, useAssistant } from "./AssistantContext";
import { CitationCard } from "./CitationCard";
import { DraftComment } from "./DraftComment";
import "./assistant.css";

/** Questions the seeded knowledge base answers, and one it can't. */
export const STARTERS = [
  "What long-term stability conditions apply in a hot climate?",
  "Who may operate a pharmaceutical establishment?",
  "Is Federal Law No. 8 of 2019 still in force?",
  "How is a product's sameness with a reference approval judged?",
  "Are vaping liquids with nicotine allowed?",
];

const NOT_AVAILABLE: Record<string, string> = {
  not_checked: "Not available: this was not checked",
  not_saved: "Not available: no reasoning was saved",
  out_of_scope: "Not something I can do",
};

const CASE_VIEWS = ["case_officer", "decision_maker", "auditor"];
type Message = Extract<AssistantEvent, { type: "message" }>;
type NoSource = Extract<AssistantEvent, { type: "no_source" }>;

function Suggestions({ items, onAsk, disabled }: { items: string[]; onAsk: (q: string) => void; disabled: boolean }) {
  if (!items.length) return null;
  return (
    <div className="as-suggest" role="group" aria-label="Suggested questions">
      {items.map((s) => (
        <button key={s} type="button" className="as-chip" onClick={() => onAsk(s)} disabled={disabled}>
          {s}
        </button>
      ))}
    </div>
  );
}

function NoSourceCard({ event }: { event: NoSource }) {
  return (
    <section className="as-nosource" aria-label="No source found">
      <div className="as-nosource-head">
        <Icon name="ban" size={16} />
        <strong>No source found</strong>
      </div>
      {event.detail ? <p>{event.detail}</p> : null}
      <div className="as-tried">
        <div className="as-tried-title">Searches tried</div>
        <ol>
          {event.searches_tried.map((t, i) => (
            <li key={i}>
              <span className="as-tried-what">{t.search}</span>
              <span className="as-tried-result">
                {t.candidates != null ? `${t.candidates} ${t.candidates === 1 ? "passage" : "passages"} looked at. ` : ""}
                {t.result}
              </span>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}

function Answer({ turn, caseId, canOpenNotice, onAsk, busy, inCase, latest }: { turn: Turn; caseId: string | null; canOpenNotice: boolean; onAsk: (q: string) => void; busy: boolean; inCase: boolean; latest: boolean }) {
  const final = turn.events.find((e): e is Message => e.type === "message");
  const none = turn.events.find((e): e is NoSource => e.type === "no_source");
  const streamed = turn.events.flatMap((e) => (e.type === "text_delta" ? [e.text] : [])).join("");
  const others = turn.events.filter((e) => e.type === "not_available" || e.type === "draft_comment");
  const failed = turn.events.find((e): e is Extract<AssistantEvent, { type: "error" }> => e.type === "error");
  const onlyLaw = final ? final.citations.length > 0 && final.citations.every((c) => c.source !== "case_file") : false;
  const suggestions = final?.suggestions ?? none?.suggestions ?? [];
  const waiting = !turn.done && !final && !none && !streamed && !others.length;
  return (
    <div className="as-answer" aria-busy={!turn.done}>
      {final && inCase ? (
        final.basis === "saved" ? (
          <Pill tone="pass" icon="history" size="sm">
            Saved when the check ran
          </Pill>
        ) : onlyLaw ? (
          <Pill tone="neutral" icon="book" size="sm">
            From the knowledge base
          </Pill>
        ) : (
          <Pill tone="look" icon="sparkle" size="sm">
            New reading
          </Pill>
        )
      ) : null}
      {final ? <p className="as-text">{final.text}</p> : streamed && !none ? <p className="as-text">{streamed}<span className="as-caret" aria-hidden="true" /></p> : null}
      {waiting ? (
        <p className="as-thinking muted small">
          <Icon name="loader" size={13} spin /> Searching the knowledge base...
        </p>
      ) : null}
      {final?.citations.length ? (
        <div className="as-cites" role="list" aria-label="Sources">
          {final.citations.map((c) => (
            <div role="listitem" key={c.anchor + c.quote}>
              <CitationCard citation={c} caseId={caseId} canOpenNotice={canOpenNotice} />
            </div>
          ))}
        </div>
      ) : null}
      {final && final.unverified > 0 ? (
        <p className="small muted as-unverified">
          <Icon name="alert-triangle" size={12} /> {final.unverified} citation{final.unverified === 1 ? " was" : "s were"} left out because the quote could not be checked against its source.
        </p>
      ) : null}
      {none ? <NoSourceCard event={none} /> : null}
      {others.map((e, i) =>
        e.type === "not_available" ? (
          <Callout key={i} tone="neutral" icon="info" title={NOT_AVAILABLE[e.reason] ?? "Not available"}>
            {e.detail}
          </Callout>
        ) : e.type === "draft_comment" && caseId ? (
          <DraftComment key={i} caseId={caseId} checkResultId={e.check_id} text={e.text} />
        ) : null,
      )}
      {failed ? (
        <Callout tone="error" title="The assistant could not answer">
          {failed.message}
        </Callout>
      ) : null}
      {turn.error ? <ErrorState error={turn.error} /> : null}
      {turn.done && latest ? <Suggestions items={suggestions} onAsk={onAsk} disabled={busy} /> : null}
    </div>
  );
}

export function AssistantDock() {
  const { open, setOpen, mode, setMode, scope, chats, updateChat } = useAssistant();
  const { view } = useSession();
  const panel = useRef<HTMLElement>(null);
  const composer = useRef<HTMLTextAreaElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const nextId = useRef(1);
  const chatsRef = useRef(chats);
  chatsRef.current = chats;
  const [message, setMessage] = useState("");
  const [focus, setFocus] = useState("");

  const inCase = mode === "case" && scope !== null;
  const key: ChatKey = inCase && scope ? `case:${scope.caseId}` : "knowledge";
  const caseId = inCase && scope ? scope.caseId : null;
  const chat = chats[key];
  const turns = chat?.turns ?? [];
  const busy = turns.some((t) => !t.done);

  const checks = useApi(() => (caseId ? api.listCaseChecks(caseId) : Promise.resolve(null)), [caseId], { enabled: open && Boolean(caseId) });
  const items = checks.data?.items ?? [];
  const subject = items.find((c) => c.id === focus) ?? items.find((c) => ["held", "look", "person_confirms", "error"].includes(c.status));

  useEffect(() => setFocus(scope?.focusCheckId ?? ""), [scope?.focusCheckId, scope?.caseId]);

  // Focus moves into the panel when it opens and goes back to the Ask button when it closes.
  useEffect(() => {
    if (!open) return;
    composer.current?.focus();
    return () => document.getElementById("ask-button")?.focus();
  }, [open]);
  useEffect(() => {
    if (open) composer.current?.focus();
  }, [open, key]);
  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: "end" });
  }, [turns, open]);

  if (!open) return null;

  // The check a question is about. A starter that names the check sends it (the check's code, which the tools take), as does the "About" choice.
  const send = async (text: string, about?: string) => {
    const trimmed = text.trim();
    if (!trimmed || busy) return;
    const id = nextId.current++;
    const here = key;
    const update = (fn: (t: Turn) => Turn) => updateChat(here, (c) => ({ ...c, turns: c.turns.map((t) => (t.id === id ? fn(t) : t)) }));
    updateChat(here, (c) => ({ ...c, turns: [...c.turns, { id, message: trimmed, events: [], done: false }] }));
    setMessage("");
    try {
      let session = chatsRef.current[here]?.session ?? null;
      if (!session) {
        session = (await api.startAssistantSession({ case_id: caseId, language: "en" })).id;
        updateChat(here, (c) => ({ ...c, session }));
      }
      const chosen = items.find((c) => c.id === focus)?.check_id;
      const checkCode = about ?? chosen;
      const asked = here !== "knowledge" && checkCode ? { check_id: checkCode, finding_id: null } : null;
      for await (const ev of api.sendAssistantTurn(session, { message: trimmed, focus: asked })) {
        update((t) => ({ ...t, events: [...t.events, ev] }));
      }
      update((t) => ({ ...t, done: true }));
    } catch (e) {
      update((t) => ({ ...t, done: true, error: e }));
    }
  };

  const onKey = (e: KeyboardEvent<HTMLElement>) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      setOpen(false);
    }
  };

  const caseStarters = subject ? checkStarters(subject.check_id, subject.status) : [];
  const starters = inCase ? ["What blocks promotion?", ...caseStarters, STARTERS[0]] : STARTERS;

  return (
    <aside ref={panel} id="assistant-panel" className="assistant" aria-label="Knowledge Assistant" onKeyDown={onKey}>
      <header className="as-head">
        <span className="as-mark" aria-hidden="true">
          <Icon name="message" size={16} />
        </span>
        <div className="as-head-text">
          <h2>Knowledge Assistant</h2>
          <p className="muted small">Laws, guidelines and this country's pack</p>
        </div>
        <Button variant="ghost" icon="x" aria-label="Close the Knowledge Assistant" onClick={() => setOpen(false)} />
      </header>

      {scope ? (
        <div className="as-modes" role="group" aria-label="What to ask about">
          <button type="button" className="as-mode" aria-pressed={mode === "knowledge"} onClick={() => setMode("knowledge")}>
            <Icon name="book" size={13} /> Knowledge base
          </button>
          <button type="button" className="as-mode" aria-pressed={mode === "case"} onClick={() => setMode("case")}>
            <Icon name="file-text" size={13} /> This case <span className="mono xsmall">{scope.caseId}</span>
          </button>
        </div>
      ) : null}

      <p className="as-rule">
        <Icon name="shield" size={13} />
        <span>It explains and never decides. It can't change a status, waive a check or send anything.</span>
      </p>

      <div className="as-scroll" role="log" aria-live="polite" aria-relevant="additions text" aria-label="Conversation">
        {turns.length === 0 ? (
          <div className="as-empty">
            <h3>{inCase ? `Ask about ${scope?.caseId}` : "Ask the knowledge base"}</h3>
            <p className="muted">
              {inCase
                ? "It reads this case's saved records and the knowledge base, and says which is which."
                : "Answers cite the passage they come from, with its legal status. If nothing in the knowledge base answers, it says so."}
            </p>
            <div className="as-starters" role="group" aria-label="Suggested questions">
              {starters.map((s) => (
                <button key={s} type="button" className="as-starter" onClick={() => void send(s, caseStarters.includes(s) ? subject?.check_id : undefined)}>
                  <span>{s}</span>
                  <Icon name="send" size={13} />
                </button>
              ))}
            </div>
          </div>
        ) : (
          turns.map((t, i) => (
            <div className="as-turn" key={t.id}>
              <div className="as-ask">{t.message}</div>
              <Answer turn={t} caseId={caseId} canOpenNotice={CASE_VIEWS.includes(view ?? "")} onAsk={(q) => void send(q)} busy={busy} inCase={inCase} latest={i === turns.length - 1} />
            </div>
          ))
        )}
        <div ref={endRef} />
      </div>

      <form
        className="as-compose"
        onSubmit={(e) => {
          e.preventDefault();
          void send(message);
        }}
      >
        {inCase ? (
          <div className="as-focus">
            <label htmlFor="as-focus-select" className="xsmall muted">
              About
            </label>
            <Select
              id="as-focus-select"
              value={focus}
              onChange={(e) => setFocus(e.target.value)}
              options={[{ value: "", label: "The whole case" }, ...items.map((c) => ({ value: c.id, label: `${c.check_id} ${c.name} (${CHECK_STATUS_LABEL[c.status]})` }))]}
            />
          </div>
        ) : null}
        <label htmlFor="as-question" className="visually-hidden">
          Your question
        </label>
        <div className="as-box">
          <TextArea
            id="as-question"
            ref={composer}
            rows={2}
            placeholder={inCase ? `Ask about ${scope?.caseId}, or about the law` : "Ask about a law, guideline or requirement"}
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void send(message);
              }
            }}
          />
          <Button type="submit" variant="primary" icon="send" aria-label="Send" loading={busy} disabled={!message.trim()} />
        </div>
        <p className="xsmall muted">Enter sends. Shift and Enter adds a line.</p>
      </form>
    </aside>
  );
}

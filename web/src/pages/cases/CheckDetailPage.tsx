// One check: the question and its source, the finding or typed extraction with the rule
// code evaluated, each quote beside its dossier page, the Challenger's view, any waiver
// or confirmation with its fingerprint, suspect pages and the saved trace.
// Actions send the fingerprint the person read, so a change in between is refused.
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../../api";
import type { CheckDetail, CheckTrace, ConfirmationView, DossierDocument, Evidence, RuleOutcome, WaiverView } from "../../api/types";
import { useSession } from "../../app/session";
import { Button, ButtonLink, Callout, ErrorState, EvidenceQuote, FingerprintNote, Icon, KeyValue, Panel, Pill, SeverityBadge, SkeletonLines, StatusPill, useToast } from "../../components";
import { useApi } from "../../hooks/useApi";
import { formatDateTime, formatRelative, shortFingerprint } from "../../lib/format";
import { humanize } from "../../lib/labels";
import { DossierPageView, SourceCard } from "./EvidenceViewer";
import { ActionDialog, describeSource, evidenceHref, METHOD, parseAnchor, Refusal, useCaseDocs, useNames, useReadFingerprint, useSourceTitles } from "./shared";
import { actsAs } from "../../lib/roles";

const LEGAL_FORCE: Record<CheckDetail["legal_force"], { label: string; severity: string; hint: string }> = {
  binding: { label: "Binding law", severity: "blocking", hint: "Only a Decision Maker may waive it." },
  guidance: { label: "Guidance", severity: "review", hint: "Followed unless a reason is recorded." },
  none: { label: "Local practice", severity: "info", hint: "No legal force of its own." },
};

const AGENT_STATUS: Record<NonNullable<CheckDetail["agent_status"]>, string> = {
  met: "Met",
  attention: "Needs attention",
  missing: "Missing",
  cannot_tell: "Cannot tell",
};

const CHALLENGE: Record<NonNullable<CheckDetail["challenge"]>["verdict"], { tone: "pass" | "held" | "look"; label: string }> = {
  upheld: { tone: "pass", label: "Upheld" },
  disputed: { tone: "held", label: "Disputed" },
  cannot_verify: { tone: "look", label: "Cannot verify" },
};

function RuleBlock({ rule }: { rule: RuleOutcome }) {
  return (
    <div className="cw-rule">
      <div className="row-between">
        <span className="cw-section-title" style={{ margin: 0 }}>
          Rule evaluated by code
        </span>
        {rule.passed ? (
          <Pill tone="pass" icon="check" size="sm">
            Rule passed
          </Pill>
        ) : (
          <Pill tone="held" icon="x" size="sm">
            Rule not met
          </Pill>
        )}
      </div>
      <code className="cw-rule-expr">rule: {rule.rule}</code>
      <dl className="cw-inputs" aria-label="Inputs to the rule">
        {Object.entries(rule.inputs).map(([k, v]) => (
          <div key={k}>
            <dt>{k}</dt>
            <dd>{typeof v === "string" ? v : JSON.stringify(v)}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function EvidencePanel({ check, docs, canMark, onOpened }: { check: CheckDetail; docs?: DossierDocument[]; canMark: boolean; onOpened: () => void }) {
  const all: Evidence[] = [...check.evidence];
  for (const f of check.extracted) for (const e of f.evidence) if (!all.some((x) => x.anchor === e.anchor && x.quote === e.quote)) all.push(e);
  const firstDossier = all.find((e) => parseAnchor(e.anchor).kind === "dossier");
  const [active, setActive] = useState<string | null>(firstDossier?.anchor ?? all[0]?.anchor ?? null);
  useEffect(() => {
    if (active && !all.some((e) => e.anchor === active)) setActive(firstDossier?.anchor ?? all[0]?.anchor ?? null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [check.fingerprint]);

  if (!all.length) {
    return (
      <Panel title="Evidence">
        <p className="small muted">{check.status === "skipped" || check.status === "pending" || check.status === "running" ? "Nothing to show until this check runs." : check.status === "error" ? "No evidence: the check did not finish." : "This check cites no pages. Its rule reads structured values only."}</p>
      </Panel>
    );
  }
  const shown = active ? parseAnchor(active) : null;
  const challengeQuotes = new Set(check.challenge?.verdict === "disputed" ? check.challenge.evidence.map((e) => e.anchor) : []);
  return (
    <Panel
      title="Evidence"
      actions={
        check.evidence_opened_at ? (
          <span className="xsmall muted">Evidence opened {formatRelative(check.evidence_opened_at)}</span>
        ) : canMark ? (
          <Button size="sm" icon="eye" onClick={onOpened}>
            Mark evidence opened
          </Button>
        ) : null
      }
    >
      <div className="cw-evidence-grid">
        <div className="cw-evidence-list">
          {all.map((e, i) => {
            const a = parseAnchor(e.anchor);
            return (
              <EvidenceQuote
                key={`${e.anchor}${e.quote}${i}`}
                evidence={e}
                source={describeSource(e.anchor, docs)}
                tone={challengeQuotes.has(e.anchor) ? "contradicts" : a.kind === "dossier" ? "default" : "reference"}
                onOpen={(anchor) => {
                  setActive(anchor);
                  if (canMark && !check.evidence_opened_at) onOpened();
                }}
              />
            );
          })}
        </div>
        <div className="stack-sm">
          {shown?.kind === "dossier" && shown.page ? (
            <>
              <DossierPageView documentId={shown.doc} page={shown.page} docs={docs} quotes={all.filter((e) => e.anchor === active).map((e) => e.quote)} />
              <Link className="xsmall" to={evidenceHref(check.case_id, shown.doc, shown.page)}>
                Open in the dossier
              </Link>
            </>
          ) : active ? (
            all.filter((e) => e.anchor === active).map((e) => <SourceCard key={e.quote} anchor={e.anchor} quote={e.quote} />)
          ) : null}
        </div>
      </div>
    </Panel>
  );
}

function ClearanceCard({ kind, item, names }: { kind: "Waiver" | "Confirmation"; item: WaiverView | ConfirmationView; names: (id: string) => string }) {
  const note = "reason" in item ? item.reason : item.note;
  return (
    <div className="stack-sm">
      <div className="row">
        <strong className="small">{kind}</strong>
        {item.valid ? (
          <Pill tone="pass" icon="check" size="sm">
            Valid
          </Pill>
        ) : (
          <Pill tone="look" icon="alert-triangle" size="sm">
            No longer counts
          </Pill>
        )}
      </div>
      <KeyValue
        items={[
          { label: "By", value: names(item.by) },
          { label: "When", value: formatDateTime(item.at) },
          ...(note ? [{ label: "reason" in item ? "Reason" : "Note", value: note }] : []),
          { label: "Tied to fingerprint", value: <span title={item.fingerprint}>{shortFingerprint(item.fingerprint, 14)}</span>, mono: true },
        ]}
      />
      {item.valid ? null : <p className="xsmall">The evidence changed after this was recorded, so it stopped counting.</p>}
    </div>
  );
}

function TracePanel({ trace }: { trace: CheckTrace }) {
  const refused = trace.gate_decisions.filter((g) => !g.allowed);
  return (
    <Panel
      title="Trace"
      actions={
        trace.basis === "saved_reasoning" ? (
          <Pill tone="pass" icon="history" size="sm">
            Saved reasoning
          </Pill>
        ) : trace.basis === "rule_trace" ? (
          <Pill tone="pass" icon="settings" size="sm">
            Rule trace
          </Pill>
        ) : (
          <Pill tone="neutral" icon="info" size="sm">
            Not saved
          </Pill>
        )
      }
    >
      <div className="stack">
        {trace.basis === "not_saved" ? <p className="small muted">No reasoning was saved for this run. Nothing is written after the fact.</p> : null}
        {trace.reasoning ? <p className="cw-reasoning">{trace.reasoning}</p> : null}
        {trace.basis === "rule_trace" && trace.rule_outcome ? <p className="small">Code evaluated the rule above with those inputs. No model decided the outcome.</p> : null}

        {refused.length ? (
          <Callout tone="held" title={`The gate refused ${refused.length} output${refused.length === 1 ? "" : "s"} in this run`} icon="shield">
            {refused[0].detail ?? humanize(refused[0].rule)}
          </Callout>
        ) : null}

        {trace.gate_decisions.length ? (
          <div>
            <h3 className="cw-section-title">Gate decisions</h3>
            <div className="cw-trace-wrap">
              <table className="cw-trace-table">
                <thead>
                  <tr>
                    <th scope="col">Decision</th>
                    <th scope="col">Checkpoint</th>
                    <th scope="col">Rule</th>
                    <th scope="col">Detail</th>
                  </tr>
                </thead>
                <tbody>
                  {trace.gate_decisions.map((g, i) => (
                    <tr key={i} className={g.allowed ? undefined : "cw-trace-refused"}>
                      <td className="nowrap">
                        <Icon name={g.allowed ? "check" : "x"} size={13} /> <strong>{g.allowed ? "Allowed" : "Refused"}</strong>
                      </td>
                      <td>{humanize(g.checkpoint)}</td>
                      <td>
                        <code className="xsmall">{g.rule}</code>
                      </td>
                      <td>{g.detail}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ) : null}

        {trace.tool_calls.length ? (
          <div>
            <h3 className="cw-section-title">Tool calls</h3>
            <div className="cw-trace-wrap">
              <table className="cw-trace-table">
                <thead>
                  <tr>
                    <th scope="col">Tool</th>
                    <th scope="col">What it did</th>
                  </tr>
                </thead>
                <tbody>
                  {trace.tool_calls.map((t, i) => (
                    <tr key={i} className={t.denied || t.error ? "cw-trace-refused" : undefined}>
                      <td>
                        <code className="xsmall">{t.tool}</code>
                      </td>
                      <td>
                        {t.summary}
                        {t.denied ? " (denied)" : ""}
                        {t.error ? ` (${t.error})` : ""}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ) : null}

        {trace.model_calls.length ? (
          <div>
            <h3 className="cw-section-title">Model calls</h3>
            <div className="cw-trace-wrap">
              <table className="cw-trace-table">
                <thead>
                  <tr>
                    <th scope="col">Model</th>
                    <th scope="col">Input tokens</th>
                    <th scope="col">Output tokens</th>
                    <th scope="col">Duration</th>
                  </tr>
                </thead>
                <tbody>
                  {trace.model_calls.map((m, i) => (
                    <tr key={i}>
                      <td>
                        <code className="xsmall">{m.model}</code>
                        {m.fallback ? " (fallback)" : ""}
                      </td>
                      <td className="num">{m.input_tokens}</td>
                      <td className="num">{m.output_tokens}</td>
                      <td className="num">{(m.duration_ms / 1000).toFixed(1)} s</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ) : null}

        <KeyValue
          columns={2}
          items={[
            { label: "Run", value: trace.run_id, mono: true },
            { label: "Agent version", value: trace.agent_version, mono: true },
            { label: "Prompt hash", value: trace.prompt_hash, mono: true },
            { label: "Pack version", value: trace.pack_version_id, mono: true },
            { label: "Library snapshot", value: trace.kb_snapshot_id, mono: true },
            { label: "Usage", value: trace.usage ? `${trace.usage.model_calls} model calls, ${trace.usage.tool_calls} tool calls, ${trace.usage.input_tokens + trace.usage.output_tokens} tokens` : null },
          ]}
        />
      </div>
    </Panel>
  );
}

type Dialog = "confirm" | "waive" | "recheck" | "comment" | null;

export default function CheckDetailPage() {
  const { caseId = "", checkResultId = "" } = useParams();
  const { view } = useSession();
  const names = useNames();
  const toast = useToast();
  const check = useApi(() => api.getCheck(checkResultId), [checkResultId]);
  const trace = useApi(() => api.getCheckTrace(checkResultId), [checkResultId]);
  const docs = useCaseDocs(caseId);
  const detail = useApi(() => api.getCase(caseId), [caseId]);
  const threads = useApi(() => api.listThreads(caseId), [caseId]);
  const packId = detail.data?.pack_version_id;
  const pack = useApi(() => (packId ? api.getPackVersion(packId) : Promise.resolve(null)), [packId], { live: false });
  const sourceOf = useSourceTitles(detail.data?.kb_snapshot_id);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [refusal, setRefusal] = useState<unknown>(null);
  const { fingerprint: read, stale, accept } = useReadFingerprint(check.data?.fingerprint, checkResultId);

  if (check.error) return <ErrorState error={check.error} onRetry={check.reload} />;
  if (!check.data) return <SkeletonLines lines={10} />;
  const c = check.data;
  const old = !c.is_current;
  const canAct = (actsAs(view, "case_officer") || actsAs(view, "decision_maker")) && !old;
  const frozen = ["signed", "recording", "closed"].includes(detail.data?.status ?? "");
  const canConfirm = actsAs(view, "case_officer") && !old && !frozen && (c.status === "person_confirms" || c.status === "look");
  const canWaive = canAct && !frozen && (c.status === "held" || c.status === "look" || c.status === "person_confirms");
  const canRecheck = canAct && !frozen && c.status !== "running" && (c.method === "agent" || c.method === "typed" || c.status === "error");
  const waiveBy = pack.data?.document.checks.find((d) => d.id === c.check_id)?.waive_by ?? (c.legal_force === "binding" ? "decision_maker" : "case_officer");
  const force = LEGAL_FORCE[c.legal_force];
  const onThis = threads.data?.items.filter((t) => t.target.type === "check" && t.target.ref === c.id) ?? [];
  const openThreads = onThis.filter((t) => t.status === "open");

  const markOpened = () => {
    api.recordEvidenceOpened(c.id).catch((e: unknown) => toast.error(e instanceof Error ? e.message : "Could not record that."));
  };
  const reloadRead = () => {
    accept();
    setRefusal(null);
    check.reload();
  };

  return (
    <div className="stack">
      <div className="row-between">
        <div className="row">
          <Link to={`/cases/${caseId}/checks`} className="small">
            <Icon name="chevron-left" size={12} /> All checks
          </Link>
          <code>{c.check_id}</code>
          <strong>{c.name}</strong>
          <StatusPill status={c.status} />
          {c.blocking ? <SeverityBadge severity="blocking" /> : <SeverityBadge severity="info" label="Advisory" />}
        </div>
        <span className="xsmall muted num">
          Generation {c.generation}, attempt {c.attempt}, updated {formatRelative(c.updated_at)}
        </span>
      </div>

      {old ? (
        <Callout tone="neutral" icon="history" title="Superseded result">
          A later generation or attempt replaced this one, so it no longer counts. It is kept so the history can be read. <Link to={`/cases/${caseId}/checks/${c.current_result_id ?? c.id}`}>Open the current result</Link>.
        </Callout>
      ) : null}
      {c.status === "error" ? (
        <Callout tone="error" title="This check could not run">
          It is not a failed check and says nothing about the file. {c.summary}
        </Callout>
      ) : null}
      {c.status === "skipped" ? (
        <Callout tone="neutral" icon="skip" title="Skipped">
          {c.summary}
        </Callout>
      ) : null}
      {c.no_source_found ? (
        <Callout tone="look" icon="search" title="No source found">
          The agent searched and found no in-force source for this question, so it did not guess one. A person decides, and a gap candidate went to the Process Owner.
        </Callout>
      ) : null}
      {c.suspect_pages.length ? (
        <Callout tone="held" icon="eye-off" title="Suspect pages">
          Hidden text was found on page{c.suspect_pages.length === 1 ? "" : "s"} {c.suspect_pages.join(", ")}. It was removed from what agents read. <Link to={`/cases/${caseId}/evidence`}>Review it in Evidence</Link>.
        </Callout>
      ) : null}

      <div className="cw-split">
        <div className="stack">
          <Panel title="Question">
            <div className="stack-sm">
              <p className="cw-question">{c.question}</p>
              <div className="row small">
                <span title={force.hint}>
                  <SeverityBadge severity={force.severity} label={force.label} />
                </span>
                {c.source ? (
                  <span className="icon-line">
                    <Icon name="book" size={13} />
                    <span>
                      <code>{c.source.doc_id}</code>
                      {sourceOf(c.source.doc_id) ? ` ${sourceOf(c.source.doc_id)?.title}` : ""}
                      {c.source.clause ? `, clause ${c.source.clause}` : ""}
                    </span>
                  </span>
                ) : (
                  <span className="muted">No source clause: local practice or a process rule.</span>
                )}
                <span className="muted">{c.may_pass_alone ? "May pass without a person." : "An agent may not pass this alone."}</span>
              </div>
            </div>
          </Panel>

          <Panel title="Finding">
            <div className="stack-sm">
              <div className="row small">
                <span className="strong">{METHOD[c.method].label}</span>
                <span className="muted">{METHOD[c.method].hint}</span>
              </div>
              {c.agent_status ? (
                <div className="row small">
                  <span>{c.agent ? names(c.agent) : "Agent"} says:</span>
                  <Pill tone={c.agent_status === "met" ? "pass" : c.agent_status === "cannot_tell" ? "neutral" : "look"} size="sm">
                    {AGENT_STATUS[c.agent_status]}
                  </Pill>
                </div>
              ) : null}
              {c.status !== "error" && c.status !== "skipped" && c.summary ? <p className="small" style={{ margin: 0 }}>{c.summary}</p> : null}
              {c.extracted.length ? (
                <div className="cw-trace-wrap">
                  <h3 className="cw-section-title">Typed extraction</h3>
                  <table className="cw-trace-table">
                    <thead>
                      <tr>
                        <th scope="col">Value</th>
                        <th scope="col">Extracted</th>
                        <th scope="col">Status</th>
                        <th scope="col">Where</th>
                      </tr>
                    </thead>
                    <tbody>
                      {c.extracted.map((f) => (
                        <tr key={f.key}>
                          <td>
                            <code className="xsmall">{f.key}</code>
                          </td>
                          <td>
                            <strong>{f.value ?? "None"}</strong>
                            {f.unit && f.value && !f.value.includes(f.unit) ? ` ${f.unit}` : ""}
                          </td>
                          <td>{humanize(f.status)}</td>
                          <td className="xsmall">{f.evidence[0] ? describeSource(f.evidence[0].anchor, docs.data?.items) : "None"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : null}
              {c.rule_outcome ? <RuleBlock rule={c.rule_outcome} /> : null}
              {c.question_for_company ? (
                <Callout tone="accent" icon="send" title="Suggested question for the company">
                  {c.question_for_company} <Link to={`/cases/${caseId}/questions`}>See questions</Link>
                </Callout>
              ) : null}
            </div>
          </Panel>

          <EvidencePanel check={c} docs={docs.data?.items} canMark={canAct} onOpened={markOpened} />

          {c.challenge ? (
            <Panel
              title="Challenger"
              actions={
                c.challenge.counted ? (
                  <Pill tone="accent" icon="check" size="sm">
                    Counted
                  </Pill>
                ) : (
                  <Pill tone="neutral" icon="ban" size="sm">
                    Did not count
                  </Pill>
                )
              }
            >
              <div className="stack-sm">
                <div className="row small">
                  <span>Verdict:</span>
                  <Pill tone={CHALLENGE[c.challenge.verdict].tone} size="sm">
                    {CHALLENGE[c.challenge.verdict].label}
                  </Pill>
                </div>
                <p className="small" style={{ margin: 0 }}>{c.challenge.reason}</p>
                <p className="xsmall muted" style={{ margin: 0 }}>
                  {c.challenge.counted ? "This verdict affected the status of the check." : "The Challenger quoted nothing that could be verified on its page, so this verdict did not affect the status."}
                </p>
                {c.challenge.evidence.map((e) => (
                  <EvidenceQuote key={e.anchor + e.quote} evidence={e} source={describeSource(e.anchor, docs.data?.items)} tone={c.challenge?.verdict === "disputed" ? "contradicts" : "default"} />
                ))}
              </div>
            </Panel>
          ) : null}

          {trace.data ? <TracePanel trace={trace.data} /> : trace.error ? <ErrorState error={trace.error} onRetry={trace.reload} /> : <SkeletonLines lines={4} />}
        </div>

        <aside className="stack cw-rail" aria-label="Actions on this check">
          {refusal ? <Refusal error={refusal} read={read} checkHref={(id) => `/cases/${caseId}/checks/${id}`} onReload={reloadRead} onDismiss={() => setRefusal(null)} /> : null}

          {!old ? (
            <Panel title="Actions">
              <div className="stack-sm">
                <FingerprintNote fingerprint={read} current={stale ? c.fingerprint : null} />
                {stale && !refusal ? (
                  <Button size="sm" icon="refresh" onClick={reloadRead}>
                    I read the latest version
                  </Button>
                ) : null}
                {canConfirm ? (
                  <Button variant="primary" icon="user-check" onClick={() => setDialog("confirm")}>
                    Confirm
                  </Button>
                ) : null}
                {canWaive ? (
                  <Button icon="ban" onClick={() => setDialog("waive")}>
                    Waive with a reason
                  </Button>
                ) : null}
                {canRecheck ? (
                  <Button icon="refresh" onClick={() => setDialog("recheck")}>
                    {c.status === "error" ? "Re-run" : "Re-check with a comment"}
                  </Button>
                ) : null}
                {canAct ? (
                  <Button icon="message" onClick={() => setDialog("comment")}>
                    Comment
                  </Button>
                ) : null}
                {canAct && !c.evidence_opened_at && c.evidence.length ? (
                  <p className="xsmall muted" style={{ margin: 0 }}>
                    Evidence not opened yet. Open a quote before you confirm.
                  </p>
                ) : null}
                {c.status === "running" ? <p className="xsmall muted" style={{ margin: 0 }}>Running. Actions return when it finishes.</p> : null}
                {frozen ? <p className="xsmall muted" style={{ margin: 0 }}>This case is signed, so its checks are frozen.</p> : null}
                {actsAs(view, "auditor") ? <p className="xsmall muted" style={{ margin: 0 }}>Read-only in the Auditor view.</p> : null}
              </div>
            </Panel>
          ) : null}

          {c.confirmation || c.waiver ? (
            <Panel title="Clearance">
              <div className="stack">
                {c.confirmation ? <ClearanceCard kind="Confirmation" item={c.confirmation} names={names} /> : null}
                {c.waiver ? <ClearanceCard kind="Waiver" item={c.waiver} names={names} /> : null}
              </div>
            </Panel>
          ) : null}

          <Panel
            title="Threads on this check"
            actions={
              <ButtonLink size="sm" variant="ghost" to={`/cases/${caseId}/threads`}>
                All threads
              </ButtonLink>
            }
          >
            {onThis.length ? (
              <div className="stack-sm small">
                {openThreads.length ? <p className="strong" style={{ margin: 0 }}>{openThreads.length} open. Open threads block promotion.</p> : null}
                {onThis.map((t) => (
                  <div key={t.id} className="row">
                    <Pill tone={t.status === "open" ? "look" : "neutral"} icon={t.status === "open" ? "message" : "check"} size="sm">
                      {t.status === "open" ? "Open" : "Resolved"}
                    </Pill>
                    <span className="cw-clip">{t.comments.at(-1)?.text.slice(0, 80)}</span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="small muted" style={{ margin: 0 }}>No comments on this check.</p>
            )}
          </Panel>
        </aside>
      </div>

      <ActionDialog
        open={dialog === "confirm"}
        onClose={() => setDialog(null)}
        title={`Confirm ${c.check_id}`}
        confirmLabel="Confirm"
        summary={
          <div className="stack-sm">
            <p className="small" style={{ margin: 0 }}>Your name and this fingerprint go on the record. If the evidence changes later, this confirmation stops counting.</p>
            <FingerprintNote fingerprint={read} />
            {!c.evidence_opened_at && c.evidence.length ? <Callout tone="look" title="You have not opened the evidence">Open a quote first if you have not read it elsewhere.</Callout> : null}
          </div>
        }
        text={{ label: "Note (optional)" }}
        acknowledge="I read the evidence behind this check."
        onConfirm={async (note) => {
          await api.confirmCheck(c.id, { fingerprint: read, note: note || null });
          toast.success(`${c.check_id} confirmed. The Coordinator is updating the brief.`);
        }}
        onRefused={setRefusal}
      />
      <ActionDialog
        open={dialog === "waive"}
        onClose={() => setDialog(null)}
        title={`Waive ${c.check_id}`}
        confirmLabel="Waive"
        variant="danger"
        summary={
          <div className="stack-sm">
            <p className="small" style={{ margin: 0 }}>A waiver clears a check with a named person and a reason. It stops counting if the evidence changes.</p>
            {waiveBy === "decision_maker" && view !== "decision_maker" ? (
              <Callout tone="look" icon="lock" title={c.legal_force === "binding" ? "Binding law: a Decision Maker must waive this" : "The pack says only a Decision Maker may waive this"}>
                You can try. The server will refuse and say why.
              </Callout>
            ) : null}
            {waiveBy === "nobody" ? <Callout tone="held" icon="lock" title="The pack says nobody may waive this check" /> : null}
            <FingerprintNote fingerprint={read} />
          </div>
        }
        text={{ label: "Reason", required: true, hint: "Saved in the audit log and shown in the signing package." }}
        onConfirm={async (reason) => {
          await api.waiveCheck(c.id, { fingerprint: read, reason });
          toast.success(`${c.check_id} waived`);
        }}
        onRefused={setRefusal}
      />
      <ActionDialog
        open={dialog === "recheck"}
        onClose={() => setDialog(null)}
        title={c.status === "error" ? `Re-run ${c.check_id}` : `Re-check ${c.check_id}`}
        confirmLabel={c.status === "error" ? "Re-run" : "Re-check"}
        summary={<p className="small" style={{ margin: 0 }}>The agent runs again with your comment as context. The fingerprint changes, so earlier confirmations and waivers on this check stop counting.</p>}
        text={{ label: "Comment for the agent", required: true, initial: c.status === "error" ? "Re-run after a failure that was not about the file." : "", rows: 4 }}
        onConfirm={async (comment) => {
          await api.recheckCheck(c.id, { comment });
          toast.info(`Re-running ${c.check_id}`);
        }}
        onRefused={setRefusal}
      />
      <ActionDialog
        open={dialog === "comment"}
        onClose={() => setDialog(null)}
        title={`Comment on ${c.check_id}`}
        confirmLabel="Post comment"
        summary={<p className="small" style={{ margin: 0 }}>This opens a review thread. It blocks promotion until someone resolves it.</p>}
        text={{ label: "Comment", required: true, rows: 4 }}
        onConfirm={async (text) => {
          await api.createThread(caseId, { target: { type: "check", ref: c.id }, text });
          toast.success("Comment posted");
        }}
      />
    </div>
  );
}

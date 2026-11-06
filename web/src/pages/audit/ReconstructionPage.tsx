// Case reconstruction: everything that happened to one case, read from saved records.
// Nothing is re-run here. Events are merged in time order; the other tabs show each
// kind of record in full (checks with their traces and gate decisions, recommendations
// by version, threads, questions, the decision with its signers, the government proposal).
import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { api, type CaseReconstruction, type CheckDetail, type CheckTrace } from "../../api";
import { ButtonLink, Callout, CaseStatusPill, Checkbox, type Column, EmptyState, ErrorState, KeyValue, PageHeader, Panel, Pill, SkeletonLines, StatusPill, Table, Tabs, Timeline, type TimelineItem } from "../../components";
import { useApi } from "../../hooks/useApi";
import { formatDateTime } from "../../lib/format";
import { CHECK_STATUS_LABEL, humanize, RECOMMENDATION_LABEL, ROLE_LABEL } from "../../lib/labels";
import { ActorChip, type ActorType, usePeopleNames } from "./actors";
import { changesOf } from "./AuditLogPage";
import "./audit.css";

type Kind = "run" | "check" | "gate" | "recommendation" | "review" | "question" | "decision" | "proposal";
const KIND_LABEL: Record<Kind, string> = {
  run: "Pipeline runs",
  check: "Checks",
  gate: "Gate decisions",
  recommendation: "Recommendations",
  review: "Threads",
  question: "Questions",
  decision: "Decision",
  proposal: "Government proposal",
};
const ALL_KINDS = Object.keys(KIND_LABEL) as Kind[];

type Ev = TimelineItem & { kind: Kind };

function checkTone(s: CheckDetail["status"]): TimelineItem["tone"] {
  if (s === "pass" || s === "confirmed" || s === "waived") return "pass";
  if (s === "look" || s === "person_confirms" || s === "stale") return "look";
  if (s === "held") return "held";
  if (s === "error") return "error";
  return "default";
}

function buildEvents(r: CaseReconstruction): Ev[] {
  const out: Ev[] = [];
  for (const run of r.pipeline_runs) {
    out.push({ kind: "run", id: `run-${run.id}-s`, at: run.started_at, actorType: "system", actorId: "pipeline", summary: <>Pipeline run <code>{run.id}</code> started (sequence {run.sequence}, generation {run.generation})</> });
    if (run.finished_at) out.push({ kind: "run", id: `run-${run.id}-f`, at: run.finished_at, actorType: "system", actorId: "pipeline", summary: <>Pipeline run <code>{run.id}</code> {humanize(run.status).toLowerCase()}</> });
  }
  for (const c of r.checks) {
    const actorType: ActorType = c.method === "agent" ? "agent" : c.method === "person" ? "person" : "system";
    out.push({
      kind: "check",
      id: `chk-${c.id}`,
      at: c.updated_at,
      actorType,
      actorId: c.method === "agent" ? (c.agent ?? "agent") : c.method === "person" ? "officer" : "rules",
      tone: checkTone(c.status),
      summary: (
        <>
          <strong>{c.check_id}</strong> {c.name}: {CHECK_STATUS_LABEL[c.status]}
          {c.summary ? <span className="muted"> - {c.summary}</span> : null}
        </>
      ),
    });
  }
  for (const t of r.traces)
    (t.gate_decisions ?? []).forEach((g, i) =>
      out.push({
        kind: "gate",
        id: `gate-${t.check_result_id}-${i}`,
        at: g.at,
        actorType: "system",
        actorId: "gate",
        tone: g.allowed ? "default" : "held",
        summary: (
          <>
            Gate at {humanize(g.checkpoint).toLowerCase()}: <strong>{g.allowed ? "allowed" : "refused"}</strong> by <code>{g.rule}</code>
            {g.detail ? <span className="muted"> - {g.detail}</span> : null}
          </>
        ),
      }),
    );
  for (const rec of r.recommendations)
    out.push({
      kind: "recommendation",
      id: `rec-${rec.version}`,
      at: rec.created_at,
      actorType: "agent",
      actorId: "coordinator",
      summary: (
        <>
          Recommendation version {rec.version}: <strong>{RECOMMENDATION_LABEL[rec.choice] ?? humanize(rec.choice)}</strong>
          {rec.is_current ? <span className="muted"> (current)</span> : <span className="muted"> (replaced)</span>}
        </>
      ),
    });
  for (const th of r.threads) {
    out.push({ kind: "review", id: `th-${th.id}`, at: th.created_at, actorType: th.comments[0]?.author_type ?? "person", actorId: th.comments[0]?.author_id ?? "unknown", summary: <>Thread opened on {th.target.type} <code>{th.target.ref}</code>{th.blocks_promotion ? " (blocks promotion)" : ""}</> });
    th.comments.forEach((c, i) => {
      if (i > 0) out.push({ kind: "review", id: `thc-${c.id}`, at: c.at, actorType: c.author_type, actorId: c.author_id, summary: <>Comment on <code>{th.id}</code>: {c.text}</> });
    });
    if (th.resolved_at) out.push({ kind: "review", id: `thr-${th.id}`, at: th.resolved_at, actorType: "person", actorId: th.resolved_by ?? "unknown", summary: <>Thread <code>{th.id}</code> resolved</> });
  }
  for (const q of r.questions)
    if (q.sent_at) out.push({ kind: "question", id: `q-${q.id}`, at: q.sent_at, actorType: "person", actorId: q.sent_by ?? "unknown", summary: <>Question <code>{q.id}</code> sent to the company ({humanize(q.status).toLowerCase()}){q.clock_stopped ? ", clock stopped" : ""}</> });
  if (r.decision) {
    for (const s of r.decision.signers) out.push({ kind: "decision", id: `sig-${r.decision.id}-${s.person_id}`, at: s.signed_at, actorType: "person", actorId: s.person_id, tone: "pass", summary: <>Signed decision <code>{r.decision.id}</code> as {(ROLE_LABEL as Record<string, string>)[s.role] ?? humanize(s.role)}</> });
  }
  if (r.proposal) {
    out.push({ kind: "proposal", id: `prop-c-${r.proposal.id}`, at: r.proposal.created_at, actorType: "connector", actorId: "government-record", summary: <>Government proposal <code>{r.proposal.id}</code> queued: {humanize(r.proposal.action).toLowerCase()}</> });
    if (r.proposal.updated_at !== r.proposal.created_at) out.push({ kind: "proposal", id: `prop-u-${r.proposal.id}`, at: r.proposal.updated_at, actorType: "connector", actorId: "government-record", tone: r.proposal.state === "rejected" ? "held" : r.proposal.state === "acknowledged" ? "pass" : "look", summary: <>Government proposal {humanize(r.proposal.state).toLowerCase()} after {r.proposal.attempts ?? 0} attempt(s)</> });
  }
  return out.sort((a, b) => a.at.localeCompare(b.at));
}

function Section({ title, children, count }: { title: string; children: React.ReactNode; count?: number }) {
  return (
    <section className="stack-sm" aria-label={title}>
      <h3 style={{ margin: 0, fontSize: "var(--fs-md)" }}>
        {title}
        {count !== undefined ? <span className="muted"> ({count})</span> : null}
      </h3>
      {children}
    </section>
  );
}

function CheckRecord({ c, trace }: { c: CheckDetail; trace?: CheckTrace }) {
  const gates = trace?.gate_decisions ?? [];
  const refused = gates.filter((g) => !g.allowed).length;
  return (
    <details className="recon-details">
      <summary>
        <code>{c.check_id}</code>
        <strong>{c.name}</strong>
        <StatusPill status={c.status} size="sm" />
        <Pill tone="neutral" size="sm">{humanize(c.method)}</Pill>
        {refused ? <Pill tone="held" icon="shield" size="sm">{refused} gate refusal{refused === 1 ? "" : "s"}</Pill> : null}
        <span className="muted small">Attempt {c.attempt}, generation {c.generation}</span>
      </summary>
      <div className="recon-details-body">
        <KeyValue
          columns={2}
          items={[
            { label: "Question", value: c.question },
            { label: "Result", value: c.summary },
            { label: "Updated", value: formatDateTime(c.updated_at) },
            { label: "Fingerprint", value: c.fingerprint, mono: true },
          ]}
        />
        {c.rule_outcome ? (
          <div>
            <div className="muted small">Rule outcome ({c.rule_outcome.passed ? "passed" : "not passed"})</div>
            <pre>{`${c.rule_outcome.rule}\n${JSON.stringify(c.rule_outcome.inputs, null, 2)}`}</pre>
          </div>
        ) : null}
        {c.evidence?.length ? (
          <div>
            <div className="muted small">Evidence cited</div>
            <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
              {c.evidence.map((e, i) => (
                <li key={i} className="small">
                  “{e.quote}” <code className="xsmall">{e.anchor}</code>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        <div>
          <div className="muted small">Saved trace</div>
          {trace ? (
            <div className="stack-sm">
              <div className="small">
                {trace.basis === "saved_reasoning" ? "Reasoning saved at the time." : trace.basis === "rule_trace" ? "A rule ran; its inputs and outcome are the trace." : "No reasoning was saved for this check."}
              </div>
              {trace.reasoning ? <pre>{trace.reasoning}</pre> : null}
              <KeyValue
                columns={3}
                items={[
                  { label: "Agent version", value: trace.agent_version, mono: true },
                  { label: "Pack version", value: trace.pack_version_id, mono: true },
                  { label: "Knowledge snapshot", value: trace.kb_snapshot_id, mono: true },
                ]}
              />
              {trace.tool_calls?.length ? (
                <div className="small">
                  <strong>Tool calls: </strong>
                  {trace.tool_calls.map((t, i) => (
                    <span key={i}>
                      {i ? "; " : ""}
                      {t.tool}
                      {t.denied ? " (denied)" : ""}
                      {t.error ? ` (error: ${t.error})` : ""}
                    </span>
                  ))}
                </div>
              ) : null}
              {trace.model_calls?.length ? (
                <div className="small">
                  <strong>Model calls: </strong>
                  {trace.model_calls.map((m, i) => (
                    <span key={i}>
                      {i ? "; " : ""}
                      {m.model} ({m.input_tokens} in, {m.output_tokens} out{m.fallback ? ", fallback" : ""})
                    </span>
                  ))}
                </div>
              ) : null}
              <div>
                <div className="small">
                  <strong>Gate decisions</strong> ({gates.length})
                </div>
                {gates.length ? (
                  <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
                    {gates.map((g, i) => (
                      <li key={i} className="small">
                        <span className={g.allowed ? "gate-allowed" : "gate-refused"}>{g.allowed ? "Allowed" : "Refused"}</span> at {humanize(g.checkpoint).toLowerCase()} by <code>{g.rule}</code>
                        {g.detail ? <span className="muted"> - {g.detail}</span> : null} <span className="muted xsmall">{formatDateTime(g.at)}</span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <div className="muted small">None recorded.</div>
                )}
              </div>
            </div>
          ) : (
            <div className="muted small">No trace was saved.</div>
          )}
        </div>
      </div>
    </details>
  );
}

export default function ReconstructionPage() {
  const { caseId = "" } = useParams();
  const { data, error, loading, reload } = useApi(() => api.reconstructCase(caseId), [caseId]);
  const { name } = usePeopleNames();
  const [tab, setTab] = useState("order");
  const [kinds, setKinds] = useState<Set<Kind>>(new Set(ALL_KINDS));
  const events = useMemo(() => (data ? buildEvents(data) : []), [data]);
  const shown = events.filter((e) => kinds.has(e.kind));
  const crumbs = [{ label: "Audit", to: "/audit" }, { label: "Case reconstruction", to: "/audit/cases" }, { label: caseId }];

  if (error) return (
    <div className="stack">
      <PageHeader title={caseId} breadcrumbs={crumbs} />
      <ErrorState error={error} onRetry={reload} />
    </div>
  );
  if (!data) return (
    <div className="stack">
      <PageHeader title={caseId} breadcrumbs={crumbs} eyebrow="Reconstruction" />
      {loading ? <SkeletonLines lines={6} /> : null}
    </div>
  );

  const r = data;
  const traceFor = new Map(r.traces.map((t) => [t.check_result_id, t]));
  const gateTotal = r.traces.reduce((n, t) => n + (t.gate_decisions?.length ?? 0), 0);
  const refusedTotal = r.traces.reduce((n, t) => n + (t.gate_decisions ?? []).filter((g) => !g.allowed).length, 0);
  const toggle = (k: Kind) =>
    setKinds((s) => {
      const n = new Set(s);
      if (n.has(k)) n.delete(k);
      else n.add(k);
      return n;
    });

  const auditCols: Column<(typeof r.audit_events)[number]>[] = [
    { key: "at", header: "When", render: (e) => <span className="nowrap">{formatDateTime(e.at)}</span> },
    { key: "actor", header: "Actor", render: (e) => <ActorChip type={e.actor_type as ActorType} id={e.actor_id} name={name(e.actor_type, e.actor_id)} /> },
    { key: "action", header: "Action", render: (e) => humanize(e.action) },
    { key: "obj", header: "Object", render: (e) => <code className="small">{e.object_id}</code>, hideBelow: 900 },
    { key: "hash", header: "Row hash", render: (e) => <span className="hash">{(e.row_hash ?? "").slice(0, 18)}</span>, hideBelow: 1000 },
    { key: "change", header: "Change", render: (e) => <span className="small muted">{changesOf(e.before, e.after).length} field(s)</span>, hideBelow: 1100 },
  ];

  return (
    <div className="stack">
      <PageHeader
        eyebrow="Reconstruction"
        title={r.case.id}
        breadcrumbs={crumbs}
        status={<CaseStatusPill status={r.case.status} />}
        subtitle={`${humanize(r.case.case_type)} · sequence ${r.case.sequence} · pack ${r.case.pack_version_id} · knowledge snapshot ${r.case.kb_snapshot_id}`}
        actions={<ButtonLink to={`/cases/${r.case.id}`} variant="secondary" icon="external">Open case page</ButtonLink>}
      />
      <Callout tone="accent" title="This shows saved records. Nothing is re-run.">
        Every item below was read from what the system stored when it happened. No check, agent or rule is run again, so what you see cannot differ from what was recorded at the time.
      </Callout>

      <div className="stat-row">
        {[
          ["Pipeline runs", r.pipeline_runs.length],
          ["Checks", r.checks.length],
          [refusedTotal ? `Gate decisions (${refusedTotal} refused)` : "Gate decisions", gateTotal],
          ["Recommendation versions", r.recommendations.length],
          ["Threads", r.threads.length],
          ["Questions", r.questions.length],
          ["Audit rows", r.audit_events.length],
        ].map(([l, v]) => (
          <div className="stat" key={String(l)}>
            <div className="stat-value">{v}</div>
            <div className="stat-label">{l}</div>
          </div>
        ))}
      </div>

      <Tabs
        label="Reconstruction sections"
        value={tab}
        onChange={setTab}
        tabs={[
          { id: "order", label: "In order", badge: events.length },
          { id: "checks", label: "Runs and checks", badge: r.checks.length },
          { id: "recs", label: "Recommendations", badge: r.recommendations.length },
          { id: "review", label: "Threads and questions", badge: r.threads.length + r.questions.length },
          { id: "decision", label: "Decision and proposal" },
          { id: "audit", label: "Audit rows", badge: r.audit_events.length },
        ]}
      >
        {tab === "order" ? (
          <Panel title="Everything that happened, oldest first">
            <fieldset style={{ border: 0, padding: 0, margin: "0 0 var(--sp-4)" }}>
              <legend className="muted small" style={{ marginBottom: "var(--sp-2)" }}>
                Show
              </legend>
              <div className="row" style={{ gap: "var(--sp-4)" }}>
                {ALL_KINDS.map((k) => (
                  <Checkbox key={k} label={KIND_LABEL[k]} checked={kinds.has(k)} onChange={() => toggle(k)} />
                ))}
              </div>
            </fieldset>
            <Timeline items={shown} resolveActor={name} empty={<EmptyState title="Nothing to show" icon="history">Turn on a kind of record above.</EmptyState>} />
          </Panel>
        ) : null}

        {tab === "checks" ? (
          <div className="stack">
            <Panel title={`Pipeline runs (${r.pipeline_runs.length})`} padded={false}>
              <Table
                caption="Pipeline runs"
                dense
                rows={r.pipeline_runs}
                rowKey={(x) => x.id}
                columns={[
                  { key: "id", header: "Run", render: (x) => <code>{x.id}</code> },
                  { key: "seq", header: "Sequence", render: (x) => x.sequence, align: "right" },
                  { key: "gen", header: "Generation", render: (x) => x.generation, align: "right" },
                  { key: "status", header: "Status", render: (x) => <Pill tone={x.status === "completed" ? "pass" : x.status === "superseded" ? "neutral" : "accent"} size="sm">{humanize(x.status)}</Pill> },
                  { key: "stages", header: "Stages", render: (x) => x.stages.map((s) => s.label).join(", "), hideBelow: 900 },
                  { key: "started", header: "Started", render: (x) => formatDateTime(x.started_at) },
                  { key: "finished", header: "Finished", render: (x) => (x.finished_at ? formatDateTime(x.finished_at) : <span className="muted">Not finished</span>), hideBelow: 900 },
                ]}
                empty={<EmptyState title="No pipeline runs saved" />}
              />
            </Panel>
            <Section title="Checks, traces and gate decisions" count={r.checks.length}>
              {r.checks.length ? r.checks.map((c) => <CheckRecord key={c.id} c={c} trace={traceFor.get(c.id)} />) : <EmptyState title="No checks saved" />}
            </Section>
          </div>
        ) : null}

        {tab === "recs" ? (
          <Panel title="Recommendations by version">
            {r.recommendations.length ? (
              <div className="stack">
                {[...r.recommendations].sort((a, b) => a.version - b.version).map((rec) => (
                  <div key={rec.version} className="recon-details" style={{ padding: "var(--sp-3) var(--sp-4)" }}>
                    <div className="row-between">
                      <div className="row">
                        <strong>Version {rec.version}</strong>
                        <Pill tone="accent" size="sm">{RECOMMENDATION_LABEL[rec.choice] ?? humanize(rec.choice)}</Pill>
                        {rec.is_current ? <Pill tone="pass" size="sm" icon="check">Current</Pill> : <Pill tone="neutral" size="sm">Replaced</Pill>}
                      </div>
                      <span className="muted small">{formatDateTime(rec.created_at)}</span>
                    </div>
                    <p style={{ margin: "var(--sp-2) 0" }}>{rec.summary}</p>
                    <KeyValue
                      columns={2}
                      items={[
                        { label: "Reason", value: rec.reason },
                        { label: "Input fingerprint", value: rec.input_fingerprint, mono: true },
                        { label: "Items to confirm", value: rec.items_to_confirm.length ? rec.items_to_confirm.join("; ") : null },
                        { label: "Conflicts", value: rec.conflicts?.length ? rec.conflicts.map((c) => `${c.fact}: ${c.values.join(" vs ")}`).join("; ") : null },
                      ]}
                    />
                  </div>
                ))}
              </div>
            ) : (
              <EmptyState title="No recommendation was saved" />
            )}
          </Panel>
        ) : null}

        {tab === "review" ? (
          <div className="stack">
            <Panel title={`Threads (${r.threads.length})`}>
              {r.threads.length ? (
                <div className="stack">
                  {r.threads.map((t) => (
                    <div className="recon-details" key={t.id} style={{ padding: "var(--sp-3) var(--sp-4)" }}>
                      <div className="row">
                        <code>{t.id}</code>
                        <span>on {t.target.type} <code>{t.target.ref}</code></span>
                        <Pill tone={t.status === "open" ? "look" : "pass"} size="sm">{humanize(t.status)}</Pill>
                        {t.blocks_promotion ? <Pill tone="held" size="sm" icon="lock">Blocks promotion</Pill> : null}
                      </div>
                      <ul style={{ margin: "var(--sp-2) 0 0", paddingLeft: 18 }}>
                        {t.comments.map((c) => (
                          <li key={c.id} className="small">
                            <strong>{name(c.author_type, c.author_id)}</strong> <span className="muted">({c.author_type}, {formatDateTime(c.at)})</span>: {c.text}
                          </li>
                        ))}
                      </ul>
                      {t.resolved_at ? <div className="muted small">Resolved by {name("person", t.resolved_by ?? "")} on {formatDateTime(t.resolved_at)}</div> : null}
                    </div>
                  ))}
                </div>
              ) : (
                <EmptyState title="No threads on this case" />
              )}
            </Panel>
            <Panel title={`Questions to the company (${r.questions.length})`}>
              {r.questions.length ? (
                <div className="stack">
                  {r.questions.map((q) => (
                    <div className="recon-details" key={q.id} style={{ padding: "var(--sp-3) var(--sp-4)" }}>
                      <div className="row">
                        <code>{q.id}</code>
                        <Pill tone={q.status === "answered" ? "pass" : q.status === "draft" ? "neutral" : "look"} size="sm">{humanize(q.status)}</Pill>
                        <span className="muted small">Drafted by {q.drafted_by}</span>
                        {q.sent_at ? <span className="muted small">Sent by {name("person", q.sent_by ?? "")} on {formatDateTime(q.sent_at)}</span> : <span className="muted small">Not sent</span>}
                        {q.clock_stopped ? <Pill tone="accent" size="sm" icon="clock">Clock stopped</Pill> : null}
                      </div>
                      <ul style={{ margin: "var(--sp-2) 0 0", paddingLeft: 18 }}>
                        {Object.entries(q.texts).map(([lang, text]) => (
                          <li key={lang} className="small"><span className="muted">{lang.toUpperCase()}:</span> {text}</li>
                        ))}
                      </ul>
                    </div>
                  ))}
                </div>
              ) : (
                <EmptyState title="No questions on this case" />
              )}
            </Panel>
          </div>
        ) : null}

        {tab === "decision" ? (
          <div className="stack">
            <Panel title="Decision">
              {r.decision ? (
                <div className="stack">
                  <KeyValue
                    columns={2}
                    items={[
                      { label: "Decision", value: <code>{r.decision.id}</code> },
                      { label: "Type", value: humanize(r.decision.type) },
                      { label: "State", value: <Pill tone={r.decision.state === "signed" || r.decision.state === "reconfirmed" ? "pass" : r.decision.state === "on_hold" ? "look" : "held"} size="sm">{humanize(r.decision.state)}</Pill> },
                      { label: "Recommendation version signed", value: `Version ${r.decision.recommendation_version}${r.decision.accepts_recommendation ? ", accepted" : ", overridden"}` },
                      { label: "Legal effect", value: humanize(r.decision.legal_effect) },
                      { label: "Record status", value: humanize(r.decision.record_status) },
                      { label: "Reason", value: r.decision.reason },
                      { label: "Conditions", value: r.decision.conditions?.length ? r.decision.conditions.join("; ") : null },
                    ]}
                  />
                  <div>
                    <div className="muted small">Signed fingerprint (the package the signers saw)</div>
                    <div className="hash">{r.decision.signed_payload_hash}</div>
                  </div>
                  <Table
                    caption="Signers"
                    dense
                    rows={r.decision.signers}
                    rowKey={(s) => s.person_id + s.signed_at}
                    columns={[
                      { key: "p", header: "Signer", render: (s) => name("person", s.person_id) },
                      { key: "r", header: "Role", render: (s) => (ROLE_LABEL as Record<string, string>)[s.role] ?? humanize(s.role) },
                      { key: "t", header: "Signed", render: (s) => formatDateTime(s.signed_at) },
                    ]}
                  />
                </div>
              ) : (
                <EmptyState title="No decision yet" icon="stamp">This case has not been signed.</EmptyState>
              )}
            </Panel>
            <Panel title="Government proposal">
              {r.proposal ? (
                <KeyValue
                  columns={2}
                  items={[
                    { label: "Proposal", value: <code>{r.proposal.id}</code> },
                    { label: "Action", value: humanize(r.proposal.action) },
                    { label: "State", value: <Pill tone={r.proposal.state === "acknowledged" ? "pass" : r.proposal.state === "rejected" ? "held" : r.proposal.state === "unknown" ? "look" : "accent"} size="sm">{humanize(r.proposal.state)}</Pill> },
                    { label: "Attempts", value: r.proposal.attempts ?? 0 },
                    { label: "Government reference", value: r.proposal.gov_ref, mono: true },
                    { label: "Based on record version", value: r.proposal.based_on_gov_version, mono: true },
                    { label: "Idempotency key", value: r.proposal.idempotency_key, mono: true },
                    { label: "Last error", value: r.proposal.last_error },
                    { label: "Created", value: formatDateTime(r.proposal.created_at) },
                    { label: "Updated", value: formatDateTime(r.proposal.updated_at) },
                  ]}
                />
              ) : (
                <EmptyState title="No government proposal" icon="send">Nothing was sent to the government record for this case.</EmptyState>
              )}
            </Panel>
          </div>
        ) : null}

        {tab === "audit" ? (
          <Panel title={`Audit rows for this case (${r.audit_events.length})`} padded={false}>
            <Table caption="Audit rows" dense rows={r.audit_events} rowKey={(e) => e.id} columns={auditCols} empty={<EmptyState title="No audit rows" />} />
            <div style={{ padding: "var(--sp-3) var(--sp-4)" }}>
              <ButtonLink to={`/audit?case=${r.case.id}`} variant="ghost" iconRight="arrow-right">
                Open these rows in the audit log
              </ButtonLink>
            </div>
          </Panel>
        ) : null}
      </Tabs>
    </div>
  );
}

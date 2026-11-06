// Gaps: signals counted by code (empty slots, missing sources, local practice, repeated
// waivers), the candidates the Gap Analyst grouped from them, and a person's review.
// Nothing is a confirmed gap until a Process Owner accepts it.
import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, ApiError } from "../../api";
import type { Gap, GapSignal } from "../../api/types";
import { useSession } from "../../app/session";
import { Button, Callout, Drawer, EmptyState, ErrorState, EvidenceQuote, Field, KeyValue, PageHeader, Panel, Pill, Table, TextArea, type Tone, useToast } from "../../components";
import { useApi } from "../../hooks/useApi";
import { humanize } from "../../lib/labels";
import { loadAll, usePersonName } from "./ownerShared";

const SIGNAL_LABEL: Record<GapSignal["kind"], string> = {
  empty_slot: "Empty required slot",
  no_source: "No source found",
  local_practice_rule: "Rule from local practice",
  repeated_waiver: "Repeated waivers",
  repeated_override: "Repeated overrides",
};
const STATUS: Record<Gap["status"], { label: string; tone: Tone }> = {
  candidate: { label: "Candidate", tone: "accent" },
  accepted: { label: "Accepted", tone: "pass" },
  rejected: { label: "Rejected", tone: "neutral" },
  needs_evidence: { label: "Needs evidence", tone: "look" },
};
const DECISIONS: { value: "accepted" | "rejected" | "needs_evidence"; label: string; hint: string }[] = [
  { value: "accepted", label: "Accept", hint: "This is a real gap to close." },
  { value: "needs_evidence", label: "Needs evidence", hint: "Possible, but more proof is needed." },
  { value: "rejected", label: "Reject", hint: "Not a gap, or already handled." },
];

export default function GapsPage() {
  const { workspace } = useSession();
  const ws = workspace?.id ?? "";
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const signals = useApi(() => loadAll((cursor) => api.listGapSignals(ws, { cursor, limit: 200 })), [ws], { enabled: Boolean(ws) });
  const gaps = useApi(() => loadAll((cursor) => api.listGaps(ws, { cursor, limit: 200 })), [ws], { enabled: Boolean(ws) });
  const [analysisId, setAnalysisId] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [startErr, setStartErr] = useState<string | null>(null);
  const analysis = useApi(() => api.getGapAnalysis(analysisId ?? ""), [analysisId], { enabled: Boolean(analysisId) });
  const [filter, setFilter] = useState<"all" | Gap["status"]>("all");
  const openId = params.get("gap");
  const open = gaps.data?.find((g) => g.id === openId) ?? null;
  const running = analysis.data?.status === "running" || analysis.data?.status === "queued";

  useEffect(() => {
    if (analysis.data?.status === "completed") toast.success(`Analysis finished: ${analysis.data.candidates_created} new candidate${analysis.data.candidates_created === 1 ? "" : "s"}`);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [analysis.data?.status]);

  const coveredBy = useMemo(() => {
    const m = new Map<string, Gap>();
    for (const g of gaps.data ?? []) for (const s of g.signal_ids) m.set(s, g);
    return m;
  }, [gaps.data]);
  const shown = (gaps.data ?? []).filter((g) => filter === "all" || g.status === filter);
  const count = (s: Gap["status"]) => (gaps.data ?? []).filter((g) => g.status === s).length;
  const setOpen = (id: string | null) => {
    const next = new URLSearchParams(params);
    if (id) next.set("gap", id);
    else next.delete("gap");
    setParams(next, { replace: true });
  };
  const start = async () => {
    setStarting(true);
    setStartErr(null);
    try {
      const a = await api.startGapAnalysis(ws, { signal_ids: [] });
      setAnalysisId(a.id);
    } catch (e) {
      setStartErr(e instanceof ApiError ? e.message : "The analysis did not start.");
    } finally {
      setStarting(false);
    }
  };

  return (
    <>
      <PageHeader
        title="Gaps"
        subtitle="Where the library or the rules fall short. Code counts the signals; an analyst groups them into candidates; you decide which are real."
        actions={<Button variant="primary" icon="sparkle" loading={starting || running} onClick={start} disabled={!ws}>Run analysis</Button>}
      />
      <div className="ow-stack">
        <Callout tone="neutral" title="Nothing is a confirmed gap until you accept it">
          Signals are counted by code. Candidates are suggestions grouped from signals not yet covered. Your review, with a note, is what makes a gap real.
        </Callout>
        {startErr ? <Callout tone="held" title="Not started">{startErr}</Callout> : null}
        {analysisId && analysis.data ? (
          <Callout tone={running ? "accent" : analysis.data.status === "failed" ? "held" : "pass"} title={running ? "Analysis running" : analysis.data.status === "failed" ? "Analysis failed" : "Analysis finished"}>
            {running ? "Grouping signals that no candidate covers yet." : `${analysis.data.candidates_created} new candidate${analysis.data.candidates_created === 1 ? "" : "s"} created from signals no earlier candidate covered.`}
          </Callout>
        ) : null}

        <Panel title={`Signals counted by code${signals.data ? ` (${signals.data.length})` : ""}`} padded={Boolean(signals.error)}>
          {signals.error ? (
            <ErrorState error={signals.error} onRetry={signals.reload} />
          ) : (
            <Table
              rows={signals.data}
              loading={signals.loading}
              rowKey={(s) => s.id}
              caption="Gap signals"
              empty={<EmptyState title="No signals" icon="check">Nothing has been counted yet.</EmptyState>}
              columns={[
                { key: "kind", header: "Signal", sort: (s) => s.kind, render: (s) => SIGNAL_LABEL[s.kind] },
                { key: "detail", header: "Detail", render: (s) => s.detail },
                { key: "count", header: "Count", align: "right", sort: (s) => s.count, render: (s) => <span className="tabular strong">{s.count}</span> },
                {
                  key: "cov",
                  header: "Candidate",
                  render: (s) => {
                    const g = coveredBy.get(s.id);
                    return g ? <button type="button" className="btn btn-ghost btn-sm" onClick={() => setOpen(g.id)}>{g.title}</button> : <span className="muted small">None yet</span>;
                  },
                },
              ]}
              dense
            />
          )}
        </Panel>

        <Panel
          title={`Candidates${gaps.data ? ` (${gaps.data.length})` : ""}`}
          padded={Boolean(gaps.error)}
          actions={
            <div className="ow-seg" role="group" aria-label="Show">
              <button type="button" aria-pressed={filter === "all"} onClick={() => setFilter("all")}>All</button>
              {(Object.keys(STATUS) as Gap["status"][]).map((s) => (
                <button key={s} type="button" aria-pressed={filter === s} onClick={() => setFilter(s)}>{STATUS[s].label} ({count(s)})</button>
              ))}
            </div>
          }
        >
          {gaps.error ? (
            <ErrorState error={gaps.error} onRetry={gaps.reload} />
          ) : (
            <Table
              rows={gaps.data ? shown : undefined}
              loading={gaps.loading}
              rowKey={(g) => g.id}
              caption="Gap candidates"
              onRowClick={(g) => setOpen(g.id)}
              selectedKey={openId}
              empty={<EmptyState title="No candidates" icon="flag">{filter === "all" ? "Run an analysis to group the signals above." : "No candidate in this state."}</EmptyState>}
              columns={[
                { key: "title", header: "Candidate", sort: (g) => g.title, render: (g) => <strong>{g.title}</strong> },
                { key: "kind", header: "Kind", sort: (g) => g.kind, render: (g) => (g.kind === "policy" ? "Policy" : "Standards"), hideBelow: 900 },
                { key: "status", header: "Status", sort: (g) => g.status, render: (g) => <Pill tone={STATUS[g.status].tone} size="sm">{STATUS[g.status].label}</Pill> },
                { key: "sig", header: "Signals", align: "right", render: (g) => g.signal_ids.length, hideBelow: 900 },
                { key: "next", header: "Suggested next step", render: (g) => <span className="small">{g.suggested_next_step ?? ""}</span>, hideBelow: 1100 },
              ]}
              dense
            />
          )}
        </Panel>
      </div>
      <Drawer open={Boolean(open)} onClose={() => setOpen(null)} title={open?.title ?? ""} subtitle={open ? `${open.kind === "policy" ? "Policy" : "Standards"} candidate` : undefined} width={600}>
        {open ? <GapReview key={open.id + open.status} gap={open} signals={signals.data ?? []} onDone={() => { gaps.reload(); }} /> : null}
      </Drawer>
    </>
  );
}

function GapReview({ gap, signals, onDone }: { gap: Gap; signals: GapSignal[]; onDone: () => void }) {
  const name = usePersonName();
  const toast = useToast();
  const [status, setStatus] = useState<"accepted" | "rejected" | "needs_evidence">(gap.status === "accepted" || gap.status === "rejected" || gap.status === "needs_evidence" ? gap.status : "accepted");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const mine = signals.filter((s) => gap.signal_ids.includes(s.id));
  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      await api.reviewGap(gap.id, { status, review_note: note.trim() });
      toast.success(`Marked ${STATUS[status].label.toLowerCase()}`);
      setNote("");
      onDone();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Not saved.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="stack">
      <Pill tone={STATUS[gap.status].tone}>{STATUS[gap.status].label}</Pill>
      <p style={{ margin: 0 }}>{gap.description}</p>
      <KeyValue
        columns={2}
        items={[
          { label: "Where in the pipeline", value: gap.pipeline_node ? humanize(gap.pipeline_node) : null },
          { label: "Readiness dimension", value: gap.readiness_dimension },
          { label: "Suggested next step", value: gap.suggested_next_step },
          ...(gap.pack_version_id ? [{ label: "Pack draft it opened", value: <Link to={`/owner/packs/${gap.pack_version_id}`}>Open the draft</Link> }] : []),
          { label: "Reviewed by", value: gap.reviewed_by ? name(gap.reviewed_by) : "Not yet" },
        ]}
      />
      {gap.review_note ? <div className="ow-quote"><div className="muted xsmall">Review note</div>{gap.review_note}</div> : null}
      {mine.length ? (
        <div>
          <div className="ow-section">Signals behind it</div>
          <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
            {mine.map((s) => <li key={s.id}>{SIGNAL_LABEL[s.kind]}: {s.detail} ({s.count})</li>)}
          </ul>
        </div>
      ) : null}
      {gap.global_examples.length ? (
        <div className="stack-sm">
          <div className="ow-section">What other sources say (for context, not a requirement)</div>
          {gap.global_examples.map((e) => <EvidenceQuote key={e.anchor} evidence={e} tone="reference" />)}
        </div>
      ) : null}
      <Panel title="Your review">
        <div className="stack">
          <div className="ow-autonomy" role="radiogroup" aria-label="Decision">
            {DECISIONS.map((d) => (
              <label className="ow-radio" key={d.value}>
                <input type="radio" name="gapdecision" checked={status === d.value} onChange={() => setStatus(d.value)} />
                <span>{d.label}<small>{d.hint}</small></span>
              </label>
            ))}
          </div>
          <Field label="Note" required hint="Saved with your name. The next reviewer reads it.">{(id, d) => <TextArea id={id} aria-describedby={d} rows={3} value={note} onChange={(e) => setNote(e.target.value)} />}</Field>
          {err ? <Callout tone="held" title="Not saved">{err}</Callout> : null}
          <div className="ow-actions">
            <Button variant="primary" loading={busy} disabled={!note.trim()} onClick={submit}>Save review</Button>
          </div>
        </div>
      </Panel>
    </div>
  );
}

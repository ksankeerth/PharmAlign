// Knowledge search test: the same filters agents get, applied before search.
// Shows the filters, the hits with legal status and level, and an explicit "No source found".
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api";
import type { KbSearchResponse } from "../../api/types";
import { Button, Callout, EmptyState, ErrorState, Field, KeyValue, PageHeader, Panel, Pill, PrototypeNotice, Select, Table, TextInput } from "../../components";
import { useSession } from "../../app/session";
import { useApi, useMutation } from "../../hooks/useApi";
import { formatPercent } from "../../lib/format";
import { humanize } from "../../lib/labels";
import { LegalPill } from "./shared";

/** One-click searches that show what the library holds, including one it has no source for. */
const EXAMPLES: { label: string; query: string; purpose: "requirement" | "context" }[] = [
  { label: "Stability tests for a hot climate", query: "long-term stability testing conditions for a hot climate zone", purpose: "requirement" },
  { label: "Who may own a pharmacy business", query: "who may own or operate a pharmaceutical establishment", purpose: "requirement" },
  { label: "Arabic on labels and leaflets", query: "Arabic language on the label and patient leaflet", purpose: "requirement" },
  { label: "Is Federal Law No. 8 of 2019 in force?", query: "Federal Law No. 8 of 2019 repealed", purpose: "context" },
  { label: "Nicotine vaping liquids (no source)", query: "nicotine vaping liquids", purpose: "requirement" },
];

const LEVEL: Record<number, string> = { 1: "Binding law", 2: "National guidance", 3: "International standard", 4: "Other regulators", 5: "Context only" };

function renderFilter(v: unknown): string {
  if (Array.isArray(v)) return v.map((x) => humanize(String(x))).join(", ");
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (v === null || v === undefined) return "None";
  return String(v);
}
const FILTER_LABEL: Record<string, string> = {
  workspace_id: "Country workspace",
  snapshot_id: "Frozen snapshot",
  adopted: "Adopted by this country only",
  legal_status: "Legal status allowed",
  applied_before_search: "Filters applied before search",
};

export default function KbSearchPage() {
  const { workspace } = useSession();
  const wsId = workspace?.id ?? "ws-ae";
  const snaps = useApi(() => api.listSnapshots(wsId, { limit: 50 }), [wsId]);
  const [query, setQuery] = useState("long-term stability 30 C 65% RH");
  const [purpose, setPurpose] = useState<"requirement" | "context">("requirement");
  const [snapshot, setSnapshot] = useState("");
  const [topK, setTopK] = useState(5);
  const [asked, setAsked] = useState<{ query: string; purpose: string } | null>(null);
  const search = useMutation((body: Parameters<typeof api.searchKb>[0]) => api.searchKb(body));
  const [result, setResult] = useState<KbSearchResponse | null>(null);

  const run = async (q = query, p = purpose) => {
    if (!q.trim()) return;
    try {
      const r = await search.run({ query: q.trim(), purpose: p, snapshot_id: snapshot || null, top_k: topK });
      setResult(r);
      setAsked({ query: q.trim(), purpose: p });
    } catch {
      setResult(null);
    }
  };
  const tryExample = (e: (typeof EXAMPLES)[number]) => {
    setQuery(e.query);
    setPurpose(e.purpose);
    void run(e.query, e.purpose);
  };

  return (
    <div className="stack">
      <PageHeader title="Knowledge search" subtitle="See exactly what an agent finds when it looks something up. The same rules apply: a requirement can only be backed by law in force." />
      <div className="kb-examples" role="group" aria-label="Try an example">
        <span className="muted small">Try:</span>
        {EXAMPLES.map((e) => (
          <button key={e.label} type="button" className="kb-example" onClick={() => tryExample(e)} disabled={search.pending}>
            {e.label}
          </button>
        ))}
      </div>
      <Panel>
        <form className="stack-sm" onSubmit={(e) => { e.preventDefault(); void run(); }}>
          <Field label="Question or phrase" required>
            {(id) => <TextInput id={id} value={query} onChange={(e) => setQuery(e.target.value)} />}
          </Field>
          <div className="adm-toolbar">
            <Field label="Purpose" hint={purpose === "requirement" ? "A requirement can only cite a source in force." : "Context may use any adopted source."}>
              {(id, d) => <Select id={id} aria-describedby={d} value={purpose} onChange={(e) => setPurpose(e.target.value as typeof purpose)} options={[{ value: "requirement", label: "Requirement (required by)" }, { value: "context", label: "Context (background)" }]} />}
            </Field>
            <Field label="Snapshot">
              {(id) => <Select id={id} value={snapshot} onChange={(e) => setSnapshot(e.target.value)} options={[{ value: "", label: "Current library" }, ...(snaps.data?.items ?? []).map((s) => ({ value: s.id, label: `${s.label} (${s.item_count} versions)` }))]} />}
            </Field>
            <Field label="Results">
              {(id) => <Select id={id} value={String(topK)} onChange={(e) => setTopK(Number(e.target.value))} options={[3, 5, 10].map((n) => ({ value: String(n), label: `Top ${n}` }))} />}
            </Field>
            <Button type="submit" variant="primary" icon="search" loading={search.pending} disabled={!query.trim()}>Search</Button>
          </div>
        </form>
      </Panel>

      {search.error ? <ErrorState error={search.error} onRetry={() => void run()} /> : null}

      {result && asked ? (
        <div className="stack" aria-live="polite">


          {result.no_source_found ? (
            <div className="adm-silence" style={{ borderRadius: "var(--r-lg)" }}>
              <Callout tone="held" icon="search" title="No source found">
                Nothing in this library backs that question. An agent gets the same answer and must say it found no source, never guess. If this is a real need, it becomes a gap for the Process Owner.
                {result.hits.length ? ` The ${result.hits.length} weak match${result.hits.length === 1 ? "" : "es"} below scored too low to cite.` : ""}
              </Callout>
            </div>
          ) : (
            <Callout tone="pass" title={`${result.hits.length} source${result.hits.length === 1 ? "" : "s"} found`}>Ordered by score. Legal status and level decide what each can back.</Callout>
          )}

          {result.hits.length === 0 ? (
            <EmptyState title="No hits" icon="search">Try other words, or change the purpose to context.</EmptyState>
          ) : (
            <div className="stack-sm">
              <h2>{result.no_source_found ? "Weak matches (not citable)" : "Hits"}</h2>
              {result.hits.map((h) => (
                <article key={h.anchor} className="adm-hit" style={result.no_source_found ? { opacity: 0.8 } : undefined}>
                  <div className="row-between">
                    <div>
                      <Link to={`/admin/library/${h.document_id}`} className="strong">{h.title}</Link>
                      <div className="xsmall muted">{h.issuer} · {h.jurisdiction}</div>
                    </div>
                    <div className="row">
                      <LegalPill status={h.legal_status} />
                      <Pill tone="neutral" size="sm" title={LEVEL[h.authority_level]}>Level {h.authority_level}: {LEVEL[h.authority_level] ?? "Other"}</Pill>
                      <Pill tone={h.score >= 0.45 ? "accent" : "neutral"} size="sm">Score {h.score.toFixed(2)}</Pill>
                    </div>
                  </div>
                  <p className="small" style={{ margin: "8px 0" }}>{h.text}</p>
                  <div className="xsmall muted"><code>{h.anchor}</code>{h.article ? ` · ${h.article}` : ""}{h.page ? ` · page ${h.page}` : ""}</div>
                </article>
              ))}
            </div>
          )}
          <details className="kb-filters">
            <summary>How this search was filtered: the same rules an agent gets</summary>
            <KeyValue columns={3} items={Object.entries(result.filters_applied).map(([k, v]) => ({ label: FILTER_LABEL[k] ?? humanize(k), value: k === "workspace_id" && v === workspace?.id && workspace ? workspace.name : renderFilter(v) }))} />
            <p className="xsmall muted" style={{ marginTop: 8 }}>For "{asked.query}" as a {asked.purpose}.</p>
          </details>
        </div>
      ) : null}

      <RetrievalTest snapshots={snaps.data?.items ?? []} />
      <PrototypeNotice />
    </div>
  );
}

function RetrievalTest({ snapshots }: { snapshots: { id: string; label: string }[] }) {
  const [snap, setSnap] = useState("");
  const [testId, setTestId] = useState<string | null>(null);
  const start = useMutation(() => api.startRetrievalTest({ snapshot_id: snap || snapshots[0]?.id, question_set: "core-questions" }));
  const test = useApi(() => api.getRetrievalTest(testId!), [testId], { enabled: Boolean(testId) });
  const running = test.data?.status === "running";
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => test.reload(), 1000);
    return () => clearInterval(t);
  }, [running, test]);
  const t = test.data;
  return (
    <Panel
      title="Retrieval test"
      actions={
        <>
          <Select aria-label="Snapshot to test" value={snap || snapshots[0]?.id || ""} onChange={(e) => setSnap(e.target.value)} options={snapshots.map((s) => ({ value: s.id, label: s.label }))} />
          <Button size="sm" icon="play" loading={start.pending || running} disabled={!snapshots.length} onClick={() => void start.run().then((r) => setTestId(r.id)).catch(() => undefined)}>Run question set</Button>
        </>
      }
    >
      <p className="small muted">Asks a fixed set of questions and checks that the right document comes back near the top. Our own starting target is 85 percent at five.</p>
      {start.error ? <ErrorState error={start.error} /> : null}
      {t ? (
        <div className="stack" style={{ marginTop: 12 }}>
          <div className="adm-metrics">
            <Metric2 label="Right document in top 1" v={t.document_hit_at_1} />
            <Metric2 label="Right document in top 5" v={t.document_hit_at_5} target={0.85} />
            <Metric2 label="Right passage in top 1" v={t.passage_hit_at_1} />
            <Metric2 label="Right passage in top 10" v={t.passage_hit_at_10} />
          </div>
          <Table
            rows={t.results}
            rowKey={(r) => r.question}
            dense
            caption="Retrieval test results"
            columns={[
              { key: "q", header: "Question", render: (r) => r.question },
              { key: "d", header: "Expected document", render: (r) => (r.expected_document_id ? <code>{r.expected_document_id}</code> : <span className="muted small">None expected</span>) },
              { key: "rd", header: "Document rank", align: "right", render: (r) => r.rank_of_document ?? <span className="muted">Not found</span> },
              { key: "rp", header: "Passage rank", align: "right", render: (r) => r.rank_of_passage ?? <span className="muted">Not found</span> },
              { key: "n", header: "Result", render: (r) => (r.no_source_found ? <Pill tone="look" size="sm">No source found (expected)</Pill> : r.rank_of_document === 1 ? <Pill tone="pass" size="sm" icon="check">Hit</Pill> : <Pill tone="look" size="sm">Lower rank</Pill>) },
            ]}
          />
        </div>
      ) : running ? <p className="small" style={{ marginTop: 8 }}>Running the questions.</p> : null}
    </Panel>
  );
}

function Metric2({ label, v, target }: { label: string; v: number | null; target?: number }) {
  return (
    <div className="adm-metric">
      <div className="adm-metric-label">{label}</div>
      <div className="adm-metric-value">{v === null ? "..." : formatPercent(v)}</div>
      {target && v !== null ? <div className="adm-metric-note">{v >= target ? "Meets" : "Below"} the {formatPercent(target)} target</div> : null}
    </div>
  );
}

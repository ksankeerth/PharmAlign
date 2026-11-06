// Agent runs: status, attempts, generations, fallback use and error codes. Ids and
// numbers only: no dossier content, and no prompts or answers.
import { useMemo, useState } from "react";
import { api } from "../../api";
import type { AgentRunView } from "../../api/types";
import { Button, EmptyState, ErrorState, Field, PageHeader, Panel, Pill, Select, Table, type Tone } from "../../components";
import { useApi } from "../../hooks/useApi";
import { formatNumber, formatRelative } from "../../lib/format";
import { humanize } from "../../lib/labels";
import { Metric } from "./shared";

const STATUS: Record<AgentRunView["status"], { tone: Tone; icon: "check" | "loader" | "x" | "ban" | "pause" | "circle-dashed" | "alert-triangle" }> = {
  queued: { tone: "neutral", icon: "circle-dashed" },
  running: { tone: "accent", icon: "loader" },
  completed: { tone: "pass", icon: "check" },
  stopped_early: { tone: "look", icon: "pause" },
  failed: { tone: "held", icon: "x" },
  cancelled: { tone: "neutral", icon: "ban" },
  lost: { tone: "error", icon: "alert-triangle" },
};

const ERROR_HINT: Record<string, string> = {
  model_timeout: "The model did not answer in time.",
  gate_refused_output: "The output gate refused the answer (for example a repealed source). A retry followed.",
  budget_reached: "The run stopped at its budget.",
  held_before_read: "A hold stopped the run before it read anything.",
  agent_restarted: "The agent restarted mid-run. The run is recorded as lost and retried.",
};

export default function AgentRunsPage() {
  const { data, error, loading, reload } = useApi(() => api.listAgentRuns({ limit: 200 }), []);
  const [agent, setAgent] = useState("");
  const [status, setStatus] = useState("");
  const [gen, setGen] = useState("");
  const all = data?.items ?? [];
  const rows = useMemo(
    () => all.filter((r) => (!agent || r.agent === agent) && (!status || r.status === status) && (!gen || (gen === "current" ? r.is_current !== false : r.is_current === false))),
    [all, agent, status, gen],
  );
  const agents = [...new Set(all.map((r) => r.agent))].sort();
  const codes = useMemo(() => {
    const m = new Map<string, { n: number; agents: Set<string> }>();
    all.forEach((r) => {
      if (!r.error_code) return;
      const e = m.get(r.error_code) ?? { n: 0, agents: new Set<string>() };
      e.n += 1;
      e.agents.add(r.agent);
      m.set(r.error_code, e);
    });
    return [...m.entries()].map(([code, e]) => ({ code, n: e.n, agents: [...e.agents].sort() })).sort((a, b) => b.n - a.n);
  }, [all]);
  const tot = rows.reduce((a, r) => ({ calls: a.calls + (r.usage?.model_calls ?? 0), tin: a.tin + (r.usage?.input_tokens ?? 0), tout: a.tout + (r.usage?.output_tokens ?? 0) }), { calls: 0, tin: 0, tout: 0 });
  const clear = () => { setAgent(""); setStatus(""); setGen(""); };

  if (error) return <ErrorState error={error} onRetry={reload} />;
  return (
    <div className="stack">
      <PageHeader title="Agent runs" subtitle="Every run with its attempt, generation and outcome. Numbers and ids only: the content of a dossier is never shown here." />
      <div className="adm-metrics">
        <Metric label="Runs shown" value={rows.length} note={`of ${all.length}`} />
        <Metric label="Failed or stopped" value={rows.filter((r) => r.status === "failed" || r.status === "stopped_early").length} tone="look" />
        <Metric label="Lost" value={rows.filter((r) => r.status === "lost").length} tone="look" note="Restarted mid-run" />
        <Metric label="Fallback model used" value={rows.filter((r) => r.fallback_used).length} />
        <Metric label="Superseded" value={rows.filter((r) => r.is_current === false).length} note="Replaced by a later attempt" />
        <Metric label="Tokens" value={formatNumber(tot.tin + tot.tout)} note={`${formatNumber(tot.calls)} model calls`} />
      </div>

      <Panel>
        <form className="adm-toolbar" aria-label="Filter runs" onSubmit={(e) => e.preventDefault()}>
          <Field label="Agent">{(id) => <Select id={id} value={agent} onChange={(e) => setAgent(e.target.value)} options={[{ value: "", label: "All agents" }, ...agents.map((a) => ({ value: a, label: humanize(a) }))]} />}</Field>
          <Field label="Status">{(id) => <Select id={id} value={status} onChange={(e) => setStatus(e.target.value)} options={[{ value: "", label: "Any status" }, ...Object.keys(STATUS).map((s) => ({ value: s, label: humanize(s) }))]} />}</Field>
          <Field label="Generation">{(id) => <Select id={id} value={gen} onChange={(e) => setGen(e.target.value)} options={[{ value: "", label: "Current and superseded" }, { value: "current", label: "Current only" }, { value: "superseded", label: "Superseded only" }]} />}</Field>
          {agent || status || gen ? <Button variant="ghost" size="sm" icon="x" onClick={clear}>Clear filters</Button> : null}
        </form>
      </Panel>

      <Panel title="Runs" padded={false}>
        <Table
          rows={data ? rows : undefined}
          loading={loading}
          rowKey={(r) => r.run_id}
          dense
          caption="Agent runs"
          initialSort={{ key: "started", dir: "desc" }}
          empty={<EmptyState title="No runs match" icon="search" action={<Button onClick={clear}>Clear filters</Button>} />}
          columns={[
            { key: "agent", header: "Agent", sort: (r) => r.agent, render: (r) => humanize(r.agent) },
            { key: "case", header: "Case", sort: (r) => r.case_id ?? "", render: (r) => (r.case_id ? <code>{r.case_id}</code> : <span className="muted small">Not case work</span>) },
            { key: "check", header: "Check", hideBelow: 900, render: (r) => (r.check_id ? <code>{r.check_id}</code> : <span className="muted small">None</span>) },
            { key: "status", header: "Status", sort: (r) => r.status, render: (r) => <Pill tone={STATUS[r.status].tone} icon={STATUS[r.status].icon} spin={r.status === "running"} size="sm">{humanize(r.status)}</Pill> },
            { key: "gen", header: "Generation", hideBelow: 900, render: (r) => <span className="small nowrap">g{r.generation ?? 1} · attempt {r.attempt ?? 1}{r.is_current === false ? <> <Pill tone="neutral" size="sm">Superseded</Pill></> : null}</span> },
            { key: "fb", header: "Fallback", hideBelow: 1100, render: (r) => (r.fallback_used ? <Pill tone="look" size="sm">Used</Pill> : <span className="muted small">No</span>) },
            { key: "err", header: "Error code", render: (r) => (r.error_code ? <code title={ERROR_HINT[r.error_code]}>{r.error_code}</code> : <span className="muted small">None</span>) },
            { key: "dur", header: "Duration", align: "right", hideBelow: 1100, sort: (r) => r.duration_ms ?? -1, render: (r) => <span className="num small">{r.duration_ms ? `${(r.duration_ms / 1000).toFixed(1)} s` : "n/a"}</span> },
            { key: "tok", header: "Tokens", align: "right", hideBelow: 1100, sort: (r) => (r.usage ? r.usage.input_tokens + r.usage.output_tokens : -1), render: (r) => <span className="num small">{r.usage ? formatNumber(r.usage.input_tokens + r.usage.output_tokens) : "n/a"}</span> },
            { key: "started", header: "Started", sort: (r) => r.started_at ?? "", render: (r) => <span className="small nowrap">{formatRelative(r.started_at)}</span> },
          ]}
        />
      </Panel>

      <Panel title="Error codes" padded={false}>
        <Table
          rows={codes}
          rowKey={(c) => c.code}
          dense
          caption="Error codes across runs"
          empty={<EmptyState title="No errors recorded" icon="check" />}
          columns={[
            { key: "c", header: "Code", render: (c) => <code>{c.code}</code> },
            { key: "n", header: "Runs", align: "right", render: (c) => <span className="num">{c.n}</span> },
            { key: "a", header: "Agents", render: (c) => <span className="small">{c.agents.map(humanize).join(", ")}</span> },
            { key: "m", header: "Meaning", render: (c) => <span className="small">{ERROR_HINT[c.code] ?? <span className="muted">No description</span>}</span> },
          ]}
        />
      </Panel>
    </div>
  );
}

// Agent health: status, models available, failures, fallback use, generations, lost
// runs and usage totals per agent. Numbers only: no document content.
import { api } from "../../api";
import type { AgentHealthView } from "../../api/types";
import { ButtonLink, Callout, EmptyState, ErrorState, PageHeader, Panel, Pill, Table, type Tone } from "../../components";
import { useApi } from "../../hooks/useApi";
import { formatNumber, formatPercent, formatRelative } from "../../lib/format";
import { humanize } from "../../lib/labels";
import { Metric } from "./shared";

const lastRun = (at?: string | null) => (at ? formatRelative(at) : "not yet");

const STATUS: Record<AgentHealthView["status"], { tone: Tone; label: string; icon: "check" | "alert-triangle" | "x" }> = {
  ok: { tone: "pass", label: "Healthy", icon: "check" },
  degraded: { tone: "look", label: "Degraded", icon: "alert-triangle" },
  down: { tone: "held", label: "Down", icon: "x" },
};

export default function AgentHealthPage() {
  const health = useApi(() => api.getAgentHealth(), []);
  const runs = useApi(() => api.listAgentRuns({ limit: 200 }), []);
  const usage = useApi(() => api.getUsage(), []);
  const details = useApi(() => api.getHealthDetails().catch(() => null), []);

  const agents = health.data?.items ?? [];
  const rs = runs.data?.items ?? [];
  const per = (name: string) => {
    const mine = rs.filter((r) => r.agent === name);
    return {
      fallback: mine.filter((r) => r.fallback_used).length,
      superseded: mine.filter((r) => r.is_current === false).length,
      current: mine.filter((r) => r.is_current !== false).length,
      lost: mine.filter((r) => r.status === "lost").length,
    };
  };
  const degraded = agents.filter((a) => a.status !== "ok");
  const totalRuns = usage.data?.agents.reduce((n, a) => n + a.runs, 0) ?? 0;
  const totalFail = usage.data?.agents.reduce((n, a) => n + a.failures, 0) ?? 0;
  const tokens = usage.data?.agents.reduce((n, a) => n + a.usage.input_tokens + a.usage.output_tokens, 0) ?? 0;

  if (health.error) return <ErrorState error={health.error} onRetry={health.reload} />;
  return (
    <div className="stack">
      <PageHeader
        title="Agent health"
        subtitle="Counts, models and timings only. This screen never shows a dossier or what an agent read."
        actions={<ButtonLink to="/admin/agent-runs" iconRight="arrow-right">Agent runs</ButtonLink>}
      />

      {details.data?.agent_mode === "replay" ? (
        <Callout tone="neutral" title="Answers come from recordings">
          No agent services run here, so these figures are stored ones, not a live reading.
        </Callout>
      ) : null}

      {degraded.length ? (
        <Callout tone="look" title={`${degraded.length} agent${degraded.length === 1 ? " is" : "s are"} not fully healthy`}>
          {degraded.map((a) => (a.status === "down" ? `${humanize(a.agent)}: not answering` : `${humanize(a.agent)}: ${a.models_available} of ${a.models_total} models answering`)).join("; ")}. Work continues on the fallback model, and every use of it is recorded.
        </Callout>
      ) : null}

      <div className="adm-metrics">
        <Metric label="Agents healthy" value={health.data ? `${agents.filter((a) => a.status === "ok").length} of ${agents.length}` : "..."} tone={degraded.length ? "look" : "pass"} />
        <Metric label="Runs, last 30 days" value={formatNumber(totalRuns)} note={totalRuns ? `${formatPercent(totalFail / totalRuns)} failed` : undefined} />
        <Metric label="Failures, last hour" value={agents.reduce((n, a) => n + (a.failures_last_hour ?? 0), 0)} tone={agents.some((a) => (a.failures_last_hour ?? 0) > 0) ? "look" : "pass"} />
        <Metric label="Lost runs" value={runs.data ? rs.filter((r) => r.status === "lost").length : "..."} note="Agent restarted mid-run" tone={rs.some((r) => r.status === "lost") ? "look" : "pass"} />
        <Metric label="Tokens used" value={formatNumber(tokens)} note="Input and output, 30 days" />
      </div>

      <Panel title="Per agent" padded={false}>
        <Table
          rows={health.data ? agents : undefined}
          loading={health.loading}
          rowKey={(a) => a.agent}
          caption="Agent health"
          dense
          empty={<EmptyState title="No agent figures for this country">No agent services run here, so the server answers from recordings.</EmptyState>}
          columns={[
            { key: "agent", header: "Agent", sort: (a) => a.agent, render: (a) => <div><div className="strong">{humanize(a.agent)}</div>{a.version ? <div className="xsmall muted">v{a.version}</div> : null}</div> },
            { key: "status", header: "Status", sort: (a) => a.status, render: (a) => { const s = STATUS[a.status]; return <Pill tone={s.tone} icon={s.icon} size="sm">{s.label}</Pill>; } },
            { key: "models", header: "Models available", align: "right", render: (a) => (a.models_total ? <span className={`num ${a.models_available !== a.models_total ? "strong" : ""}`}>{a.models_available} of {a.models_total}</span> : <span className="small muted">Not reported</span>) },
            { key: "fail", header: "Failures, last hour", align: "right", render: (a) => <span className="num">{a.failures_last_hour ?? 0}</span> },
            { key: "fb", header: "Fallback used", align: "right", hideBelow: 900, render: (a) => <span className="num">{per(a.agent).fallback}</span> },
            { key: "gen", header: "Current / superseded", align: "right", hideBelow: 1100, render: (a) => <span className="num">{per(a.agent).current} / {per(a.agent).superseded}</span> },
            { key: "lost", header: "Lost", align: "right", hideBelow: 1100, render: (a) => <span className="num">{per(a.agent).lost}</span> },
            { key: "last", header: "Last success", hideBelow: 900, render: (a) => <span className="small">{a.last_success_at ? formatRelative(a.last_success_at) : "Never"}</span> },
          ]}
        />
        <div className="panel-foot xsmall muted">
          Fallback, generation and lost counts come from the most recent 200 runs. A superseded run is one that a later attempt or generation replaced; it is kept for the record.
        </div>
      </Panel>

      <Panel title="Usage totals, last 30 days" padded={false}>
        {usage.error ? <ErrorState error={usage.error} onRetry={usage.reload} /> : (
          <Table
            rows={usage.data?.agents}
            loading={usage.loading}
            rowKey={(a) => a.agent}
            dense
            caption="Usage per agent"
            columns={[
              { key: "agent", header: "Agent", render: (a) => humanize(a.agent), sort: (a) => a.agent },
              { key: "runs", header: "Runs", align: "right", sort: (a) => a.runs, render: (a) => <span className="num">{formatNumber(a.runs)}</span> },
              { key: "fail", header: "Failures", align: "right", sort: (a) => a.failures, render: (a) => <span className="num">{a.failures}</span> },
              { key: "calls", header: "Model calls", align: "right", hideBelow: 900, render: (a) => <span className="num">{formatNumber(a.usage.model_calls)}</span> },
              { key: "tin", header: "Input tokens", align: "right", hideBelow: 900, render: (a) => <span className="num">{formatNumber(a.usage.input_tokens)}</span> },
              { key: "tout", header: "Output tokens", align: "right", hideBelow: 900, render: (a) => <span className="num">{formatNumber(a.usage.output_tokens)}</span> },
              { key: "gate", header: "Gate blocks", align: "right", hideBelow: 1100, render: (a) => <span className="num">{a.gate_blocks}</span> },
              { key: "cite", header: "Citation failures", align: "right", hideBelow: 1100, render: (a) => <span className="num">{a.citation_failures}</span> },
              { key: "miss", header: "Retrieval misses", align: "right", hideBelow: 1100, render: (a) => <span className="num">{a.retrieval_misses}</span> },
              { key: "disp", header: "Dispute rate", align: "right", hideBelow: 1100, render: (a) => <span className="num">{a.dispute_rate === null || a.dispute_rate === undefined ? "n/a" : formatPercent(a.dispute_rate)}</span> },
            ]}
          />
        )}
      </Panel>

      {details.data ? (
        <Panel title="Platform services">
          <div className="row" style={{ gap: 12 }}>
            {[["Server", details.data.status], ["Database", details.data.database], ["Ingestion worker", details.data.ingest_worker]].map(([n, s]) => (
              <Pill key={n} tone={s === "ok" ? "pass" : s === "degraded" ? "look" : "held"} icon={s === "ok" ? "check" : "alert-triangle"}>{n}: {s === "ok" ? "OK" : humanize(s)}</Pill>
            ))}
            <span className="small muted">Scheduler last ran: {lastRun(details.data.scheduler_last_tick_at)}. Escalations last checked: {lastRun(details.data.escalation_loop_last_run_at)}.</span>
          </div>
        </Panel>
      ) : null}
    </div>
  );
}

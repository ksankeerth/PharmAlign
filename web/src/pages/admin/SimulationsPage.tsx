// Simulations: run a scenario with a pack and snapshot, replay or live model, gate on
// or off (off only here), and deliver staged events.
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api";
import type { Scenario, Simulation, SimulationEvent } from "../../api/types";
import { toProblem } from "../../api/errors";
import { Button, ButtonLink, Callout, EmptyState, ErrorState, Field, PageHeader, Panel, Pill, Select, Table, TextArea, TextInput, type Tone, useToast } from "../../components";
import { useSession } from "../../app/session";
import { useApi, useMutation } from "../../hooks/useApi";
import { formatRelative } from "../../lib/format";
import { humanize } from "../../lib/labels";
import { Metric } from "./shared";
import { actsAs } from "../../lib/roles";

const EVENTS: { kind: SimulationEvent["kind"]; label: string; hint: string }[] = [
  { kind: "foreign_notice", label: "Foreign notice", hint: "A regulator publishes a notice. Matching cases are alerted before any agent runs." },
  { kind: "company_reply", label: "Company reply", hint: "The company answers an open question. Affected checks go stale and re-run." },
  { kind: "model_outage", label: "Model outage", hint: "The model provider times out. Checks end as Error, not as a finding." },
  { kind: "agent_restart", label: "Agent restart", hint: "An agent restarts mid-run. The run is recorded as lost and retried." },
  { kind: "server_restart", label: "Server restart", hint: "The server restarts. Saved work resumes without repeats." },
  { kind: "gov_failure", label: "Government failure", hint: "The government system fails after a send. The record needs a read-back." },
];
const SEED_NOTICE = {
  regulator: "Reference Agency A (synthetic)",
  title: "Recall of batch CL-2291: clopidogrel 75 mg tablets (synthetic)",
  text: "Batch CL-2291 of clopidogrel 75 mg film-coated tablets is recalled because of a dissolution failure at the 12-month stability point. Other batches are not affected.",
};

const STATUS: Record<Simulation["status"], { tone: Tone; icon: "check" | "loader" | "x" | "clock" | "circle-dashed" }> = {
  queued: { tone: "neutral", icon: "circle-dashed" },
  running: { tone: "accent", icon: "loader" },
  waiting_for_event: { tone: "look", icon: "clock" },
  completed: { tone: "pass", icon: "check" },
  failed: { tone: "held", icon: "x" },
};

export default function SimulationsPage() {
  const { workspace, workspaces, view } = useSession();
  const wsId = workspace?.id ?? "ws-ae";
  const settings = workspaces.find((w) => w.id === wsId);
  const toast = useToast();
  const scenarios = useApi(() => api.listScenarios({ limit: 50 }), []);
  const packs = useApi(() => api.listPackVersions(wsId, { limit: 50 }), [wsId]);
  const snaps = useApi(() => api.listSnapshots(wsId, { limit: 50 }), [wsId]);
  const sims = useApi(() => api.listSimulations({ limit: 100 }), [wsId]);

  const [scenario, setScenario] = useState("");
  const [pack, setPack] = useState("");
  const [snapshot, setSnapshot] = useState("");
  const [execution, setExecution] = useState<"replay" | "live_model">("replay");
  const [gate, setGate] = useState<"on" | "off">("on");
  const canGateOff = actsAs(view, "platform_admin");

  useEffect(() => {
    // Each workspace has its own scenario ids, so start on the shelf-life one when it has it.
    if (!scenario && scenarios.data) setScenario((scenarios.data.items.find((s) => s.label === "Shelf-life trap") ?? scenarios.data.items[0])?.id ?? "");
  }, [scenarios.data, scenario]);
  useEffect(() => {
    if (!pack && packs.data) setPack(settings?.active_pack_version_id ?? packs.data.items[0]?.id ?? "");
  }, [packs.data, pack, settings]);
  useEffect(() => {
    if (!snapshot && snaps.data) setSnapshot(settings?.active_kb_snapshot_id ?? snaps.data.items[0]?.id ?? "");
  }, [snaps.data, snapshot, settings]);

  const list: Simulation[] = sims.data?.items ?? [];
  const active = list.some((s) => s.status === "running" || s.status === "queued");
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => sims.reload(), 1200);
    return () => clearInterval(t);
  }, [active, sims]);

  const start = useMutation(() => api.startSimulation({ scenario_id: scenario, pack_version_id: pack, kb_snapshot_id: snapshot, execution, gate }));
  const label = (id: string) => scenarios.data?.items.find((s) => s.id === id)?.label ?? humanize(id);
  const chosen: Scenario | undefined = scenarios.data?.items.find((s) => s.id === scenario);
  const blocked = workspace && (workspace.mode === "live" || workspace.mode === "shadow");

  const run = async () => {
    try {
      const s = await start.run();
      toast.success(`Started ${s.id}: ${label(s.scenario_id)}${s.gate === "off" ? " with the gate off" : ""}`);
    } catch {
      /* shown below */
    }
  };

  return (
    <div className="stack">
      <PageHeader
        title="Simulations"
        subtitle="Replay scenarios against a pack and a frozen snapshot. Simulation only: nothing here changes a government record."
        actions={<ButtonLink to="/admin/simulations/compare" icon="compare">Governed vs ungoverned</ButtonLink>}
      />
      {blocked ? <Callout tone="held" title="Simulation only">This workspace is in {workspace?.mode} mode, so simulation actions are refused.</Callout> : null}

      <div className="grid-2">
        <Panel title="Scenarios" padded={false}>
          {scenarios.error ? <ErrorState error={scenarios.error} onRetry={scenarios.reload} /> : (
            <Table
              rows={scenarios.data?.items}
              loading={scenarios.loading}
              rowKey={(s) => s.id}
              dense
              caption="Scenarios"
              selectedKey={scenario}
              onRowClick={(s) => setScenario(s.id)}
              columns={[
                { key: "l", header: "Scenario", render: (s) => <div><div className="strong">{s.label}</div><div className="xsmall muted">{s.description}</div></div> },
                { key: "k", header: "Kind", width: "80px", render: (s) => <Pill size="sm" tone={s.kind === "normal" ? "neutral" : s.kind === "gate" ? "accent" : "error"}>{humanize(s.kind)}</Pill> },
              ]}
            />
          )}
        </Panel>

        <Panel title="Run a scenario">
          <form className="stack-sm" onSubmit={(e) => { e.preventDefault(); void run(); }}>
            <Field label="Scenario" hint={chosen ? `Expected: ${chosen.expected_outcome}` : undefined}>
              {(id, d) => <Select id={id} aria-describedby={d} value={scenario} onChange={(e) => setScenario(e.target.value)} options={(scenarios.data?.items ?? []).map((s) => ({ value: s.id, label: s.label }))} />}
            </Field>
            <div className="adm-opts">
              <Field label="Pack version">
                {(id) => <Select id={id} value={pack} onChange={(e) => setPack(e.target.value)} options={(packs.data?.items ?? []).map((p) => ({ value: p.id, label: `Version ${p.version} (${humanize(p.state)})` }))} />}
              </Field>
              <Field label="Knowledge snapshot">
                {(id) => <Select id={id} value={snapshot} onChange={(e) => setSnapshot(e.target.value)} options={(snaps.data?.items ?? []).map((s) => ({ value: s.id, label: s.label }))} />}
              </Field>
              <Field label="Execution" hint={execution === "replay" ? "Saved model answers. No model cost." : "Calls the model now. Costs tokens."}>
                {(id, d) => <Select id={id} aria-describedby={d} value={execution} onChange={(e) => setExecution(e.target.value as typeof execution)} options={[{ value: "replay", label: "Replay" }, { value: "live_model", label: "Live model" }]} />}
              </Field>
              <Field label="Rules gate" hint={canGateOff ? "On is how the product runs." : "Only a Platform Admin can turn it off."}>
                {(id, d) => <Select id={id} aria-describedby={d} value={gate} disabled={!canGateOff} onChange={(e) => setGate(e.target.value as typeof gate)} options={[{ value: "on", label: "On (governed)" }, { value: "off", label: "Off (ungoverned)" }]} />}
              </Field>
            </div>
            {gate === "off" ? <Callout tone="look" title="Gate off">Agent output is accepted without source and citation rules. Use this only to show what the gate prevents, never on a real case.</Callout> : null}
            {start.error ? <Callout tone="held" title={toProblem(start.error).title}>{toProblem(start.error).detail}</Callout> : null}
            <div><Button type="submit" variant="primary" icon="play" loading={start.pending} disabled={!pack || !snapshot || Boolean(blocked)}>Run scenario</Button></div>
          </form>
        </Panel>
      </div>

      <div className="adm-metrics">
        <Metric label="Runs" value={list.length} />
        <Metric label="Met expectation" value={list.filter((s) => s.expected_met === true).length} tone="pass" />
        <Metric label="Did not meet" value={list.filter((s) => s.expected_met === false).length} tone="held" note="Expected with the gate off" />
        <Metric label="Waiting for an event" value={list.filter((s) => s.status === "waiting_for_event").length} tone="look" />
      </div>

      <Panel title="Runs" padded={false}>
        {sims.error ? <ErrorState error={sims.error} onRetry={sims.reload} /> : (
          <Table
            rows={sims.data ? list : undefined}
            loading={sims.loading}
            rowKey={(s) => s.id}
            dense
            caption="Simulation runs"
            empty={<EmptyState title="No runs yet" icon="beaker" />}
            columns={[
              { key: "id", header: "Run", render: (s) => <div><code className="strong">{s.id}</code><div className="xsmall muted">{label(s.scenario_id)}</div></div> },
              { key: "gate", header: "Gate", render: (s) => (s.gate === "off" ? <Pill tone="held" size="sm" icon="alert-triangle">Off</Pill> : <Pill tone="pass" size="sm" icon="shield">On</Pill>) },
              { key: "exec", header: "Execution", hideBelow: 1100, render: (s) => <span className="small">{s.execution === "live_model" ? "Live model" : "Replay"}</span> },
              { key: "status", header: "Status", render: (s) => <Pill tone={STATUS[s.status].tone} icon={STATUS[s.status].icon} spin={s.status === "running"} size="sm">{humanize(s.status)}</Pill> },
              { key: "met", header: "Expected outcome", hideBelow: 900, render: (s) => (s.expected_met === null ? <span className="muted small">Pending</span> : s.expected_met ? <Pill tone="pass" size="sm" icon="check">Met</Pill> : <Pill tone="held" size="sm" icon="x">Not met</Pill>) },
              { key: "out", header: "Outcome", render: (s) => <span className="small">{s.outcome ?? <span className="muted">None yet</span>}</span> },
              { key: "start", header: "Started", hideBelow: 1100, render: (s) => <span className="small nowrap">{formatRelative(s.started_at)}</span> },
            ]}
          />
        )}
        <div className="panel-foot xsmall muted">Pairs with the gate on and off appear side by side on <Link to="/admin/simulations/compare">Governed vs ungoverned</Link>.</div>
      </Panel>

      <EventPanel sims={list} onDone={sims.reload} />
    </div>
  );
}

function EventPanel({ sims, onDone }: { sims: Simulation[]; onDone: () => void }) {
  const toast = useToast();
  const waiting = sims.find((s) => s.status === "waiting_for_event");
  const [simId, setSimId] = useState("");
  const [kind, setKind] = useState<SimulationEvent["kind"]>("foreign_notice");
  const [regulator, setRegulator] = useState(SEED_NOTICE.regulator);
  const [title, setTitle] = useState(SEED_NOTICE.title);
  const [text, setText] = useState(SEED_NOTICE.text);
  const target = simId || waiting?.id || sims[0]?.id || "";
  const sim = sims.find((s) => s.id === target);
  const deliver = useMutation(() =>
    api.deliverSimulationEvent(target, kind === "foreign_notice" ? { kind, notice: { regulator, title, text, language: "en" } } : { kind }),
  );
  const info = useMemo(() => EVENTS.find((e) => e.kind === kind), [kind]);
  return (
    <Panel title="Deliver a staged event">
      <div className="stack-sm">
        <p className="small muted">Events arrive while a run is in progress, as they would in real use. A foreign notice needs a run that is waiting for one. In this demo the staged notice also reaches CASE-1052, which is waiting for a signature.</p>
        <div className="adm-opts">
          <Field label="Run">
            {(id) => <Select id={id} value={target} onChange={(e) => setSimId(e.target.value)} options={sims.map((s) => ({ value: s.id, label: `${s.id} (${humanize(s.status)})` }))} />}
          </Field>
          <Field label="Event" hint={info?.hint}>
            {(id, d) => <Select id={id} aria-describedby={d} value={kind} onChange={(e) => setKind(e.target.value as typeof kind)} options={EVENTS.map((e) => ({ value: e.kind, label: e.label }))} />}
          </Field>
        </div>
        {kind === "foreign_notice" ? (
          <div className="adm-opts">
            <Field label="Regulator (synthetic)">{(id) => <TextInput id={id} value={regulator} onChange={(e) => setRegulator(e.target.value)} />}</Field>
            <Field label="Title">{(id) => <TextInput id={id} value={title} onChange={(e) => setTitle(e.target.value)} />}</Field>
            <div className="span-2"><Field label="Notice text">{(id) => <TextArea id={id} rows={3} value={text} onChange={(e) => setText(e.target.value)} />}</Field></div>
          </div>
        ) : null}
        {sim?.status === "waiting_for_event" ? <Callout tone="look" title={`${sim.id} is waiting`}>{sim.outcome}</Callout> : null}
        {deliver.error ? <Callout tone="held" title={toProblem(deliver.error).title}>{toProblem(deliver.error).detail}</Callout> : null}
        <div>
          <Button variant="primary" icon="send" loading={deliver.pending} disabled={!target} onClick={() => void deliver.run().then((s) => { toast.success(s.outcome ?? "Event delivered"); onDone(); }).catch(() => undefined)}>
            Deliver event
          </Button>
        </div>
      </div>
    </Panel>
  );
}

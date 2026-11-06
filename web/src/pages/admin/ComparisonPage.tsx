// Governed vs ungoverned: real runs of the same scenario with the gate on and off,
// side by side.
import "./admin.css";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api";
import type { Simulation } from "../../api/types";
import { Callout, ErrorState, Field, PageHeader, Pill, Select, SkeletonLines } from "../../components";
import { useApi } from "../../hooks/useApi";
import { formatRelative } from "../../lib/format";
import { humanize } from "../../lib/labels";

const PAIRS = [
  { key: "sim-11|sim-12", label: "Repealed law cited", governed: "sim-11", ungoverned: "sim-12" },
  { key: "sim-13|sim-14", label: "Hidden instruction text", governed: "sim-13", ungoverned: "sim-14" },
];

function Head({ sim, kind }: { sim: Simulation | undefined; kind: "pass" | "fail" }) {
  return (
    <div className={`adm-cmp-head ${kind}`}>
      <div className="row-between">
        <h2>{kind === "pass" ? "Governed" : "Ungoverned"}</h2>
        {kind === "pass" ? <Pill tone="pass" icon="shield">Gate on</Pill> : <Pill tone="held" icon="alert-triangle">Gate off</Pill>}
      </div>
      {sim ? (
        <div className="small" style={{ marginTop: 6 }}>
          <code>{sim.id}</code> · {humanize(sim.status)} · {sim.execution === "live_model" ? "live model" : "replay"} · {formatRelative(sim.started_at)}
          <div style={{ marginTop: 4 }}>{sim.outcome}</div>
        </div>
      ) : null}
    </div>
  );
}

export default function ComparisonPage() {
  const [pair, setPair] = useState(PAIRS[0].key);
  const p = PAIRS.find((x) => x.key === pair) ?? PAIRS[0];
  const cmp = useApi(() => api.compareSimulations({ governed: p.governed, ungoverned: p.ungoverned }), [pair]);
  const g = useApi(() => api.getSimulation(p.governed), [pair]);
  const u = useApi(() => api.getSimulation(p.ungoverned), [pair]);

  return (
    <div className="stack">
      <PageHeader
        title="Governed vs ungoverned"
        subtitle="The same scenario run twice: once with the rules gate on, as the product runs, and once with it off. Both are real runs, not a mock-up."
        actions={
          <Field label="Scenario">{(id) => <Select id={id} value={pair} onChange={(e) => setPair(e.target.value)} options={PAIRS.map((x) => ({ value: x.key, label: x.label }))} />}</Field>
        }
      />
      <Callout tone="look" icon="beaker" title="Simulation only">
        The ungoverned run exists to show what the gate prevents. It uses synthetic files, and turning the gate off is refused outside simulation. Start more runs on <Link to="/admin/simulations">Simulations</Link>.
      </Callout>
      {cmp.error ? (
        <ErrorState error={cmp.error} onRetry={cmp.reload} />
      ) : !cmp.data ? (
        <SkeletonLines lines={8} />
      ) : (
        <div className="adm-cmp" role="group" aria-label={`${p.label}: governed and ungoverned runs`}>
          <Head sim={g.data} kind="pass" />
          <Head sim={u.data} kind="fail" />
          {cmp.data.rows.map((r) => {
            const bad = /^(yes|pass|accepted)/i.test(r.ungoverned) && /^(no|person|held|0)/i.test(r.governed);
            return (
              <div key={r.aspect} style={{ display: "contents" }}>
                <div className="adm-cmp-aspect">{r.aspect}</div>
                <div className="adm-cmp-cell small">
                  <span className="visually-hidden">Governed: </span>
                  {r.governed}
                </div>
                <div className={`adm-cmp-cell small ${bad ? "bad" : ""}`}>
                  <span className="visually-hidden">Ungoverned: </span>
                  {r.ungoverned}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

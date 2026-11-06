// The route as a swimlane: one row per actor (company, authority staff, sign-off,
// government record), one column per step in order. Lines show the flow. Every step is
// a button; the list below the diagram gives the same order for keyboard and screen readers.
import type { RouteDefinition } from "../../../api/types";
import { Icon } from "../../../components";
import { AUTONOMY_LABEL, isLocked, LANES, laneOf } from "../packEdit";

const LABEL_W = 124;
const COL_W = 114;
const ROW_H = 84;

export function Swimlane({ route, selectedId, onSelect }: { route: RouteDefinition; selectedId: string | null; onSelect: (id: string) => void }) {
  const steps = route.steps;
  const width = LABEL_W + steps.length * COL_W;
  const height = LANES.length * ROW_H;
  const center = (i: number) => {
    const lane = LANES.findIndex((l) => l.id === laneOf(steps[i]));
    return { x: LABEL_W + i * COL_W + COL_W / 2, y: lane * ROW_H + ROW_H / 2 };
  };
  const paths = steps.slice(1).map((_, k) => {
    const a = center(k);
    const b = center(k + 1);
    const x1 = a.x + 52;
    const x2 = b.x - 52;
    const mid = (x1 + x2) / 2;
    return { d: `M${x1} ${a.y} H${mid} V${b.y} H${x2 - 5}`, ax: x2, ay: b.y, key: steps[k + 1].id };
  });
  return (
    <div className="ow-swim-scroll" role="group" aria-label={`Workflow for ${route.label}, as a swimlane diagram`}>
      <div className="ow-swim" style={{ gridTemplateColumns: `${LABEL_W}px repeat(${steps.length}, ${COL_W}px)`, gridTemplateRows: `repeat(${LANES.length}, ${ROW_H}px)`, width }}>
        {LANES.map((l, i) => (
          <div key={l.id} className="ow-lane-band" style={{ gridRow: i + 1, background: i % 2 === 0 ? "var(--surface)" : "var(--surface-2)" }} />
        ))}
        {LANES.map((l, i) => (
          <div key={`${l.id}-label`} className="ow-lane-label" style={{ gridColumn: 1, gridRow: i + 1, background: i % 2 === 0 ? "var(--surface)" : "var(--surface-2)" }}>
            {l.label}
            <small>{l.hint}</small>
          </div>
        ))}
        <svg className="ow-lines" width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
          {paths.map((p) => (
            <g key={p.key}>
              <path d={p.d} />
              <polygon points={`${p.ax},${p.ay} ${p.ax - 6},${p.ay - 4} ${p.ax - 6},${p.ay + 4}`} />
            </g>
          ))}
        </svg>
        {steps.map((s, i) => {
          const lane = LANES.findIndex((l) => l.id === laneOf(s));
          return (
            <button
              key={s.id}
              type="button"
              className="ow-node"
              title={`${s.name}. Agent: ${AUTONOMY_LABEL[s.agent_autonomy]}`}
              aria-pressed={selectedId === s.id}
              aria-label={`Step ${i + 1}: ${s.name}. ${LANES[lane].label}.${isLocked(s) ? " Locked." : ""}`}
              style={{ gridColumn: i + 2, gridRow: lane + 1 }}
              onClick={() => onSelect(s.id)}
            >
              <span className="ow-node-num" aria-hidden="true">
                {i + 1}
              </span>
              {isLocked(s) ? <Icon className="ow-node-lock" name="lock" size={11} /> : null}
              <span className="ow-node-name">{s.name}</span>
              <span className="ow-node-sub">
                {s.deadline_days !== null && s.deadline_days !== undefined ? `${s.deadline_days} ${s.day_type === "calendar" ? "cal." : "work"} days` : "No deadline"}
              </span>
              {lane === 0 && s.kind !== "question" ? null : <span className="ow-node-sub">Agent: {AUTONOMY_LABEL[s.agent_autonomy]}</span>}
            </button>
          );
        })}
      </div>
    </div>
  );
}

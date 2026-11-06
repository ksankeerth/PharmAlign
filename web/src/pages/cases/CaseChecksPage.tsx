// Checks: pipeline results by stage, read like CI checks. Each row: status, method,
// whether it blocks, and a summary. Error looks different from a failed check, skipped
// says why, and superseded generations are hidden but one click away.
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../../api";
import type { CheckResult, StageStatus } from "../../api/types";
import { useSession } from "../../app/session";
import { Button, Callout, Checkbox, ErrorState, Icon, Panel, SeverityBadge, SkeletonLines, StatusPill, type Tone, checkTone, useToast } from "../../components";
import { useApi, useMutation } from "../../hooks/useApi";
import { formatDateTime, formatRelative } from "../../lib/format";
import { CHECK_STATUS_LABEL } from "../../lib/labels";
import { MethodTag } from "./shared";
import { actsAs } from "../../lib/roles";

const CLEAR = new Set(["pass", "confirmed", "waived"]);
const SEVERITY_ORDER: Tone[] = ["held", "error", "look", "accent", "pass", "neutral"];

function worstTone(items: CheckResult[]): Tone {
  const tones = items.map((c) => checkTone(c.status));
  return SEVERITY_ORDER.find((t) => tones.includes(t)) ?? "neutral";
}

function countsText(items: CheckResult[]): string {
  const counts = new Map<string, number>();
  for (const c of items) counts.set(c.status, (counts.get(c.status) ?? 0) + 1);
  return [...counts.entries()].map(([s, n]) => `${n} ${CHECK_STATUS_LABEL[s as CheckResult["status"]].toLowerCase()}`).join(", ");
}

function rowClass(c: CheckResult): string {
  if (c.status === "error") return "cw-check-row-error";
  if (c.status === "skipped" || c.status === "pending") return "cw-check-row-muted";
  if (!c.blocking || CLEAR.has(c.status)) return "";
  const tone = checkTone(c.status);
  return tone === "held" ? "cw-check-row-held" : tone === "look" ? "cw-check-row-look" : "cw-check-row-accent";
}

function CheckRow({ c, canRerun }: { c: CheckResult; canRerun: boolean }) {
  const toast = useToast();
  const rerun = useMutation(() => api.recheckCheck(c.id, { comment: "Re-run after a failure that was not about the file." }));
  const [failure, setFailure] = useState<unknown>(null);
  return (
    <li>
      <div className={`cw-check-row ${rowClass(c)}`}>
        <div>
          <StatusPill status={c.status} size="sm" />
        </div>
        <div className="cw-check-cell-main">
          <div className="cw-check-title">
            <code>{c.check_id}</code>
            <Link to={c.id}>{c.name}</Link>
          </div>
          <div className="cw-check-summary">{c.summary}</div>
          {c.status === "error" ? (
            <div className="cw-error-note">
              <Icon name="alert-octagon" size={12} />
              Could not run. This is not a finding about the file.
            </div>
          ) : null}
          {failure ? <ErrorState error={failure} /> : null}
        </div>
        <div className="cw-check-cell-method">
          <MethodTag method={c.method} />
        </div>
        <div className="cw-check-cell-sev">{!c.blocking ? (
            <SeverityBadge severity="info" label="Advisory" />
          ) : CLEAR.has(c.status) ? (
            <span className="cw-method" title="Blocks promotion if it is not clear. It is clear now.">
              <Icon name="lock" size={12} />
              Blocking
            </span>
          ) : (
            <SeverityBadge severity="blocking" />
          )}</div>
        <div className="cw-check-meta cw-check-cell-meta">
          {c.status === "error" && canRerun ? (
            <Button
              size="sm"
              icon="refresh"
              loading={rerun.pending}
              onClick={async () => {
                setFailure(null);
                try {
                  await rerun.run();
                  toast.info(`Re-running ${c.check_id}`);
                } catch (e) {
                  setFailure(e);
                }
              }}
            >
              Re-run
            </Button>
          ) : (
            <>
              {c.attempt > 1 ? <span>Attempt {c.attempt}</span> : null}
              <time dateTime={c.updated_at} title={formatDateTime(c.updated_at)}>
                {formatRelative(c.updated_at)}
              </time>
            </>
          )}
        </div>
      </div>
    </li>
  );
}

function StageGroup({ id, stage, items, canRerun }: { id: string; stage: StageStatus; items: CheckResult[]; canRerun: boolean }) {
  const needsAttention = items.some((c) => !CLEAR.has(c.status) && c.status !== "skipped");
  const [open, setOpen] = useState(true);
  const panelId = `stage-${stage.key}`;
  return (
    <section className="panel" id={id} aria-labelledby={`${panelId}-title`}>
      <div className="panel-head">
        <button type="button" className="cw-stage-head" aria-expanded={open} aria-controls={panelId} onClick={() => setOpen((o) => !o)}>
          <Icon name={open ? "chevron-down" : "chevron-right"} size={14} />
          <h2 id={`${panelId}-title`}>{stage.label}</h2>
          <span className="small muted">{countsText(items)}</span>
          {!needsAttention ? <Icon name="check" size={14} label="All clear or skipped" /> : null}
        </button>
      </div>
      {open ? (
        <div className="panel-body panel-body-flush" id={panelId}>
          <ul className="cw-check-list">
            {items.map((c) => (
              <CheckRow key={c.id} c={c} canRerun={canRerun} />
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}

function Superseded({ caseId }: { caseId: string }) {
  const [show, setShow] = useState(false);
  const old = useApi(() => api.listCaseChecks(caseId, { history: "superseded" }), [caseId]);
  const items = old.data?.items ?? [];
  return (
    <div className="stack-sm">
      <Checkbox
        checked={show}
        onChange={(e) => setShow(e.target.checked)}
        label={`Show superseded generations (${items.length})`}
        hint="Earlier results are kept and can be read. They no longer count."
      />
      {show ? (
        !old.data ? (
          <SkeletonLines lines={2} />
        ) : items.length ? (
          <Panel title="Superseded results" padded={false}>
            <ul className="cw-check-list">
              {items.map((c) => (
                <li key={c.id}>
                  <div className="cw-check-row cw-check-row-muted">
                    <div>
                      <StatusPill status={c.status} size="sm" />
                    </div>
                    <div className="cw-check-cell-main">
                      <div className="cw-check-title">
                        <code>{c.check_id}</code>
                        <Link to={encodeURIComponent(c.id)}>{c.name}</Link>
                      </div>
                      <div className="cw-check-summary">{c.summary}</div>
                    </div>
                    <div className="cw-check-cell-method">
                      <MethodTag method={c.method} />
                    </div>
                    <div className="cw-check-cell-sev xsmall muted num">
                      Gen {c.generation}, attempt {c.attempt}
                    </div>
                    <div className="cw-check-meta cw-check-cell-meta">
                      <time dateTime={c.updated_at}>{formatRelative(c.updated_at)}</time>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          </Panel>
        ) : (
          <p className="small muted">No check on this case has been re-run yet.</p>
        )
      ) : null}
    </div>
  );
}

export default function CaseChecksPage() {
  const { caseId = "" } = useParams();
  const { view } = useSession();
  const checks = useApi(() => api.listCaseChecks(caseId), [caseId]);
  const detail = useApi(() => api.getCase(caseId), [caseId]);
  const runId = detail.data?.current_pipeline_run_id;
  const run = useApi(() => (runId ? api.getPipelineRun(runId) : Promise.resolve(null)), [runId]);

  if (checks.error) return <ErrorState error={checks.error} onRetry={checks.reload} />;
  if (!checks.data || !detail.data || (runId && !run.data && !run.error)) return <SkeletonLines lines={8} />;

  const items = checks.data.items;
  const byId = new Map(items.map((c) => [c.id, c]));
  const stages = run.data?.stages ?? [];
  const placed = new Set(stages.flatMap((s) => s.check_ids));
  const unplaced = items.filter((c) => !placed.has(c.id));
  const groups: StageStatus[] = [...stages, ...(unplaced.length ? [{ key: "other", label: "Other checks", check_ids: unplaced.map((c) => c.id) }] : [])];
  const blockingOpen = items.filter((c) => c.blocking && !CLEAR.has(c.status));
  const errors = items.filter((c) => c.status === "error");
  const canAct = actsAs(view, "case_officer") || actsAs(view, "decision_maker");

  return (
    <div className="stack">
      <div className="row-between">
        <div className="small">
          <strong className="num">{items.length}</strong> checks: {countsText(items)}.{" "}
          {blockingOpen.length ? (
            <span className="strong">
              {blockingOpen.length} blocking check{blockingOpen.length === 1 ? "" : "s"} not clear.
            </span>
          ) : (
            <span>Every blocking check is clear.</span>
          )}
        </div>
        {run.data ? (
          <div className="xsmall muted">
            Run <code>{run.data.id}</code>, generation {run.data.generation}, {run.data.status}
            {run.data.finished_at ? `, finished ${formatRelative(run.data.finished_at)}` : `, started ${formatRelative(run.data.started_at)}`}
          </div>
        ) : null}
      </div>

      <nav className="cw-pipeline" aria-label="Pipeline stages">
        {groups.map((s) => {
          const members = s.check_ids.flatMap((id) => (byId.get(id) ? [byId.get(id) as CheckResult] : []));
          return (
            <a key={s.key} href={`#${s.key}`} className={`cw-pipeline-stage cw-tone-${worstTone(members)}`}>
              <strong>{s.label}</strong>
              <span>{countsText(members) || "No checks"}</span>
            </a>
          );
        })}
      </nav>

      {errors.length ? (
        <Callout tone="error" title={`${errors.length} check${errors.length === 1 ? "" : "s"} could not run`}>
          An Error is an infrastructure failure, not a finding. It blocks until it re-runs, but says nothing about the file.
        </Callout>
      ) : null}

      {groups.map((s) => {
        const members = s.check_ids.flatMap((id) => (byId.get(id) ? [byId.get(id) as CheckResult] : []));
        return members.length ? <StageGroup key={s.key} id={s.key} stage={s} items={members} canRerun={canAct} /> : null;
      })}

      <Superseded caseId={caseId} />
    </div>
  );
}

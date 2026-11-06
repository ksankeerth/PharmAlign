// Setup tracker: the nine stages for a country, what done means, blockers and the
// action; readiness checks with proof. Sign-off belongs to a Process Owner.
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../../api";
import type { ReadinessCheck, SetupStage, SetupStageStatus, Workspace } from "../../api/types";
import { Button, ButtonLink, Callout, ConfirmModal, EmptyState, ErrorState, KeyValue, PageHeader, Panel, Pill, Skeleton, Table, type Tone, useToast } from "../../components";
import { useSession } from "../../app/session";
import { useApi, useMutation } from "../../hooks/useApi";
import { formatDate, formatDateTime } from "../../lib/format";
import { humanize } from "../../lib/labels";
import { NewWorkspaceModal } from "./NewWorkspaceModal";
import { Metric, usePeopleNames } from "./shared";

const STAGES: Record<SetupStage, { label: string; to?: string; action: string; who: string }> = {
  workspace: { label: "Workspace", action: "Review workspace settings", who: "Platform Admin" },
  library: { label: "Document library", to: "/admin/library", action: "Open library", who: "Platform Admin, Process Owner reviews" },
  rules_and_workflows: { label: "Rules and workflows", action: "Process Owner edits the pack", who: "Process Owner" },
  watch_sources: { label: "Watch sources", to: "/admin/watch-sources", action: "Open watch sources", who: "Platform Admin" },
  connections: { label: "Government connection", to: "/admin/connections", action: "Open connections", who: "Platform Admin, Process Owner approves use" },
  people_and_notifications: { label: "People and notifications", to: "/admin/people", action: "Open people and roles", who: "Platform Admin" },
  agents_and_limits: { label: "Agents and limits", to: "/admin/agents", action: "Open agent health", who: "Platform Admin" },
  test_and_simulate: { label: "Test and simulate", to: "/admin/simulations", action: "Open simulations", who: "Platform Admin" },
  sign_off: { label: "Sign-off", action: "Waits for a Process Owner", who: "Process Owner" },
};

const STATUS: Record<SetupStageStatus["status"], { tone: Tone; label: string; icon: "check" | "clock" | "pause" | "circle-dashed" }> = {
  done: { tone: "pass", label: "Done", icon: "check" },
  in_progress: { tone: "look", label: "In progress", icon: "clock" },
  blocked: { tone: "held", label: "Blocked", icon: "pause" },
  not_started: { tone: "neutral", label: "Not started", icon: "circle-dashed" },
};

const CHECK: Record<ReadinessCheck["status"], { tone: Tone; label: string; icon: "check" | "x" | "beaker" | "circle-dashed" }> = {
  pass: { tone: "pass", label: "Pass", icon: "check" },
  fail: { tone: "held", label: "Fail", icon: "x" },
  mock: { tone: "look", label: "Mock", icon: "beaker" },
  not_run: { tone: "neutral", label: "Not run", icon: "circle-dashed" },
};

export default function SetupPage() {
  const { me, workspace: ws, workspaces, switchWorkspace } = useSession();
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const [confirm, setConfirm] = useState<SetupStageStatus | null>(null);
  const names = usePeopleNames();
  // The active workspace of the session, so the page follows a switch from the workspace menu.
  const wsId = ws?.id ?? me?.workspace_id ?? "";
  // The session's workspace is a summary, so the settings come from the Platform Admin's list.
  const settings = workspaces.find((w) => w.id === wsId);
  const setup = useApi(() => api.getSetup(wsId), [wsId], { enabled: wsId !== "" });
  const readiness = useApi(() => api.getReadiness(wsId).catch(() => null), [wsId], { enabled: wsId !== "" });
  const runReadiness = useMutation(() => api.startReadinessRun(wsId));

  const newOpen = params.get("new") === "1";
  const openNew = () =>
    setParams(
      (p) => {
        const next = new URLSearchParams(p);
        next.set("new", "1");
        return next;
      },
      { replace: true },
    );
  const closeNew = () =>
    setParams(
      (p) => {
        const next = new URLSearchParams(p);
        next.delete("new");
        return next;
      },
      { replace: true },
    );
  const onCreated = async (created: Workspace) => {
    toast.success(`Created ${created.name}`);
    try {
      await switchWorkspace(created.id);
    } catch {
      toast.info(`Choose ${created.name} from the workspace menu at the top to set it up.`);
    }
  };

  // Poll while a readiness run is going.
  const running = readiness.data?.status === "running";
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => readiness.reload(), 1200);
    return () => clearInterval(t);
  }, [running, readiness]);

  const stages = setup.data?.stages ?? [];
  const done = stages.filter((s) => s.status === "done").length;
  const blockers = stages.reduce((n, s) => n + s.blockers.length, 0);
  const checks = readiness.data?.checks ?? [];
  const passing = checks.filter((c) => c.status === "pass" || c.status === "mock").length;
  const signOff = stages.find((s) => s.stage === "sign_off");

  return (
    <div className="stack">
      <PageHeader
        title="Setup tracker"
        subtitle="Nine stages take a country from an empty workspace to sign-off. Nothing reaches real cases before sign-off."
        status={ws ? <Pill tone={ws.mode === "draft" ? "look" : "pass"} icon={ws.mode === "draft" ? "pen" : "check"}>{humanize(ws.mode)}</Pill> : undefined}
        actions={
          <Button variant="primary" icon="plus" onClick={openNew}>
            New country workspace
          </Button>
        }
      />

      <div className="adm-metrics">
        <Metric label="Stages done" value={`${done} of ${stages.length || 9}`} tone={done === stages.length && stages.length ? "pass" : "neutral"} />
        <Metric label="Open blockers" value={blockers} tone={blockers ? "held" : "pass"} note={blockers ? "Listed under each stage" : "Nothing blocks setup"} />
        <Metric label="Readiness checks passing" value={checks.length ? `${passing} of ${checks.length}` : "None"} note={readiness.data?.finished_at ? `Run ${formatDateTime(readiness.data.finished_at)}` : undefined} tone={checks.length && passing === checks.length ? "pass" : "look"} />
        <Metric label="Mode" value={ws ? humanize(ws.mode) : "..."} note={ws?.mode === "draft" ? "Not yet reaching cases" : "Signed off"} />
      </div>

      <Callout tone="accent" icon="stamp" title="Sign-off is done by a Process Owner">
        Platform Admin prepares every stage and runs the readiness checks. A named Process Owner then approves the pack, the knowledge snapshot and the mode on their own screen.
        {signOff?.status === "done" && signOff.completed_by ? ` Signed off by ${names(signOff.completed_by)} on ${formatDate(signOff.completed_at)}.` : " Sign-off has not happened yet."}
      </Callout>

      <Panel title="The nine stages" padded={false}>
        {setup.error ? (
          <div style={{ padding: 16 }}>
            <ErrorState error={setup.error} onRetry={setup.reload} />
          </div>
        ) : !setup.data ? (
          <div style={{ padding: 16 }}>
            <Skeleton width="80%" />
          </div>
        ) : (
          <ol className="adm-stages">
            {stages.map((s, i) => {
              const m = STAGES[s.stage];
              const st = STATUS[s.status];
              return (
                <li key={s.stage} className={`adm-stage adm-stage-${s.status}`}>
                  <span className="adm-stage-num" aria-hidden="true">
                    {i + 1}
                  </span>
                  <div className="stack-sm">
                    <div className="adm-stage-title">
                      <span>{m.label}</span>
                      <Pill tone={st.tone} icon={st.icon} size="sm">
                        {st.label}
                      </Pill>
                      <span className="muted xsmall" style={{ fontWeight: 400 }}>
                        Done by {m.who}
                      </span>
                    </div>
                    <div className="small">
                      <span className="muted">Done means: </span>
                      {s.done_when}
                    </div>
                    {s.blockers.length ? (
                      <ul className="adm-blockers" aria-label={`Blockers for ${m.label}`}>
                        {s.blockers.map((b) => (
                          <li key={b}>{b}</li>
                        ))}
                      </ul>
                    ) : null}
                    {s.status === "done" ? (
                      <div className="xsmall muted">
                        Completed by {names(s.completed_by)} on {formatDate(s.completed_at)}
                      </div>
                    ) : null}
                  </div>
                  <div className="adm-stage-actions">
                    {m.to ? (
                      <ButtonLink to={m.to} size="sm" iconRight="arrow-right">
                        {m.action}
                      </ButtonLink>
                    ) : (
                      <span className="small muted">{m.action}</span>
                    )}
                    {s.status !== "done" && s.stage !== "sign_off" ? (
                      <Button size="sm" variant="ghost" onClick={() => setConfirm(s)}>
                        Mark done
                      </Button>
                    ) : null}
                  </div>
                </li>
              );
            })}
          </ol>
        )}
      </Panel>

      <Panel
        title="Readiness checks"
        actions={
          <Button size="sm" variant="primary" icon="play" loading={runReadiness.pending || running} onClick={() => void runReadiness.run().then(readiness.reload).catch(() => undefined)}>
            Run readiness checks
          </Button>
        }
        padded={false}
      >
        {readiness.error ? (
          <div style={{ padding: 16 }}>
            <ErrorState error={readiness.error} onRetry={readiness.reload} />
          </div>
        ) : (
          <Table
            rows={readiness.data === undefined ? undefined : checks}
            loading={readiness.loading}
            rowKey={(c) => c.key}
            caption="Readiness checks"
            empty={<EmptyState title="No readiness run yet" icon="list-checks">Run the checks to see what passes and what blocks sign-off.</EmptyState>}
            columns={[
              { key: "label", header: "Check", render: (c) => <span className="strong">{c.label}</span> },
              {
                key: "status",
                header: "Result",
                render: (c) => {
                  const r = CHECK[c.status];
                  return (
                    <Pill tone={r.tone} icon={r.icon} size="sm" title={c.status === "mock" ? "Passed against the built-in mock, not a live system." : undefined}>
                      {r.label}
                    </Pill>
                  );
                },
              },
              { key: "detail", header: "What was found", render: (c) => <span className="small">{c.detail ?? <span className="muted">Not run yet</span>}</span> },
              { key: "proof", header: "Proof", hideBelow: 900, render: (c) => (c.proof_ref ? <code>{c.proof_ref}</code> : <span className="muted small">No proof recorded</span>) },
            ]}
          />
        )}
        {readiness.data ? (
          <div className="panel-foot small muted">
            {running ? "Checks are running." : readiness.data.all_passed ? "All checks pass. A Process Owner can sign off." : "Not every check passes yet, so sign-off is not possible. Fix the failing checks, then run them again."}
          </div>
        ) : null}
      </Panel>

      {ws && settings ? (
        <Panel title="Workspace">
          <KeyValue
            columns={3}
            items={[
              { label: "Country", value: ws.name },
              { label: "Authority", value: ws.authority_name },
              { label: "Languages", value: settings.languages.join(", ").toUpperCase() },
              { label: "Time zone", value: settings.time_zone },
              { label: "Day counting", value: humanize(settings.day_counting) },
              { label: "Data location", value: settings.data_location },
            ]}
          />
        </Panel>
      ) : null}

      <NewWorkspaceModal open={newOpen} onClose={closeNew} onCreated={onCreated} />

      <ConfirmModal
        open={confirm !== null}
        onClose={() => setConfirm(null)}
        title={`Mark "${confirm ? STAGES[confirm.stage].label : ""}" done`}
        summary={
          confirm ? (
            <div className="stack-sm small">
              <div>
                <strong>Done means:</strong> {confirm.done_when}
              </div>
              <div className="muted">The server re-tests this. If a blocker remains, it refuses and lists what is left.</div>
            </div>
          ) : null
        }
        confirmLabel="Mark done"
        onConfirm={async () => {
          if (confirm) await api.completeSetupStage({ workspace_id: wsId, stage: confirm.stage }, {});
        }}
        onDone={() => setup.reload()}
      />
    </div>
  );
}

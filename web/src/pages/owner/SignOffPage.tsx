// Sign-off: the Process Owner's go-live step. It names the readiness run, the approved pack
// version, the frozen library and the mode the country moves to. The server refuses a
// sign-off whose guards fail, and each reason is shown as it was given.
import { useEffect, useState } from "react";
import { api, ApiError, toProblem } from "../../api";
import type { ReadinessCheck, ReadinessRun, SignOff } from "../../api/types";
import { useSession } from "../../app/session";
import { Button, ButtonLink, Callout, EmptyState, ErrorState, Field, GuardList, KeyValue, PageHeader, Panel, Pill, Select, SkeletonLines, type Tone, TextArea, useToast } from "../../components";
import { useApi } from "../../hooks/useApi";
import { formatDateTime } from "../../lib/format";
import { humanize } from "../../lib/labels";
import { loadAll, PACK_STATE_LABEL, usePersonName } from "./ownerShared";

const CHECK: Record<ReadinessCheck["status"], { tone: Tone; label: string }> = {
  pass: { tone: "pass", label: "Pass" },
  fail: { tone: "held", label: "Fail" },
  mock: { tone: "look", label: "Mock" },
  not_run: { tone: "neutral", label: "Not run" },
};

const RUN_STATUS: Record<ReadinessRun["status"], { tone: Tone; label: string }> = {
  running: { tone: "look", label: "Running" },
  completed: { tone: "pass", label: "Completed" },
  failed: { tone: "held", label: "Failed" },
};

const MODES: { value: SignOff["target_mode"]; label: string; hint: string }[] = [
  { value: "simulation", label: "Simulation", hint: "Cases run on synthetic data only. Nothing reaches a real applicant." },
  { value: "shadow", label: "Shadow", hint: "Real cases are read and assessed, but a person still decides everything." },
  { value: "live", label: "Live", hint: "New cases start under this pack and this library." },
];

export default function SignOffPage() {
  const { me, workspace } = useSession();
  const ws = workspace?.id ?? me?.workspace_id ?? "";
  const toast = useToast();
  const name = usePersonName();
  const { data, error, loading, reload } = useApi(
    async () => {
      const [readiness, packs, snapshots] = await Promise.all([
        api.getReadiness(ws).catch((e: unknown) => {
          if (e instanceof ApiError && e.status === 404) return null;
          throw e;
        }),
        loadAll((cursor) => api.listPackVersions(ws, { cursor, limit: 200 })),
        loadAll((cursor) => api.listSnapshots(ws, { cursor, limit: 200 })),
      ]);
      return { readiness, packs: packs.filter((p) => p.state === "approved" || p.state === "active"), snapshots };
    },
    [ws],
    { enabled: Boolean(ws) },
  );
  const [packId, setPackId] = useState("");
  const [snapshotId, setSnapshotId] = useState("");
  const [mode, setMode] = useState<SignOff["target_mode"]>("simulation");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<unknown>(null);
  const [signed, setSigned] = useState<SignOff | null>(null);

  // Start from the version in use, or the newest approved one, and the latest frozen library.
  useEffect(() => {
    if (!data) return;
    setPackId((cur) => (data.packs.some((p) => p.id === cur) ? cur : (data.packs.find((p) => p.state === "active") ?? data.packs.reduce<(typeof data.packs)[number] | undefined>((a, p) => (!a || p.version > a.version ? p : a), undefined))?.id ?? ""));
    setSnapshotId((cur) => (data.snapshots.some((s) => s.id === cur) ? cur : (data.snapshots[0]?.id ?? "")));
  }, [data]);

  const run = data?.readiness ?? null;
  const ready = Boolean(run && packId && snapshotId && reason.trim());
  const sign = async () => {
    if (!run || !ready) return;
    setBusy(true);
    setRefusal(null);
    try {
      const so = await api.signOffWorkspace(ws, { readiness_run_id: run.id, pack_version_id: packId, kb_snapshot_id: snapshotId, target_mode: mode, reason: reason.trim() });
      setSigned(so);
      setReason("");
      toast.success(`Signed off to ${MODES.find((m) => m.value === so.target_mode)?.label.toLowerCase() ?? so.target_mode} mode`);
      reload();
    } catch (e) {
      setSigned(null);
      setRefusal(e);
    } finally {
      setBusy(false);
    }
  };

  const failing = run?.checks.filter((c) => c.status !== "pass") ?? [];
  const problem = refusal ? toProblem(refusal) : null;
  const hint = MODES.find((m) => m.value === mode)?.hint;
  return (
    <>
      <PageHeader
        title="Sign-off"
        subtitle={<>Move {workspace?.name ?? "this country"} to a new mode. You name the readiness run, the approved pack version and the frozen library that stay in force. The server refuses the sign-off if any guard fails.</>}
        status={workspace ? <Pill tone={workspace.mode === "draft" ? "look" : "pass"}>{humanize(workspace.mode)}</Pill> : undefined}
      />
      {error ? (
        <ErrorState error={error} onRetry={reload} />
      ) : !data ? (
        loading ? <SkeletonLines lines={6} /> : null
      ) : (
        <div className="ow-stack">
          <Panel title="Latest readiness run">
            {!run ? (
              <EmptyState title="No readiness run yet" icon="list-checks" action={<ButtonLink to="/admin/setup" variant="secondary">Open the setup tracker</ButtonLink>}>
                A Platform Admin runs the readiness checks from the setup tracker. Sign-off needs a run that passed.
              </EmptyState>
            ) : (
              <div className="stack">
                <KeyValue
                  columns={3}
                  items={[
                    { label: "Status", value: <Pill tone={RUN_STATUS[run.status].tone} size="sm">{RUN_STATUS[run.status].label}</Pill> },
                    { label: "All checks passed", value: run.all_passed ? "Yes" : "No" },
                    { label: "Started", value: formatDateTime(run.started_at) },
                  ]}
                />
                {failing.length ? (
                  <ul className="small" style={{ margin: 0, paddingLeft: 18 }} aria-label="Checks that did not pass">
                    {failing.map((c) => (
                      <li key={c.key}>
                        <strong>{c.label}</strong> <Pill tone={CHECK[c.status].tone} size="sm">{CHECK[c.status].label}</Pill>
                        {c.detail ? <span className="muted"> {c.detail}</span> : null}
                      </li>
                    ))}
                  </ul>
                ) : run.status === "completed" ? (
                  <div className="small muted">Every check passed.</div>
                ) : null}
                <div>
                  <ButtonLink to="/admin/setup" variant="ghost" size="sm">Open the setup tracker</ButtonLink>
                </div>
              </div>
            )}
          </Panel>

          <Panel title="What to sign off">
            <div className="stack">
              <Field label="Pack version" hint="Only approved or active versions can be signed.">
                {(id, d) => (
                  <Select
                    id={id}
                    aria-describedby={d}
                    value={packId}
                    onChange={(e) => setPackId(e.target.value)}
                    placeholder={data.packs.length ? undefined : "No approved version yet"}
                    disabled={!data.packs.length}
                    options={data.packs.map((p) => ({ value: p.id, label: `v${p.version}, ${PACK_STATE_LABEL[p.state]}${p.approved_by ? `, approved by ${name(p.approved_by)}` : ""}` }))}
                  />
                )}
              </Field>
              <Field label="Frozen library" hint="The snapshot of approved documents that new cases use. Freeze one on the Adoptions page.">
                {(id, d) => (
                  <Select
                    id={id}
                    aria-describedby={d}
                    value={snapshotId}
                    onChange={(e) => setSnapshotId(e.target.value)}
                    placeholder={data.snapshots.length ? undefined : "No snapshot yet"}
                    disabled={!data.snapshots.length}
                    options={data.snapshots.map((s) => ({ value: s.id, label: `${s.label}, ${s.item_count} version${s.item_count === 1 ? "" : "s"}, ${formatDateTime(s.created_at)}` }))}
                  />
                )}
              </Field>
              <Field label="Mode" hint={hint}>
                {(id, d) => <Select id={id} aria-describedby={d} value={mode} onChange={(e) => setMode(e.target.value as SignOff["target_mode"])} options={MODES.map((m) => ({ value: m.value, label: m.label }))} />}
              </Field>
              <Field label="Reason" required hint="Kept in the audit log with your name.">
                {(id, d) => <TextArea id={id} aria-describedby={d} rows={3} value={reason} onChange={(e) => setReason(e.target.value)} />}
              </Field>
              {!data.packs.length ? (
                <Callout tone="look" title="No pack version is ready">Approve a pack version first. A second person approves it on the Packs and versions page.</Callout>
              ) : null}
              {!data.snapshots.length ? <Callout tone="look" title="The library is not frozen">Freeze the library on the Adoptions page, then come back.</Callout> : null}
              {refusal && problem ? (
                refusal instanceof ApiError && refusal.failed.length ? (
                  <GuardList guards={refusal.failed} title={problem.title} />
                ) : (
                  <Callout tone="held" title={problem.title}>{problem.detail}</Callout>
                )
              ) : null}
              <div className="ow-actions">
                <Button variant="primary" icon="stamp" disabled={!ready} loading={busy} onClick={sign}>Sign off</Button>
              </div>
            </div>
          </Panel>

          {signed ? (
            <Panel title="Signed off">
              <div className="stack">
                <Callout tone="pass" title={`${workspace?.name ?? "This country"} is now in ${signed.target_mode} mode`}>{signed.reason}</Callout>
                <KeyValue
                  columns={3}
                  items={[
                    { label: "Signed by", value: name(signed.signed_by) },
                    { label: "When", value: formatDateTime(signed.signed_at) },
                    { label: "Mode", value: humanize(signed.target_mode) },
                    { label: "Pack version", value: <code>{signed.pack_version_id}</code> },
                    { label: "Frozen library", value: <code>{signed.kb_snapshot_id}</code> },
                    { label: "Readiness run", value: <code>{signed.readiness_run_id}</code> },
                  ]}
                />
              </div>
            </Panel>
          ) : null}
        </div>
      )}
    </>
  );
}

// Approve and activate. A draft is simulated, a second Process Owner approves it with a
// reason, then it is activated for new cases. The guards are shown before anyone presses
// a button, and the server enforces them again.
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ApiError } from "../../api";
import { useSession } from "../../app/session";
import { Button, ButtonLink, Callout, Checkbox, ConfirmModal, ErrorState, GuardList, KeyValue, PageHeader, Panel, Pill, SkeletonLines, useToast } from "../../components";
import { useApi } from "../../hooks/useApi";
import { formatDateTime } from "../../lib/format";
import { approvalReadiness } from "./packEdit";
import { HashText, PackLoading, PackNav, PackStatePill, usePack, usePersonName } from "./ownerShared";

// A base in another workspace can't be read from here, so its changes can't be shown.
function diffUnavailable(error: unknown, reload: () => void) {
  if (error instanceof ApiError && error.status === 404) return "The base version is in another workspace, so its changes can't be shown here.";
  return <ErrorState error={error} onRetry={reload} />;
}

export default function PackApprovalPage() {
  const { versionId = "" } = useParams();
  const { me } = useSession();
  const name = usePersonName();
  const toast = useToast();
  const pack = usePack(versionId);
  const validation = useApi(() => api.validatePack(versionId), [versionId]);
  const versions = useApi(() => api.listPackVersions(pack.data?.workspace_id ?? ""), [pack.data?.workspace_id], { enabled: Boolean(pack.data) });
  const scenarios = useApi(() => api.listScenarios(), []);
  const diff = useApi(() => api.getPackDiff(versionId, { against: pack.data!.based_on_version_id! }), [versionId, pack.data?.based_on_version_id], { enabled: Boolean(pack.data?.based_on_version_id) });
  const [picked, setPicked] = useState<string[] | null>(null);
  const [running, setRunning] = useState(false);
  const [simError, setSimError] = useState<unknown>(null);
  const [approveOpen, setApproveOpen] = useState(false);
  const [activateOpen, setActivateOpen] = useState(false);
  const [justActivated, setJustActivated] = useState(false);

  useEffect(() => {
    if (scenarios.data && picked === null) setPicked(scenarios.data.items.filter((s) => s.kind === "normal").map((s) => s.id));
  }, [scenarios.data, picked]);
  useEffect(() => {
    if (pack.data && pack.data.state !== "draft") setRunning(false);
  }, [pack.data]);

  if (!pack.data) return <PackLoading error={pack.error} reload={pack.reload} />;
  const p = pack.data;
  const errors = validation.data?.issues.filter((i) => i.severity === "error").length ?? 0;
  const readiness = approvalReadiness(p, me?.person_id, errors);
  const refused = readiness.filter((g) => !g.passed);
  const active = versions.data?.items.find((v) => v.state === "active" && v.id !== p.id);
  const mine = me?.person_id === p.created_by;
  const guards = readiness.map((g) => ({ guard: g.guard, passed: g.passed, detail: g.detail, check_id: null }));

  const runSimulation = async () => {
    setRunning(true);
    setSimError(null);
    try {
      await api.simulatePack(p.id, { scenario_ids: picked ?? [], execution: "replay" });
    } catch (e) {
      setRunning(false);
      setSimError(e);
    }
  };

  return (
    <>
      <PageHeader
        breadcrumbs={[{ label: "Packs and versions", to: "/owner/packs" }, { label: `v${p.version}`, to: `/owner/packs/${p.id}` }, { label: "Approve and activate" }]}
        title={`Approve and activate v${p.version}`}
        status={<PackStatePill state={p.state} />}
        subtitle="Nobody approves their own change. A second Process Owner reads what changed, and says why they approve."
      />
      <div className="ow-stack">
        <PackNav pack={p} />

        <div className="ow-split">
          <Panel title="This version">
            <KeyValue
              columns={2}
              items={[
                { label: "State", value: <PackStatePill state={p.state} size="sm" /> },
                { label: "Written by", value: <>{name(p.created_by)}{mine ? <> <Pill tone="look" size="sm">You</Pill></> : null}</> },
                { label: "Approved by", value: p.approved_by ? `${name(p.approved_by)}, ${formatDateTime(p.approved_at)}` : "Not yet" },
                { label: "Content hash", value: <HashText hash={p.content_hash} chars={14} /> },
                { label: "Based on", value: p.based_on_version_id ? <Link to={`/owner/packs/${p.id}/diff`}>{versions.data?.items.find((v) => v.id === p.based_on_version_id) ? `v${versions.data.items.find((v) => v.id === p.based_on_version_id)!.version}` : p.based_on_version_id}</Link> : "A template" },
                { label: "Changes from its base", value: diff.data ? <Link to={`/owner/packs/${p.id}/diff`}>{diff.data.changes.length} path{diff.data.changes.length === 1 ? "" : "s"} changed</Link> : diff.error ? diffUnavailable(diff.error, diff.reload) : p.based_on_version_id ? "Loading" : "Not applicable" },
              ]}
            />
          </Panel>
          <Panel title="Before approval">
            {validation.data || mine || p.state ? <GuardList guards={guards} title={refused.length ? `${refused.length} guard${refused.length === 1 ? "" : "s"} not yet met` : "Every guard is met"} /> : <SkeletonLines lines={3} />}
            {mine && p.state !== "active" && p.state !== "retired" ? (
              <p className="small" style={{ margin: "10px 0 0" }}>
                You are viewing as {name(me?.person_id)}, who wrote this version. Use "View as" in the top bar to switch to the second Process Owner.
              </p>
            ) : null}
          </Panel>
        </div>

        {p.state === "draft" || p.state === "simulated" ? (
          <Panel
            title="1. Simulate"
            actions={p.state === "simulated" ? <Pill tone="pass" icon="check">Simulated</Pill> : running ? <Pill tone="accent" icon="loader" spin>Running</Pill> : null}
          >
            <p className="muted small" style={{ marginTop: 0 }}>
              Replays scenarios against this version without touching real cases. Any change to the draft sends it back here.
            </p>
            {scenarios.data ? (
              <div className="ow-checklist" role="group" aria-label="Scenarios" style={{ maxHeight: 260 }}>
                {scenarios.data.items.map((s) => (
                  <Checkbox
                    key={s.id}
                    checked={(picked ?? []).includes(s.id)}
                    onChange={(e) => setPicked((cur) => (e.target.checked ? [...(cur ?? []), s.id] : (cur ?? []).filter((x) => x !== s.id)))}
                    label={<><strong>{s.label}</strong> <span className="muted small">{s.description}</span></>}
                  />
                ))}
              </div>
            ) : scenarios.error ? (
              <Callout tone="held" title="Scenarios are not available">They can only be listed in a simulation workspace.</Callout>
            ) : (
              <SkeletonLines lines={3} />
            )}
            <div className="ow-actions" style={{ marginTop: 12 }}>
              <Button variant={p.state === "simulated" ? "secondary" : "primary"} icon="beaker" loading={running} disabled={!picked?.length || errors > 0} onClick={runSimulation}>
                {p.state === "simulated" ? "Simulate again" : "Run simulation"}
              </Button>
              {errors > 0 ? (
                <span className="ow-inline-err">Fix {errors} validation error{errors === 1 ? "" : "s"} first. <Link to={`/owner/packs/${p.id}/validate`}>Open validation</Link></span>
              ) : null}
            </div>
            {simError ? (
              <div style={{ marginTop: 8 }}>
                {simError instanceof ApiError && simError.failed.length ? <GuardList guards={simError.failed} /> : <Callout tone="held" title="The simulation did not start">{simError instanceof Error ? simError.message : "Try again."}</Callout>}
              </div>
            ) : null}
          </Panel>
        ) : null}

        {p.state === "draft" || p.state === "simulated" ? (
          <Panel title="2. Approve">
            <p className="muted small" style={{ marginTop: 0 }}>The reason is saved in the audit log with your name.</p>
            <Button variant="primary" icon="user-check" onClick={() => setApproveOpen(true)}>Approve v{p.version}</Button>
            {refused.length ? <p className="small ow-inline-err" style={{ marginBottom: 0 }}>Not ready: {refused.map((g) => g.detail).join(" ")}</p> : null}
          </Panel>
        ) : null}

        {p.state === "approved" || justActivated || p.state === "active" ? (
          <Panel title={p.state === "active" ? "Active" : "3. Activate"}>
            {p.state === "active" ? (
              <Callout tone="pass" title={justActivated ? `v${p.version} is now active` : `v${p.version} is the active version`}>
                New cases start on v{p.version}. Cases that were already running stay on the version they started with. {active ? "" : ""}
              </Callout>
            ) : (
              <>
                <Callout tone="neutral" title="What activating does">
                  New cases start on v{p.version}.{active ? ` v${active.version} is retired and no new case uses it.` : ""} Cases already running stay on their version; nothing changes for them.
                </Callout>
                <div style={{ marginTop: 12 }}>
                  <Button variant="primary" icon="play" onClick={() => setActivateOpen(true)}>Activate for new cases</Button>
                </div>
              </>
            )}
          </Panel>
        ) : null}

        {p.state === "retired" ? (
          <Callout tone="neutral" title="Retired">This version was replaced. Cases that started on it stay on it. To use its content again, start a new draft from it on the packs list.</Callout>
        ) : null}

        <div className="row">
          <ButtonLink to={`/owner/packs/${p.id}/diff`} variant="secondary">See what changed</ButtonLink>
          <ButtonLink to="/owner/packs" variant="ghost">All versions</ButtonLink>
        </div>
      </div>

      <ConfirmModal
        open={approveOpen}
        onClose={() => setApproveOpen(false)}
        title={`Approve v${p.version}`}
        confirmLabel="Approve"
        requireReason
        reasonLabel="Why do you approve this version?"
        summary={<KeyValue items={[{ label: "Written by", value: name(p.created_by) }, { label: "You", value: name(me?.person_id) }, { label: "Content hash", value: <HashText hash={p.content_hash} chars={14} /> }]} />}
        onConfirm={async (reason) => {
          await api.approvePack(p.id, { reason });
          toast.success(`v${p.version} approved`);
        }}
      />
      <ConfirmModal
        open={activateOpen}
        onClose={() => setActivateOpen(false)}
        title={`Activate v${p.version}`}
        confirmLabel="Activate"
        summary={
          <div className="stack-sm">
            <p style={{ margin: 0 }}>New cases will start on v{p.version}.{active ? ` v${active.version} is retired.` : ""}</p>
            <p style={{ margin: 0 }}>Cases already running stay on the version they started with.</p>
          </div>
        }
        onConfirm={async () => {
          await api.activatePack(p.id);
          setJustActivated(true);
          toast.success(`v${p.version} is active for new cases`);
        }}
      />
    </>
  );
}

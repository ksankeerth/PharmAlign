// Signing package: exactly what the Decision Maker signs. The package is pinned when
// opened, so what is on screen is what the fingerprint covers. If anything changes
// after that, signing is refused with every failed guard and the new fingerprint, and
// the person reloads and reads again. A Case Officer who opens this page sees why they
// can't sign.
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, isApiError, toProblem } from "../../api";
import type { CheckResult, DecisionType, SigningPackage } from "../../api/types";
import { useSession } from "../../app/session";
import { Button, ButtonLink, Callout, CaseStatusPill, type Column, ErrorState, Field, FingerprintNote, GuardList, Icon, KeyValue, Modal, PageHeader, Panel, Pill, SkeletonLines, StatusPill, SyntheticTag, Table, TextArea, useToast } from "../../components";
import { useApi } from "../../hooks/useApi";
import { formatDateTime, formatRelative, shortFingerprint } from "../../lib/format";
import { humanize, RECOMMENDATION_LABEL } from "../../lib/labels";
import { DECISION_TYPE, LEGAL_EFFECT, METHOD, RECOMMENDATION_TONE, Refusal, useNames } from "./shared";
import { actsAs, roleLabel } from "../../lib/roles";

/** Why this person can't sign: their view, and what they did on this case. */
function NotYourSignature({ caseId, error }: { caseId: string; error: unknown }) {
  const { me, view } = useSession();
  const names = useNames();
  const detail = useApi(() => api.getCase(caseId), [caseId]);
  const timeline = useApi(() => api.getCaseTimeline(caseId), [caseId]);
  const promoted = timeline.data?.items.find((e) => e.kind === "promoted");
  const meId = me?.person_id;
  const didPromote = promoted?.actor_id === meId;
  const isAssessor = detail.data?.assigned_to === meId;
  const problem = toProblem(error);
  return (
    <div className="stack">
      <Callout tone="held" icon="lock" title="You can't sign this case">
        Signing needs a person who took no part in the assessment. You are {names(meId)}, a {roleLabel(view) || "signed-in person"}.
      </Callout>
      <div className="cw-pair">
        <GuardList
          title="Separation of duties"
          guards={[
            { guard: "signer_is_decision_maker", passed: actsAs(view, "decision_maker"), detail: actsAs(view, "decision_maker") ? "You can sign for this role." : "Only a Decision Maker may open or sign this package.", check_id: null },
            { guard: "signer_did_not_assess", passed: !isAssessor, detail: isAssessor ? "You are the assigned officer, so you assessed this case." : "You are not recorded as assessing this case.", check_id: null },
            { guard: "signer_did_not_promote", passed: !didPromote, detail: didPromote ? "You promoted this case, so you can't sign it." : promoted ? `${names(promoted.actor_id)} promoted this case.` : "This case has not been promoted yet.", check_id: null },
          ]}
        />
        <Panel title="What the server said">
          <div className="stack-sm small">
            <p style={{ margin: 0 }}>{problem.detail ?? problem.title}</p>
            <code className="xsmall muted">
              {problem.status} {problem.code}
            </code>
            <p className="muted" style={{ margin: 0 }}>
              Use View as in the top bar and pick Dana Haddad to open this package as the Decision Maker.
            </p>
          </div>
        </Panel>
      </div>
    </div>
  );
}

// What this person opened stays pinned for this browser tab, so it survives moving to
// another screen or person and back. Only "Reload the package" replaces it.
const pinKey = (caseId: string, personId: string) => `pharmalign.signing.read.${caseId}.${personId}`;

function readPin(caseId: string, personId: string): SigningPackage | null {
  try {
    const raw = sessionStorage.getItem(pinKey(caseId, personId));
    return raw ? (JSON.parse(raw) as SigningPackage) : null;
  } catch {
    return null;
  }
}

function writePin(caseId: string, personId: string, pkg: SigningPackage | null): void {
  try {
    if (pkg) sessionStorage.setItem(pinKey(caseId, personId), JSON.stringify(pkg));
    else sessionStorage.removeItem(pinKey(caseId, personId));
  } catch {
    // Storage is unavailable: the pin lasts until the page is left.
  }
}

const isForeignRead = (g: { guard: string; passed: boolean }) => g.guard === "foreign_sources_fresh" && !g.passed;
const DUTY_GUARDS = ["signer_did_not_assess", "signer_did_not_promote", "no_declared_conflict"];

function SignDialog({
  pkg,
  type,
  accepts,
  reason,
  conditions,
  foreignReason,
  onClose,
  onSigned,
  onRefused,
}: {
  pkg: SigningPackage;
  type: DecisionType;
  accepts: boolean;
  reason: string;
  conditions: string[];
  foreignReason: string;
  onClose: () => void;
  onSigned: (decisionId: string) => void;
  onRefused: (e: unknown) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const dec = await api.signDecision(pkg.case_id, { type, fingerprint: pkg.fingerprint, recommendation_version: pkg.recommendation.version, accepts_recommendation: accepts, reason: reason || null, conditions, foreign_status_reason: foreignReason || null });
      onSigned(dec.id);
    } catch (e) {
      if (isApiError(e) && e.failed.length) {
        onRefused(e);
        onClose();
      } else {
        setError(e);
        setBusy(false);
      }
    }
  };
  const problem = error ? toProblem(error) : null;
  return (
    <Modal
      open
      onClose={onClose}
      title={`Sign: ${DECISION_TYPE[type]}`}
      busy={busy}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button variant="primary" icon="pen" onClick={submit} loading={busy}>
            Sign
          </Button>
        </>
      }
    >
      <div className="stack">
        <p className="small" style={{ margin: 0 }}>
          The server tests every guard again at this moment, against the fingerprint you read.
        </p>
        <KeyValue
          items={[
            { label: "Decision", value: DECISION_TYPE[type] },
            { label: "Recommendation", value: `v${pkg.recommendation.version}: ${accepts ? "accepted" : "overridden"}` },
            ...(reason ? [{ label: "Reason", value: reason }] : []),
            ...(conditions.length ? [{ label: "Conditions", value: conditions.join("; ") }] : []),
            ...(foreignReason ? [{ label: "Foreign status not read", value: foreignReason }] : []),
            { label: "Legal effect", value: LEGAL_EFFECT[pkg.legal_effect] },
          ]}
        />
        <FingerprintNote fingerprint={pkg.fingerprint} label="You sign exactly this" />
        {problem ? (
          <div className="callout callout-held" role="alert">
            <Icon name="alert-octagon" />
            <div>
              <strong>{problem.title}</strong>
              {problem.detail ? <div>{problem.detail}</div> : null}
            </div>
          </div>
        ) : null}
      </div>
    </Modal>
  );
}

const CHECK_COLUMNS: Column<CheckResult>[] = [
  { key: "status", header: "Status", width: "136px", sort: (c) => c.status, render: (c) => <StatusPill status={c.status} size="sm" /> },
  { key: "check", header: "Check", sort: (c) => c.check_id, render: (c) => <span className="small"><code>{c.check_id}</code> {c.name}</span> },
  { key: "method", header: "Method", width: "90px", hideBelow: 900, render: (c) => <span className="small">{METHOD[c.method].label}</span> },
  { key: "blocking", header: "Blocks", width: "70px", render: (c) => <span className="small">{c.blocking ? "Yes" : "No"}</span> },
  { key: "summary", header: "Summary", hideBelow: 1100, render: (c) => <span className="small">{c.summary}</span> },
];

export default function SigningPackagePage() {
  const { caseId = "" } = useParams();
  const { me, view } = useSession();
  const names = useNames();
  const navigate = useNavigate();
  const toast = useToast();
  const personId = me?.person_id ?? "";
  const [reloadKey, setReloadKey] = useState(0);
  const detail = useApi(() => api.getCase(caseId), [caseId]);
  // Pinned: read once, so a background change never swaps what the person is reading.
  const pinned = useApi(
    async () => {
      const kept = readPin(caseId, personId);
      if (kept) return kept;
      const pkg = await api.getSigningPackage(caseId);
      writePin(caseId, personId, pkg);
      return pkg;
    },
    [caseId, reloadKey, personId],
    { live: false, enabled: Boolean(personId) },
  );
  const latest = useApi(() => api.getSigningPackage(caseId), [caseId, personId], { enabled: actsAs(view, "decision_maker") });
  const medicine = useApi(() => (detail.data ? api.getMedicine(detail.data.medicine_id) : Promise.resolve(null)), [detail.data?.medicine_id]);
  const parties = useApi(() => api.listParties(), []);

  const [type, setType] = useState<DecisionType>("grant");
  const [override, setOverride] = useState(false);
  const [reason, setReason] = useState("");
  const [conditionsText, setConditionsText] = useState("");
  const [foreignReason, setForeignReason] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [refusal, setRefusal] = useState<unknown>(null);

  const pkg = pinned.data;
  const proceed = pkg?.recommendation.choice === "proceed";
  // A refusal or close never agrees with a "proceed" recommendation, and a grant never agrees with a hold.
  const mustOverride = pkg ? (type === "grant" ? !proceed : proceed) : false;
  useEffect(() => {
    if (mustOverride) setOverride(true);
    else setOverride(false);
  }, [mustOverride]);

  const party = parties.data?.items.find((p) => p.id === detail.data?.applicant_party_id);
  const header = (
    <PageHeader
      eyebrow={caseId}
      breadcrumbs={[
        { label: "Case queue", to: "/cases" },
        { label: caseId, to: `/cases/${caseId}` },
        { label: "Signing package" },
      ]}
      title={medicine.data?.name ?? "Signing package"}
      status={
        <>
          {detail.data ? <CaseStatusPill status={detail.data.status} /> : null}
          <SyntheticTag />
        </>
      }
      subtitle={party ? `${party.name}. ${humanize(detail.data?.case_type)}${detail.data?.route ? `, ${humanize(detail.data.route)} route` : ""}.` : undefined}
      actions={<ButtonLink to={`/cases/${caseId}`}>Open the case</ButtonLink>}
    />
  );

  if (pinned.error && isApiError(pinned.error) && pinned.error.status === 403) {
    return (
      <div className="stack">
        {header}
        <NotYourSignature caseId={caseId} error={pinned.error} />
      </div>
    );
  }
  if (pinned.error) {
    return (
      <div className="stack">
        {header}
        <ErrorState error={pinned.error} onRetry={pinned.reload} />
      </div>
    );
  }
  if (!pkg || !detail.data) {
    return (
      <div className="stack">
        {header}
        <SkeletonLines lines={10} />
      </div>
    );
  }

  const status = detail.data.status;
  const signed = status === "signed" || status === "recording" || status === "closed";
  const changed = Boolean(latest.data && latest.data.fingerprint !== pkg.fingerprint);
  const conditions = conditionsText.split("\n").map((s) => s.trim()).filter(Boolean);
  const needsReason = override || type !== "grant";
  const failedGuards = pkg.guards.filter((g) => !g.passed);
  const dutyGuards = failedGuards.filter((g) => DUTY_GUARDS.includes(g.guard));
  const foreign = pkg.foreign_status;
  // A grant relies on foreign status being read now. If it could not be read, the signer may go on by saying why.
  const needsForeignReason = type === "grant" && (failedGuards.some(isForeignRead) || (isApiError(refusal) && refusal.failed.some(isForeignRead)));
  const reloadPackage = () => {
    setRefusal(null);
    writePin(caseId, personId, null);
    setReloadKey((k) => k + 1);
    toast.info("Package reloaded. Read it again before signing.");
  };

  return (
    <div className="stack">
      {header}

      {signed ? (
        <Callout tone="pass" icon="stamp" title="This case is already signed">
          {detail.data.decision_id ? <Link to={`/cases/decisions/${detail.data.decision_id}`}>Open the decision and its record status</Link> : "It closed without a decision."}
        </Callout>
      ) : null}

      {dutyGuards.length && !signed ? (
        <Callout tone="held" icon="user" title="You can't sign this case. Another Decision Maker must.">
          <div className="stack-sm">
            <ul className="cw-plain-list">
              {dutyGuards.map((g) => (
                <li key={g.guard}>{g.detail}</li>
              ))}
            </ul>
            <span>
              Whoever assessed, waived or promoted a case, or has declared a conflict about it, may not sign it. Ask a different Decision Maker to open this package. Nothing in the package needs to change.
            </span>
          </div>
        </Callout>
      ) : null}

      {!foreign.sources_healthy || foreign.open_notices > 0 ? (
        <Callout tone={foreign.open_notices > 0 ? "held" : "look"} icon="globe" title="Foreign status needs your attention">
          {foreign.warning ? <div>{foreign.warning}</div> : null}
          {foreign.open_notices > 0 ? (
            <div>
              {foreign.open_notices} foreign notice match{foreign.open_notices === 1 ? " is" : "es are"} open on this case. <Link to="/watch/matches">See matches</Link>
            </div>
          ) : null}
        </Callout>
      ) : null}

      {refusal ? <Refusal error={refusal} read={pkg.fingerprint} checkHref={(id) => `/cases/${caseId}/checks/${id}`} onReload={reloadPackage} reloadLabel="Reload the package" /> : null}

      {changed && !refusal && !signed ? (
        <Callout tone="look" icon="alert-triangle" title="This package changed after you opened it">
          <div className="stack-sm">
            <span>You are still looking at the version you opened. Signing it will be refused.</span>
            <FingerprintNote fingerprint={pkg.fingerprint} current={latest.data?.fingerprint} />
            <div className="row">
              <Button size="sm" icon="refresh" onClick={reloadPackage}>
                Reload the package
              </Button>
            </div>
          </div>
        </Callout>
      ) : null}

      <div className="cw-split">
        <div className="stack">
          <Panel title="What will be signed">
            <KeyValue
              columns={2}
              items={[
                { label: "Proposed action", value: pkg.proposed_action },
                {
                  label: "Legal effect",
                  value: (
                    <span className="cw-legal">
                      <Icon name="beaker" size={13} />
                      {LEGAL_EFFECT[pkg.legal_effect]}
                    </span>
                  ),
                },
                { label: "Pinned pack", value: pkg.pack_version_id, mono: true },
                { label: "Pinned library snapshot", value: pkg.kb_snapshot_id, mono: true },
              ]}
            />
          </Panel>

          <Panel title="Current recommendation">
            <div className="stack-sm">
              <div className="row">
                <code className="small">v{pkg.recommendation.version}</code>
                <Pill tone={RECOMMENDATION_TONE[pkg.recommendation.choice] ?? "neutral"} size="sm">
                  {RECOMMENDATION_LABEL[pkg.recommendation.choice] ?? humanize(pkg.recommendation.choice)}
                </Pill>
                <span className="xsmall muted">{formatDateTime(pkg.recommendation.created_at)}</span>
              </div>
              <strong className="small">{pkg.recommendation.reason}</strong>
              <p className="small" style={{ margin: 0 }}>
                {pkg.recommendation.summary}
              </p>
              {pkg.recommendation.conflicts.map((cf) => (
                <div key={cf.fact} className="small">
                  <strong>{humanize(cf.fact)}:</strong> {cf.values.join(" vs ")}
                </div>
              ))}
            </div>
          </Panel>

          <Panel title={`All checks (${pkg.checks.length})`} padded={false}>
            <Table caption="Every check in this package" dense rows={pkg.checks} rowKey={(c) => c.id} columns={CHECK_COLUMNS} onRowClick={(c) => navigate(`/cases/${caseId}/checks/${c.id}`)} />
          </Panel>

          <Panel title={`Waivers (${pkg.waivers.length})`}>
            {pkg.waivers.length ? (
              <div className="stack-sm">
                {pkg.waivers.map((w) => (
                  <div key={w.id} className="stack-sm small">
                    <div className="row">
                      <strong>{names(w.by)}</strong>
                      <span className="muted">{formatDateTime(w.at)}</span>
                      {w.valid ? (
                        <Pill tone="pass" icon="check" size="sm">
                          Valid
                        </Pill>
                      ) : (
                        <Pill tone="look" icon="alert-triangle" size="sm">
                          No longer counts
                        </Pill>
                      )}
                      <code className="xsmall" title={w.fingerprint}>
                        {shortFingerprint(w.fingerprint, 12)}
                      </code>
                    </div>
                    <span>{w.reason}</span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="small muted" style={{ margin: 0 }}>
                No check on this case was waived.
              </p>
            )}
          </Panel>

          <Panel title="Foreign status and source health">
            <KeyValue
              columns={2}
              items={[
                { label: "Sources", value: foreign.sources_healthy ? <Pill tone="pass" icon="check" size="sm">Healthy</Pill> : <Pill tone="look" icon="alert-triangle" size="sm">Unhealthy</Pill> },
                { label: "Last checked", value: foreign.last_checked_at ? formatRelative(foreign.last_checked_at) : "Never" },
                { label: "Open notices", value: String(foreign.open_notices) },
                { label: "Reliance basis", value: foreign.reliance_basis_id ?? "This case does not rely on a foreign approval", mono: Boolean(foreign.reliance_basis_id) },
              ]}
            />
          </Panel>
        </div>

        <aside className="stack cw-rail" aria-label="Sign">
          <Panel title="Sign">
            <div className="cw-reason-box">
              <FingerprintNote fingerprint={pkg.fingerprint} label="You sign exactly this" />
              {signed ? null : (
                <>
                  <fieldset className="cw-radio-group">
                    <legend>Decision</legend>
                    {(["grant", "refusal", "close"] as DecisionType[]).concat(detail.data.case_type === "reliance_rereview" ? ["licence_action"] : []).map((t) => (
                      <label key={t} className="cw-radio">
                        <input type="radio" name="decision-type" checked={type === t} onChange={() => setType(t)} />
                        <span>
                          {DECISION_TYPE[t]}
                          <span className="cw-radio-hint">{t === "grant" ? pkg.proposed_action : t === "refusal" ? "Refuse the application. Needs a reason." : t === "close" ? "Close without a government record. Needs a reason." : "Keep, vary or suspend the licence. Needs a reason."}</span>
                        </span>
                      </label>
                    ))}
                  </fieldset>

                  <fieldset className="cw-radio-group">
                    <legend>Recommendation v{pkg.recommendation.version}</legend>
                    <label className="cw-radio">
                      <input type="radio" name="accept" checked={!override} disabled={mustOverride} onChange={() => setOverride(false)} />
                      <span>
                        Accept
                        {mustOverride ? <span className="cw-radio-hint">This decision differs from the recommendation, so it overrides it.</span> : null}
                      </span>
                    </label>
                    <label className="cw-radio">
                      <input type="radio" name="accept" checked={override} onChange={() => setOverride(true)} />
                      <span>
                        Override
                        <span className="cw-radio-hint">Needs a reason.</span>
                      </span>
                    </label>
                  </fieldset>

                  <Field label={needsReason ? "Reason" : "Reason (optional)"} required={needsReason} hint="Saved with the decision and in the audit log.">
                    {(id, describedBy) => <TextArea id={id} aria-describedby={describedBy} rows={3} value={reason} onChange={(e) => setReason(e.target.value)} />}
                  </Field>
                  {needsForeignReason ? (
                    <Field label="Why foreign status could not be read" required hint="Foreign status could not be read just now. Sign anyway only if you can say why. Saved with the decision and in the audit log.">
                      {(id, describedBy) => <TextArea id={id} aria-describedby={describedBy} rows={2} value={foreignReason} onChange={(e) => setForeignReason(e.target.value)} />}
                    </Field>
                  ) : null}
                  {type === "grant" ? (
                    <Field label="Conditions (optional)" hint="One per line.">
                      {(id, describedBy) => <TextArea id={id} aria-describedby={describedBy} rows={2} value={conditionsText} onChange={(e) => setConditionsText(e.target.value)} />}
                    </Field>
                  ) : null}

                  <Button variant="primary" icon="pen" onClick={() => setConfirming(true)} disabled={needsReason && !reason.trim()}>
                    Sign: {DECISION_TYPE[type]}
                  </Button>
                  {needsReason && !reason.trim() ? <p className="xsmall muted" style={{ margin: 0 }}>Add a reason to sign this decision.</p> : null}
                </>
              )}
            </div>
          </Panel>

          <Panel title="Guards when you opened this">
            <GuardList guards={pkg.guards} onlyFailed={failedGuards.length > 0} checkHref={(id) => `/cases/${caseId}/checks/${id}`} />
            {failedGuards.length === 0 ? <p className="xsmall muted" style={{ margin: "8px 0 0" }}>The server tests them again when you sign.</p> : null}
          </Panel>

        </aside>
      </div>

      {confirming ? (
        <SignDialog
          pkg={pkg}
          type={type}
          accepts={!override}
          reason={reason.trim()}
          conditions={type === "grant" ? conditions : []}
          foreignReason={type === "grant" && needsForeignReason ? foreignReason.trim() : ""}
          onClose={() => setConfirming(false)}
          onSigned={(id) => {
            writePin(caseId, personId, null);
            toast.success(`Signed. Decision ${id} is on its way to the government record.`);
            navigate(`/cases/decisions/${id}`);
          }}
          onRefused={setRefusal}
        />
      ) : null}
    </div>
  );
}

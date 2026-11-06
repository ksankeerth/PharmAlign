// One decision: signers, the signed fingerprint, and where the government record stands.
// An unknown outcome needs a read-back, recorded by a Case Officer. A decision put on
// hold by a foreign notice after signing must be reconfirmed or revoked by a Decision Maker.
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../../api";
import type { Proposal } from "../../api/types";
import { useSession } from "../../app/session";
import { Button, ButtonLink, Callout, ErrorState, Field, FingerprintNote, KeyValue, PageHeader, Panel, Select, SkeletonLines, SyntheticTag, Table, TextArea, TextInput, useToast } from "../../components";
import { useApi } from "../../hooks/useApi";
import { formatDateTime, formatRelative } from "../../lib/format";
import { humanize, ROLE_LABEL } from "../../lib/labels";
import { ActionDialog, DECISION_TYPE, DecisionStatePill, LEGAL_EFFECT, Refusal, useNames } from "./shared";

/** queued, sent, then acknowledged, unknown or rejected. */
function RecordSteps({ proposal }: { proposal: Proposal }) {
  const s = proposal.state;
  const sent = s !== "queued";
  return (
    <ol className="cw-steps" aria-label="Government record progress">
      <li className={s === "queued" ? "cw-step-now" : "cw-step-done"}>Queued</li>
      <li className={s === "sent" ? "cw-step-now" : sent ? "cw-step-done" : ""}>Sent</li>
      {s === "unknown" ? <li className="cw-step-unknown">Outcome unknown</li> : s === "rejected" ? <li className="cw-step-bad">Rejected</li> : <li className={s === "acknowledged" ? "cw-step-done" : ""}>Acknowledged</li>}
    </ol>
  );
}

function ReadBack({ proposal }: { proposal: Proposal }) {
  const toast = useToast();
  const [outcome, setOutcome] = useState<"applied" | "rejected">("applied");
  const [govRef, setGovRef] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const ready = !busy && (outcome === "rejected" || govRef.trim());
  return (
    <form
      className="stack-sm"
      onSubmit={async (e) => {
        e.preventDefault();
        if (!ready) return;
        setBusy(true);
        setError(null);
        try {
          await api.recordManualAck(proposal.id, { outcome, gov_ref: govRef.trim() || null, note: note.trim() || null });
          toast.success("Outcome recorded");
        } catch (err) {
          setError(err);
        } finally {
          setBusy(false);
        }
      }}
    >
      <p className="small" style={{ margin: 0 }}>
        Read the government record, then record what it says. Nothing is retried until a person has checked: a blind retry could apply it twice.
      </p>
      <div className="grid-2">
        <Field label="What the record says">
          {(id) => (
            <Select
              id={id}
              value={outcome}
              onChange={(e) => setOutcome(e.target.value as "applied" | "rejected")}
              options={[
                { value: "applied", label: "Applied" },
                { value: "rejected", label: "Not applied" },
              ]}
            />
          )}
        </Field>
        <Field label={outcome === "applied" ? "Government reference" : "Government reference (optional)"} required={outcome === "applied"}>
          {(id) => <TextInput id={id} value={govRef} onChange={(e) => setGovRef(e.target.value)} />}
        </Field>
      </div>
      <Field label="Note (optional)">{(id) => <TextArea id={id} rows={2} value={note} onChange={(e) => setNote(e.target.value)} />}</Field>
      {error ? <Refusal error={error} /> : null}
      <div className="row">
        <Button type="submit" variant="primary" icon="check" disabled={!ready} loading={busy}>
          Record the outcome
        </Button>
      </div>
    </form>
  );
}

export default function DecisionDetailPage() {
  const { decisionId = "" } = useParams();
  const { view } = useSession();
  const names = useNames();
  const toast = useToast();
  const dec = useApi(() => api.getDecision(decisionId), [decisionId]);
  const proposalId = dec.data?.proposal_id;
  const proposal = useApi(() => (proposalId ? api.getProposal(proposalId) : Promise.resolve(null)), [proposalId]);
  const detail = useApi(() => (dec.data ? api.getCase(dec.data.case_id) : Promise.resolve(null)), [dec.data?.case_id]);
  const medicine = useApi(() => (detail.data ? api.getMedicine(detail.data.medicine_id) : Promise.resolve(null)), [detail.data?.medicine_id]);
  const [dialog, setDialog] = useState<"reconfirm" | "revoke" | null>(null);
  const [refusal, setRefusal] = useState<unknown>(null);

  if (dec.error) return <ErrorState error={dec.error} onRetry={dec.reload} />;
  if (!dec.data) return <SkeletonLines lines={8} />;
  const d = dec.data;
  const p = proposal.data;
  const onHold = d.state === "on_hold";
  const canHoldAct = view === "decision_maker" && onHold;
  const canReadBack = view === "case_officer" && p && (p.state === "unknown" || p.state === "sent");

  return (
    <div className="stack">
      <PageHeader
        eyebrow={d.id}
        breadcrumbs={[
          { label: "Decisions", to: "/cases/decisions" },
          { label: d.id },
        ]}
        title={`${DECISION_TYPE[d.type]}: ${medicine.data?.name ?? d.case_id}`}
        status={
          <>
            <DecisionStatePill state={d.state} />
            <SyntheticTag />
          </>
        }
        subtitle={<Link to={`/cases/${d.case_id}`}>{d.case_id}</Link>}
        actions={<ButtonLink to={`/cases/${d.case_id}/timeline`} icon="history">Case timeline</ButtonLink>}
      />

      {refusal ? <Refusal error={refusal} onDismiss={() => setRefusal(null)} /> : null}

      {onHold ? (
        <Callout tone="held" icon="pause" title="On hold: a foreign notice arrived after signing">
          <div className="stack-sm">
            <span>The record update is paused. Nothing reaches the government system until a Decision Maker reconfirms or revokes this decision.</span>
            {canHoldAct ? (
              <div className="row">
                <Button variant="primary" icon="check" onClick={() => setDialog("reconfirm")}>
                  Reconfirm
                </Button>
                <Button variant="danger" icon="ban" onClick={() => setDialog("revoke")}>
                  Revoke
                </Button>
              </div>
            ) : view === "case_officer" ? (
              <span className="small muted">Only a Decision Maker can act on this.</span>
            ) : null}
          </div>
        </Callout>
      ) : null}
      {d.record_status === "unknown" ? (
        <Callout tone="look" icon="alert-triangle" title="Record outcome unknown">
          The government system did not confirm the update. The record may or may not have changed.
          {view !== "case_officer" ? " A Case Officer reads the record back and records the outcome." : ""}
        </Callout>
      ) : null}
      {d.record_status === "rejected" ? (
        <Callout tone="held" title="The record update did not apply">
          {p?.last_error ?? "A person must handle it."}
        </Callout>
      ) : null}
      {d.record_status === "acknowledged" ? (
        <Callout tone="pass" icon="check-double" title="Recorded">
          The government record confirmed the update{p?.gov_ref ? ` as ${p.gov_ref}` : ""}, and the applicant was told.
        </Callout>
      ) : null}

      <div className="cw-split">
        <div className="stack">
          <Panel title="Decision">
            <KeyValue
              columns={2}
              items={[
                { label: "Type", value: DECISION_TYPE[d.type] },
                { label: "Legal effect", value: LEGAL_EFFECT[d.legal_effect] },
                { label: "Recommendation", value: `v${d.recommendation_version}: ${d.accepts_recommendation ? "accepted" : "overridden"}` },
                { label: "Reason", value: d.reason },
                ...(d.foreign_status_reason ? [{ label: "Why signed without foreign status", value: d.foreign_status_reason }] : []),
                { label: "Conditions", value: d.conditions.length ? d.conditions.join("; ") : null },
                { label: "Case status", value: detail.data ? humanize(detail.data.status) : null },
              ]}
            />
          </Panel>

          <Panel title={`Signers (${d.signers.length})`} padded={false}>
            <Table
              caption="People who signed this decision"
              dense
              rows={d.signers}
              rowKey={(s) => s.person_id + s.signed_at}
              columns={[
                { key: "who", header: "Person", render: (s) => <span className="small strong">{names(s.person_id)}</span> },
                { key: "role", header: "Role", render: (s) => <span className="small">{ROLE_LABEL[s.role]}</span> },
                { key: "at", header: "Signed", render: (s) => <span className="small" title={formatDateTime(s.signed_at)}>{formatDateTime(s.signed_at)} ({formatRelative(s.signed_at)})</span> },
              ]}
            />
          </Panel>

          <Panel title="Government record">
            {d.proposal_id && p ? (
              <div className="stack">
                <RecordSteps proposal={p} />
                <KeyValue
                  columns={2}
                  items={[
                    { label: "Action", value: humanize(p.action) },
                    { label: "Government reference", value: p.gov_ref, mono: true },
                    { label: "Attempts", value: String(p.attempts) },
                    { label: "Idempotency key", value: p.idempotency_key, mono: true },
                    { label: "Last updated", value: formatDateTime(p.updated_at) },
                    { label: "Last error", value: p.last_error },
                  ]}
                />
                {canReadBack ? (
                  <Panel title="Read back and record the outcome">
                    <ReadBack proposal={p} />
                  </Panel>
                ) : null}
              </div>
            ) : d.proposal_id ? (
              <SkeletonLines lines={3} />
            ) : (
              <p className="small muted" style={{ margin: 0 }}>
                No government record is needed for this decision.
              </p>
            )}
          </Panel>
        </div>

        <aside className="stack cw-rail" aria-label="Signed fingerprint">
          <Panel title="What was signed">
            <div className="stack-sm">
              <FingerprintNote fingerprint={d.signed_payload_hash} label="Signed fingerprint" />
              <p className="xsmall muted" style={{ margin: 0 }}>
                This covers the checks, waivers, recommendation, pack and snapshot as they stood when it was signed. The case is frozen from then on: late results are stored and ignored.
              </p>
            </div>
          </Panel>
        </aside>
      </div>

      <ActionDialog
        open={dialog === "reconfirm"}
        onClose={() => setDialog(null)}
        title="Reconfirm this decision"
        confirmLabel="Reconfirm"
        summary={<p className="small" style={{ margin: 0 }}>You stand by the decision despite the foreign notice. The record update continues, and you are added as a signer.</p>}
        text={{ label: "Reason", required: true }}
        onConfirm={async (reason) => {
          await api.reconfirmDecision(d.id, { fingerprint: d.signed_payload_hash, reason });
          toast.success("Decision reconfirmed. The record update continues.");
        }}
        onRefused={setRefusal}
      />
      <ActionDialog
        open={dialog === "revoke"}
        onClose={() => setDialog(null)}
        title="Revoke this decision"
        confirmLabel="Revoke"
        variant="danger"
        summary={<p className="small" style={{ margin: 0 }}>The decision is withdrawn before the record changes, and the case closes. This can't be undone.</p>}
        text={{ label: "Reason", required: true }}
        onConfirm={async (reason) => {
          await api.revokeDecision(d.id, { fingerprint: d.signed_payload_hash, reason });
          toast.success("Decision revoked");
        }}
        onRefused={setRefusal}
      />
    </div>
  );
}

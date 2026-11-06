// Promotion: what would block promotion now, guard by guard and live, and the promote
// action. The person can always try: if the server refuses, every failed guard is shown.
import { useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../../api";
import { useSession } from "../../app/session";
import { Button, ButtonLink, Callout, CaseStatusPill, ErrorState, Field, FingerprintNote, GuardList, KeyValue, PageHeader, Panel, Pill, SkeletonLines, SyntheticTag, TextArea, useToast } from "../../components";
import { useApi } from "../../hooks/useApi";
import { humanize, RECOMMENDATION_LABEL } from "../../lib/labels";
import { RECOMMENDATION_TONE, Refusal, useNames } from "./shared";

export default function PromotionPage() {
  const { caseId = "" } = useParams();
  const { me } = useSession();
  const names = useNames();
  const toast = useToast();
  const detail = useApi(() => api.getCase(caseId), [caseId]);
  const medicine = useApi(() => (detail.data ? api.getMedicine(detail.data.medicine_id) : Promise.resolve(null)), [detail.data?.medicine_id]);
  const check = useApi(() => api.getPromotionCheck(caseId), [caseId, me?.person_id]);
  const recs = useApi(() => api.listRecommendations(caseId), [caseId]);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<unknown>(null);
  const [sentFingerprint, setSentFingerprint] = useState<string | undefined>(undefined);

  const title = medicine.data?.name ?? "Promotion";
  const header = (
    <PageHeader
      eyebrow={caseId}
      breadcrumbs={[
        { label: "Case queue", to: "/cases" },
        { label: caseId, to: `/cases/${caseId}` },
        { label: "Promotion" },
      ]}
      title={title}
      status={
        <>
          {detail.data ? <CaseStatusPill status={detail.data.status} /> : null}
          <SyntheticTag />
        </>
      }
      subtitle="Promoting sends this case to a Decision Maker. A person does this, against the fingerprint shown."
      actions={<ButtonLink to={`/cases/${caseId}`}>Back to the case</ButtonLink>}
    />
  );

  if (check.error) {
    return (
      <div className="stack">
        {header}
        <ErrorState error={check.error} onRetry={check.reload} />
      </div>
    );
  }
  if (!check.data || !detail.data) {
    return (
      <div className="stack">
        {header}
        <SkeletonLines lines={8} />
      </div>
    );
  }

  const p = check.data;
  const status = detail.data.status;
  const done = status === "promoted" || status === "signed" || status === "recording" || status === "closed";
  const current = recs.data?.items.find((r) => r.is_current);
  const failed = p.guards.filter((g) => !g.passed);

  const promote = async () => {
    setBusy(true);
    setRefusal(null);
    setSentFingerprint(p.fingerprint);
    try {
      await api.promoteCase(caseId, { fingerprint: p.fingerprint, reason: reason.trim() || null });
      toast.success(`${caseId} promoted to the Decision Maker`);
      setReason("");
    } catch (e) {
      setRefusal(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack">
      {header}

      {done ? (
        <Callout tone="pass" icon="check-double" title={status === "promoted" ? "Promoted. Waiting for a Decision Maker" : "This case is past promotion"}>
          {status === "promoted" ? "The Decision Maker reads the signing package and signs against its fingerprint." : `It is now ${humanize(status).toLowerCase()}.`} Whoever assessed, waived or promoted a case can't sign it.
        </Callout>
      ) : null}

      {refusal ? (
        <Refusal
          error={refusal}
          read={sentFingerprint}
          checkHref={(id) => `/cases/${caseId}/checks/${id}`}
          onDismiss={() => setRefusal(null)}
        />
      ) : null}

      <div className="cw-split">
        <div className="stack">
          <Panel title="Guards, live">
            <GuardList guards={p.guards} checkHref={(id) => `/cases/${caseId}/checks/${id}`} />
            <p className="xsmall muted" style={{ margin: "8px 0 0" }}>
              This list updates by itself as checks, threads and questions change. The server tests the same guards again when you promote.
            </p>
          </Panel>

          {current ? (
            <Panel title="Current recommendation">
              <div className="stack-sm">
                <div className="row">
                  <code className="small">v{current.version}</code>
                  <Pill tone={RECOMMENDATION_TONE[current.choice] ?? "neutral"} size="sm">
                    {RECOMMENDATION_LABEL[current.choice] ?? humanize(current.choice)}
                  </Pill>
                </div>
                <p className="small" style={{ margin: 0 }}>
                  {current.summary}
                </p>
              </div>
            </Panel>
          ) : null}
        </div>

        <aside className="stack cw-rail" aria-label="Promote">
          <Panel title="Promote">
            <div className="cw-reason-box">
              <KeyValue
                items={[
                  { label: "Assigned to", value: names(detail.data.assigned_to) },
                  { label: "Promoting as", value: names(me?.person_id) },
                  { label: "Blocking checks open", value: String(detail.data.blocking_checks_open) },
                ]}
              />
              <FingerprintNote fingerprint={p.fingerprint} label="Promote against this fingerprint" />
              {!done ? (
                <>
                  <Field label="Reason (optional)" hint="Saved in the audit log and on the timeline.">
                    {(id, describedBy) => <TextArea id={id} aria-describedby={describedBy} rows={3} value={reason} onChange={(e) => setReason(e.target.value)} />}
                  </Field>
                  {p.can_promote ? (
                    <Button variant="primary" icon="arrow-right" onClick={promote} loading={busy}>
                      Promote to the Decision Maker
                    </Button>
                  ) : (
                    <>
                      <p className="small" style={{ margin: 0 }}>
                        {failed.length} guard{failed.length === 1 ? "" : "s"} still fail{failed.length === 1 ? "s" : ""}. You can still try: the server decides, and shows why it refused.
                      </p>
                      <Button icon="arrow-right" onClick={promote} loading={busy}>
                        Try to promote
                      </Button>
                    </>
                  )}
                </>
              ) : null}
            </div>
          </Panel>
        </aside>
      </div>
    </div>
  );
}

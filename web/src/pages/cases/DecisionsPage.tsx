// Decisions: every signed decision and where its government record stands. Decisions
// on hold and unknown record outcomes come first, because they need a person.
import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../../api";
import type { Decision, Proposal } from "../../api/types";
import { useSession } from "../../app/session";
import { Callout, type Column, EmptyState, ErrorState, Field, PageHeader, Select, SyntheticTag, Table } from "../../components";
import { useApi } from "../../hooks/useApi";
import { formatRelative, shortFingerprint } from "../../lib/format";
import { DECISION_TYPE, DecisionStatePill, RecordStatusPill, useNames } from "./shared";

interface Row {
  decision: Decision;
  proposal: Proposal | null;
  product: string;
}

async function loadDecisions(): Promise<Row[]> {
  const [decisions, medicines, cases] = await Promise.all([api.listDecisions({ limit: 200 }), api.listMedicines({ limit: 200 }), api.listCases({ status: "signed,recording,closed", limit: 200 })]);
  const medicineOf = new Map(cases.items.map((c) => [c.id, c.medicine_id]));
  const name = new Map(medicines.items.map((m) => [m.id, m.name]));
  return Promise.all(
    decisions.items.map(async (decision): Promise<Row> => {
      const proposal = decision.proposal_id ? await api.getProposal(decision.proposal_id).catch(() => null) : null;
      const medicine = medicineOf.get(decision.case_id);
      return { decision, proposal, product: medicine ? name.get(medicine) ?? medicine : decision.case_id };
    }),
  );
}

const needsPerson = (r: Row) => r.decision.state === "on_hold" || r.decision.record_status === "unknown" || r.decision.record_status === "rejected";

export default function DecisionsPage() {
  const navigate = useNavigate();
  const { me } = useSession();
  const names = useNames();
  const { data, error, loading, reload } = useApi(loadDecisions, [me?.person_id]);
  const [record, setRecord] = useState("");

  const rows = useMemo(() => {
    const filtered = (data ?? []).filter((r) => !record || r.decision.record_status === record);
    return [...filtered].sort((a, b) => Number(needsPerson(b)) - Number(needsPerson(a)) || (b.decision.signers[0]?.signed_at ?? "").localeCompare(a.decision.signers[0]?.signed_at ?? ""));
  }, [data, record]);

  const onHold = data?.filter((r) => r.decision.state === "on_hold") ?? [];
  const unknown = data?.filter((r) => r.decision.record_status === "unknown") ?? [];

  const columns: Column<Row>[] = [
    {
      key: "decision",
      header: "Decision",
      sort: (r) => r.decision.id,
      render: (r) => (
        <div className="cw-cell-main">
          <Link to={`/cases/decisions/${r.decision.id}`} className="cw-linkish" onClick={(e) => e.stopPropagation()}>
            {DECISION_TYPE[r.decision.type]}: {r.decision.case_id}
          </Link>
          <span className="cw-cell-sub">{r.product}</span>
        </div>
      ),
    },
    { key: "state", header: "State", width: "130px", sort: (r) => r.decision.state, render: (r) => <DecisionStatePill state={r.decision.state} size="sm" /> },
    {
      key: "record",
      header: "Record status",
      width: "160px",
      sort: (r) => r.decision.record_status,
      render: (r) => <RecordStatusPill status={r.decision.record_status} size="sm" />,
    },
    {
      key: "proposal",
      header: "Government proposal",
      hideBelow: 1100,
      render: (r) => (
        <div className="cw-cell-main small">
          {r.proposal ? (
            <>
              <span>
                {r.proposal.gov_ref ?? "No reference yet"}, {r.proposal.attempts} attempt{r.proposal.attempts === 1 ? "" : "s"}
              </span>
              {r.proposal.last_error ? <span className="cw-cell-sub">{r.proposal.last_error}</span> : null}
            </>
          ) : (
            <span className="muted">No record needed</span>
          )}
        </div>
      ),
    },
    {
      key: "signers",
      header: "Signed by",
      width: "150px",
      sort: (r) => r.decision.signers[0]?.signed_at,
      render: (r) => (
        <div className="cw-cell-main small">
          <span>{r.decision.signers.map((s) => names(s.person_id)).join(", ")}</span>
          <span className="cw-cell-sub">{formatRelative(r.decision.signers[0]?.signed_at)}</span>
        </div>
      ),
    },
    {
      key: "fingerprint",
      header: "Signed fingerprint",
      hideBelow: 900,
      width: "170px",
      render: (r) => (
        <code className="xsmall" title={r.decision.signed_payload_hash}>
          {shortFingerprint(r.decision.signed_payload_hash, 10)}
        </code>
      ),
    },
  ];

  return (
    <div className="stack">
      <PageHeader title="Decisions" status={<SyntheticTag />} subtitle="Signed decisions and their government record. The applicant is told only after the record is confirmed." />
      {error ? <ErrorState error={error} onRetry={reload} /> : null}
      {onHold.length ? (
        <Callout tone="held" icon="pause" title={`${onHold.length} decision${onHold.length === 1 ? "" : "s"} on hold`}>
          A foreign notice arrived after signing. A Decision Maker must reconfirm or revoke before the record changes.{" "}
          {onHold.map((r) => (
            <Link key={r.decision.id} to={`/cases/decisions/${r.decision.id}`}>
              {r.decision.case_id}
            </Link>
          ))}
        </Callout>
      ) : null}
      {unknown.length ? (
        <Callout tone="look" icon="alert-triangle" title={`${unknown.length} unknown record outcome${unknown.length === 1 ? "" : "s"}`}>
          The government system did not confirm the update. Read the record back before retrying.{" "}
          {unknown.map((r) => (
            <Link key={r.decision.id} to={`/cases/decisions/${r.decision.id}`}>
              {r.decision.case_id}
            </Link>
          ))}
        </Callout>
      ) : null}
      <div className="cw-filters">
        <Field label="Record status">
          {(id) => (
            <Select
              id={id}
              value={record}
              onChange={(e) => setRecord(e.target.value)}
              options={[
                { value: "", label: "Any status" },
                { value: "queued", label: "Queued" },
                { value: "sent", label: "Sent" },
                { value: "acknowledged", label: "Acknowledged" },
                { value: "unknown", label: "Outcome unknown" },
                { value: "rejected", label: "Rejected" },
                { value: "not_needed", label: "No record needed" },
              ]}
            />
          )}
        </Field>
      </div>
      <Table
        caption="Signed decisions"
        rows={data ? rows : undefined}
        columns={columns}
        rowKey={(r) => r.decision.id}
        loading={loading}
        onRowClick={(r) => navigate(`/cases/decisions/${r.decision.id}`)}
        empty={
          <EmptyState title="No decisions match" icon="stamp">
            {record ? "Pick another record status." : "Nothing has been signed in this workspace yet."}
          </EmptyState>
        }
      />
    </div>
  );
}

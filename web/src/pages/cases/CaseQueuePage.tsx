// Case queue: every case in this workspace with what it is waiting for, its deadline,
// and how many blocking checks are open. Held files and record problems stand out.
import { useMemo } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../../api";
import type { CaseSummary, Decision, View } from "../../api/types";
import { useSession } from "../../app/session";
import { type Column, EmptyState, ErrorState, Field, Icon, type IconName, PageHeader, Pill, Select, SyntheticTag, Table, TextInput, type Tone } from "../../components";
import { useApi } from "../../hooks/useApi";
import { formatDate, formatRelative } from "../../lib/format";
import { humanize } from "../../lib/labels";
import { useNames } from "./shared";
import { actsAs } from "../../lib/roles";
import { CaseJourney } from "./CaseJourney";

/** What a case is waiting for, in the words the queue uses. */
type QueueState = "held" | "ready" | "review" | "running" | "company" | "awaiting" | "on_hold" | "unknown" | "rejected" | "recording" | "recorded" | "closed" | "intake";

const STATE: Record<QueueState, { label: string; tone: Tone; icon: IconName; spin?: boolean }> = {
  intake: { label: "Intake", tone: "neutral", icon: "inbox" },
  running: { label: "Checks running", tone: "accent", icon: "loader", spin: true },
  held: { label: "Held", tone: "held", icon: "pause" },
  review: { label: "In review", tone: "accent", icon: "eye" },
  company: { label: "With company", tone: "neutral", icon: "send" },
  ready: { label: "Ready to promote", tone: "pass", icon: "arrow-right" },
  awaiting: { label: "Awaiting signature", tone: "look", icon: "pen" },
  on_hold: { label: "Decision on hold", tone: "held", icon: "pause" },
  unknown: { label: "Unknown record outcome", tone: "look", icon: "alert-triangle" },
  rejected: { label: "Record rejected", tone: "held", icon: "x" },
  recording: { label: "Recording", tone: "accent", icon: "upload" },
  recorded: { label: "Recorded", tone: "pass", icon: "check-double" },
  closed: { label: "Closed", tone: "neutral", icon: "check" },
};

function stateOf(c: CaseSummary, canPromote: boolean | null, decision: Decision | null): QueueState {
  if (decision) {
    if (decision.state === "on_hold") return "on_hold";
    if (decision.record_status === "unknown") return "unknown";
    if (decision.record_status === "rejected") return "rejected";
    if (decision.record_status === "acknowledged") return "recorded";
    if (decision.record_status === "queued" || decision.record_status === "sent") return "recording";
    return "closed";
  }
  switch (c.status) {
    case "intake":
      return "intake";
    case "in_pipeline":
      return "running";
    case "held":
      return "held";
    case "with_company":
      return "company";
    case "in_review":
      return canPromote ? "ready" : "review";
    case "promoted":
      return "awaiting";
    case "signed":
    case "recording":
      return "recording";
    default:
      return "closed";
  }
}

interface Row extends CaseSummary {
  state: QueueState;
  product: string;
  company: string;
}

async function loadQueue(view: View | null): Promise<Row[]> {
  const [cases, medicines, parties] = await Promise.all([api.listCases({ limit: 200 }), api.listMedicines({ limit: 200 }), api.listParties({ limit: 200 })]);
  const medicine = new Map(medicines.items.map((m) => [m.id, m.name]));
  const party = new Map(parties.items.map((p) => [p.id, p.name]));
  return Promise.all(
    cases.items.map(async (c) => {
      let canPromote: boolean | null = null;
      let decision: Decision | null = null;
      if (c.status === "in_review" && actsAs(view, "case_officer")) {
        canPromote = await api.getPromotionCheck(c.id).then((p) => p.can_promote, () => null);
      }
      if (c.status === "recording" || c.status === "signed" || c.status === "closed") {
        decision = await api
          .getCase(c.id)
          .then((d) => (d.decision_id ? api.getDecision(d.decision_id) : null))
          .catch(() => null);
      }
      return { ...c, state: stateOf(c, canPromote, decision), product: medicine.get(c.medicine_id) ?? c.medicine_id, company: party.get(c.applicant_party_id) ?? c.applicant_party_id };
    }),
  );
}

function Deadline({ row }: { row: Row }) {
  if (!row.next_deadline) return <span className="muted">None</span>;
  const days = (Date.parse(row.next_deadline) - Date.now()) / 86_400_000;
  const cls = days < 0 ? "cw-deadline-over" : days <= 3 ? "cw-deadline-soon" : "";
  return (
    <div className="cw-cell-main">
      <span className={cls}>
        {days <= 3 ? <Icon name="clock" size={12} /> : null} {formatRelative(row.next_deadline)}
      </span>
      <span className="cw-cell-sub">{row.state === "company" ? "Clock stopped" : formatDate(row.next_deadline)}</span>
    </div>
  );
}

export default function CaseQueuePage() {
  const navigate = useNavigate();
  const { view, me } = useSession();
  const names = useNames();
  const [params, setParams] = useSearchParams();
  const status = params.get("status") ?? "";
  const assignee = params.get("assignee") ?? "";
  const caseType = params.get("type") ?? "";
  const search = (params.get("q") ?? "").toLowerCase();
  const { data, error, loading, reload } = useApi(() => loadQueue(view), [view, me?.person_id]);

  const setParam = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };

  const rows = useMemo(
    () =>
      data?.filter(
        (r) =>
          (!status || r.state === status) &&
          (!assignee || (assignee === "none" ? !r.assigned_to : r.assigned_to === assignee)) &&
          (!caseType || r.case_type === caseType) &&
          (!search || `${r.id} ${r.product} ${r.company}`.toLowerCase().includes(search)),
      ),
    [data, status, assignee, caseType, search],
  );

  const assignees = [...new Set((data ?? []).map((r) => r.assigned_to).filter((x): x is string => Boolean(x)))];
  const types = [...new Set((data ?? []).map((r) => r.case_type))];
  const present = [...new Set((data ?? []).map((r) => r.state))];

  const columns: Column<Row>[] = [
    {
      key: "case",
      header: "Case",
      sort: (r) => r.id,
      render: (r) => (
        <div className="cw-cell-main">
          <Link to={`/cases/${r.id}`} className="cw-linkish" onClick={(e) => e.stopPropagation()}>
            {r.id}
          </Link>
          <span className="cw-cell-sub">{r.product}</span>
        </div>
      ),
    },
    {
      key: "state",
      header: "State",
      width: "190px",
      sort: (r) => STATE[r.state].label,
      render: (r) => {
        const s = STATE[r.state];
        return (
          <Pill tone={s.tone} icon={s.icon} spin={s.spin} size="sm">
            {s.label}
          </Pill>
        );
      },
    },
    { key: "company", header: "Company", hideBelow: 1100, sort: (r) => r.company, render: (r) => <span className="small">{r.company}</span> },
    {
      key: "type",
      header: "Type and route",
      hideBelow: 900,
      sort: (r) => r.case_type,
      render: (r) => (
        <div className="cw-cell-main small">
          <span>{humanize(r.case_type)}</span>
          <span className="cw-cell-sub">{r.route ? `${humanize(r.route)} route` : "No route yet"}</span>
        </div>
      ),
    },
    { key: "deadline", header: "Deadline", width: "130px", sort: (r) => r.next_deadline, render: (r) => <Deadline row={r} /> },
    {
      key: "blocking",
      header: "Blocking",
      align: "right",
      width: "86px",
      sort: (r) => r.blocking_checks_open,
      render: (r) =>
        r.blocking_checks_open ? (
          <span className="cw-blocking-count">
            <Icon name="lock" size={12} label="Blocking checks open" />
            {r.blocking_checks_open}
          </span>
        ) : (
          <span className="cw-blocking-none">0</span>
        ),
    },
    {
      key: "assignee",
      header: "Assignee",
      width: "120px",
      sort: (r) => names(r.assigned_to),
      render: (r) => <span className="small">{r.assigned_to === me?.person_id ? "You" : names(r.assigned_to)}</span>,
    },
  ];

  return (
    <div className="stack">
      <PageHeader
        title="Case queue"
        status={<SyntheticTag />}
        subtitle={actsAs(view, "decision_maker") ? "Cases waiting for your signature come first when you filter by Awaiting signature." : "Every case in this workspace. A person promotes and a person signs; nothing is decided automatically."}
      />

      {error ? <ErrorState error={error} onRetry={reload} /> : null}

      <CaseJourney cases={data ?? null} active={status} onPick={(s) => setParam("status", status === s ? "" : s)} />

      <div className="cw-filters">
        <Field label="Search">{(id) => <TextInput id={id} type="search" placeholder="Case, product or company" value={params.get("q") ?? ""} onChange={(e) => setParam("q", e.target.value)} />}</Field>
        <Field label="State">
          {(id) => (
            <Select
              id={id}
              value={status}
              onChange={(e) => setParam("status", e.target.value)}
              options={[{ value: "", label: "Any state" }, ...(Object.keys(STATE) as QueueState[]).filter((s) => present.includes(s) || s === status).map((s) => ({ value: s, label: STATE[s].label }))]}
            />
          )}
        </Field>
        <Field label="Assignee">
          {(id) => (
            <Select
              id={id}
              value={assignee}
              onChange={(e) => setParam("assignee", e.target.value)}
              options={[{ value: "", label: "Anyone" }, ...assignees.map((a) => ({ value: a, label: a === me?.person_id ? `${names(a)} (you)` : names(a) })), { value: "none", label: "Nobody" }]}
            />
          )}
        </Field>
        <Field label="Case type">
          {(id) => <Select id={id} value={caseType} onChange={(e) => setParam("type", e.target.value)} options={[{ value: "", label: "Any type" }, ...types.map((t) => ({ value: t, label: humanize(t) }))]} />}
        </Field>
      </div>

      <Table
        caption="Cases in this workspace"
        rows={rows}
        columns={columns}
        rowKey={(r) => r.id}
        loading={loading}
        onRowClick={(r) => navigate(`/cases/${r.id}`)}
        empty={
          <EmptyState title="No cases match these filters" icon="filter">
            Clear a filter to see more.
          </EmptyState>
        }
      />
    </div>
  );
}

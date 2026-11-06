// Case page layout: header (case, product, company, status), section tabs, the
// the Knowledge Assistant's case (its panel is in the shell), and the outlet for the active tab.
import { Outlet, useMatch, useParams } from "react-router-dom";
import { api } from "../../api";
import { useSession } from "../../app/session";
import { useAssistantCase } from "../../app/assistant/AssistantContext";
import { ButtonLink, CaseStatusPill, ErrorState, Icon, NavTabs, PageHeader, SkeletonLines, SyntheticTag } from "../../components";
import { useApi } from "../../hooks/useApi";
import { formatRelative } from "../../lib/format";
import { humanize } from "../../lib/labels";
import { useNames } from "./shared";

const PROMOTABLE = new Set(["in_review", "held", "with_company", "in_pipeline"]);

export default function CaseLayout() {
  const { caseId = "" } = useParams();
  const { view } = useSession();
  const names = useNames();
  const onCheck = useMatch("/cases/:caseId/checks/:checkResultId");
  useAssistantCase(caseId, onCheck?.params.checkResultId ?? null);
  const c = useApi(() => api.getCase(caseId), [caseId]);
  const medicine = useApi(() => (c.data ? api.getMedicine(c.data.medicine_id) : Promise.resolve(null)), [c.data?.medicine_id]);
  const parties = useApi(() => api.listParties(), []);
  const threads = useApi(() => api.listThreads(caseId, { status: "open" }), [caseId]);
  const questions = useApi(() => api.listQuestions(caseId), [caseId]);

  if (c.error) return <ErrorState error={c.error} onRetry={c.reload} />;
  if (!c.data) return <SkeletonLines lines={6} />;
  const d = c.data;
  const party = parties.data?.items.find((p) => p.id === d.applicant_party_id);
  const openQuestions = questions.data?.items.filter((q) => q.status !== "closed").length;
  const base = `/cases/${d.id}`;

  return (
    <div className="stack">
      <PageHeader
        eyebrow={d.id}
        breadcrumbs={[{ label: "Case queue", to: "/cases" }, { label: d.id }]}
        title={medicine.data?.name ?? "Loading product"}
        status={
          <>
            <CaseStatusPill status={d.status} />
            <SyntheticTag />
          </>
        }
        subtitle={
          <div className="case-head-meta">
            <span>{party?.name ?? d.applicant_party_id}</span>
            <span>{humanize(d.case_type)}</span>
            <span>{d.route ? `${humanize(d.route)} route` : "No route yet"}</span>
            <span className="num">Sequence {d.sequence}</span>
            <span className="num">{d.blocking_checks_open} blocking open</span>
            <span>Assigned to {names(d.assigned_to)}</span>
            {d.next_deadline ? <span>Deadline {formatRelative(d.next_deadline)}</span> : null}
            {d.days_used !== null ? <span className="num">Day {d.days_used}</span> : null}
            {!d.clock_running && d.status !== "closed" && d.status !== "recording" ? (
              <span>
                <Icon name="pause" size={12} /> Clock stopped
              </span>
            ) : null}
          </div>
        }
        actions={
          <>
            {d.decision_id ? (
              <ButtonLink to={`/cases/decisions/${d.decision_id}`} icon="stamp">
                Decision
              </ButtonLink>
            ) : null}
            {view === "case_officer" && PROMOTABLE.has(d.status) ? (
              <ButtonLink to={`${base}/promote`} variant="primary" icon="arrow-right">
                Promotion
              </ButtonLink>
            ) : null}
            {d.status === "promoted" && view === "case_officer" ? (
              <ButtonLink to={`${base}/signing`} icon="pen">
                Signing package
              </ButtonLink>
            ) : null}
            {view === "decision_maker" && d.status === "promoted" ? (
              <ButtonLink to={`${base}/signing`} variant="primary" icon="pen">
                Signing package
              </ButtonLink>
            ) : null}
          </>
        }
      />
      <NavTabs
        label="Case sections"
        items={[
          { to: base, label: "Overview", end: true },
          { to: `${base}/checks`, label: "Checks", badge: d.blocking_checks_open || undefined },
          { to: `${base}/evidence`, label: "Evidence" },
          { to: `${base}/threads`, label: "Review threads", badge: threads.data?.items.length || undefined },
          { to: `${base}/questions`, label: "Questions", badge: openQuestions || undefined },
          { to: `${base}/timeline`, label: "Timeline" },
        ]}
      />
      <Outlet />
    </div>
  );
}

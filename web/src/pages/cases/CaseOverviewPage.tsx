// Overview: the case at a glance. The recommendation with every version, items to
// confirm and conflicts (built by code from the check results), extracted facts with
// their evidence, reliance basis and foreign status, and what blocks promotion now.
import { Link, useParams } from "react-router-dom";
import { api } from "../../api";
import type { CheckResult, DossierDocument, FactValue, RecommendationVersion } from "../../api/types";
import { useSession } from "../../app/session";
import { ButtonLink, Callout, EmptyState, ErrorState, GuardList, Icon, KeyValue, Panel, Pill, SkeletonLines, StatusPill, Table } from "../../components";
import { useApi } from "../../hooks/useApi";
import { formatDate, formatDateTime, formatRelative, shortFingerprint } from "../../lib/format";
import { factLabel, humanize, RECOMMENDATION_LABEL } from "../../lib/labels";
import { describeSource, evidenceHref, parseAnchor, RECOMMENDATION_TONE, useCaseDocs, useNames } from "./shared";

function Recommendation({ rec, checks, caseId }: { rec: RecommendationVersion; checks: CheckResult[]; caseId: string }) {
  const byResult = new Map(checks.map((c) => [c.id, c]));
  const byCheck = new Map(checks.map((c) => [c.check_id, c]));
  return (
    <div className={`cw-rec ${rec.is_current ? "cw-rec-current" : "cw-rec-old"}`}>
      <div className="cw-rec-head">
        <span className="cw-rec-version">v{rec.version}</span>
        {rec.is_current ? (
          <Pill tone="accent" icon="check" size="sm">
            Current
          </Pill>
        ) : (
          <Pill tone="neutral" icon="history" size="sm">
            Earlier version
          </Pill>
        )}
        <Pill tone={RECOMMENDATION_TONE[rec.choice] ?? "neutral"} size="sm">
          {RECOMMENDATION_LABEL[rec.choice] ?? humanize(rec.choice)}
        </Pill>
        <span className="xsmall muted">{formatDateTime(rec.created_at)}</span>
      </div>
      <strong className="small">{rec.reason}</strong>
      <p className="cw-rec-summary">{rec.summary}</p>

      {rec.items_to_confirm.length ? (
        <div>
          <h3 className="cw-section-title">Items to confirm</h3>
          <ul className="cw-confirm-list">
            {rec.items_to_confirm.map((id) => {
              const c = byResult.get(id);
              return (
                <li key={id}>
                  {c ? <StatusPill status={c.status} size="sm" /> : null}
                  <Link to={`/cases/${caseId}/checks/${id}`}>{c ? `${c.check_id} ${c.name}` : id}</Link>
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}

      {rec.conflicts.length ? (
        <div>
          <h3 className="cw-section-title">Conflicts</h3>
          {rec.conflicts.map((cf) => (
            <div className="cw-conflict" key={cf.fact}>
              <span className="strong">{humanize(cf.fact)}</span>
              <div className="stack-sm">
                <div className="cw-conflict-values">
                  {cf.values.map((v, i) => (
                    <span key={v} className="row">
                      {i > 0 ? <span className="muted xsmall">vs</span> : null}
                      <span className="cw-conflict-value">{v}</span>
                    </span>
                  ))}
                </div>
                <div className="row xsmall">
                  {cf.check_ids.map((id) => {
                    const c = byCheck.get(id);
                    return c ? (
                      <Link key={id} to={`/cases/${caseId}/checks/${c.id}`}>
                        {id}
                      </Link>
                    ) : (
                      <span key={id}>{id}</span>
                    );
                  })}
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function FactEvidence({ fact, caseId, docs }: { fact: FactValue; caseId: string; docs?: DossierDocument[] }) {
  if (!fact.evidence.length) return <span className="muted small">No quote</span>;
  return (
    <div className="stack-sm" style={{ gap: 4 }}>
      {fact.evidence.map((e) => {
        const a = parseAnchor(e.anchor);
        return (
          <div key={e.anchor + e.quote} className="small">
            <span>"{e.quote}"</span>{" "}
            {a.kind === "dossier" && a.page ? (
              <Link className="xsmall" to={evidenceHref(caseId, a.doc, a.page)}>
                {describeSource(e.anchor, docs)}
              </Link>
            ) : (
              <span className="xsmall muted">{describeSource(e.anchor, docs)}</span>
            )}
          </div>
        );
      })}
    </div>
  );
}

const FACT_TONE = { found: "pass", not_found: "held", ambiguous: "look" } as const;

function ForeignPanel({ caseId }: { caseId: string }) {
  const { view } = useSession();
  const basis = useApi(() => api.listCaseRelianceBasis(caseId), [caseId]);
  const matches = useApi(() => api.listMatches({ limit: 200 }), [caseId]);
  const sources = useApi(async () => {
    if (view === "decision_maker") {
      const pkg = await api.getSigningPackage(caseId).catch(() => null);
      return pkg ? { healthy: pkg.foreign_status.sources_healthy, warning: pkg.foreign_status.warning, last: pkg.foreign_status.last_checked_at } : null;
    }
    if (view !== "case_officer") return null;
    const list = await api.listWatchSources({ limit: 200 }).catch(() => null);
    if (!list) return null;
    const bad = list.items.filter((s) => s.health === "failing" || s.health === "zero_yield" || s.health === "layout_changed");
    const last = list.items.map((s) => s.last_success_at).filter(Boolean).sort().at(-1) ?? null;
    return { healthy: bad.length === 0, warning: bad.length ? `${bad.map((s) => s.regulator).join(", ")}: source unhealthy. Foreign status may be out of date.` : null, last };
  }, [caseId, view]);
  const here = matches.data?.items.filter((m) => m.target_type === "case" && m.target_id === caseId) ?? [];
  const open = here.filter((m) => m.status === "open");
  const items = basis.data?.items ?? [];
  return (
    <Panel title="Reliance basis and foreign status">
      <div className="stack-sm">
        {open.length ? (
          <Callout tone="held" title={`${open.length} foreign notice${open.length === 1 ? "" : "s"} open on this case`}>
            {open.map((m) => (
              <div key={m.id}>
                <Link to={`/watch/notices/${m.notice_id}`}>Notice {m.notice_id}</Link>: {m.match_basis.join(", ")}
              </div>
            ))}
          </Callout>
        ) : null}
        {sources.data?.warning ? (
          <Callout tone="look" title="Foreign sources unhealthy">
            {sources.data.warning}
          </Callout>
        ) : null}
        {items.length ? (
          items.map((r) => (
            <KeyValue
              key={r.id}
              items={[
                { label: "Regulator", value: r.regulator },
                { label: "Approval", value: r.approval_ref, mono: true },
                { label: "Approved on", value: formatDate(r.approved_on) },
                {
                  label: "Verified",
                  value: r.verified ? (
                    <Pill tone="pass" icon="check" size="sm">
                      Verified
                    </Pill>
                  ) : (
                    <Pill tone="look" icon="eye" size="sm">
                      Not verified
                    </Pill>
                  ),
                },
                { label: "Declared the same by", value: r.declared_same_by },
              ]}
            />
          ))
        ) : (
          <p className="small muted">This case does not rely on a foreign approval.</p>
        )}
        <div className="small">
          {open.length === 0 ? (
            <span className="row">
              <Icon name="check" size={13} /> No open foreign notices on this case.
            </span>
          ) : null}
          {sources.data?.last ? <span className="muted">Sources last checked {formatRelative(sources.data.last)}.</span> : null}
        </div>
      </div>
    </Panel>
  );
}

function BlockersPanel({ caseId, checks, status }: { caseId: string; checks: CheckResult[]; status: string }) {
  const { view } = useSession();
  const check = useApi(() => (view === "case_officer" ? api.getPromotionCheck(caseId) : Promise.resolve(null)), [caseId, view]);
  const threads = useApi(() => api.listThreads(caseId, { status: "open" }), [caseId]);
  const questions = useApi(() => api.listQuestions(caseId), [caseId]);
  const frozen = ["promoted", "signed", "recording", "closed"].includes(status);

  if (frozen) {
    return (
      <Panel title="What blocks promotion">
        <p className="small">{status === "promoted" ? "This case is already promoted and is waiting for a Decision Maker." : "This case is past promotion."}</p>
      </Panel>
    );
  }
  if (view === "case_officer") {
    return (
      <Panel
        title="What blocks promotion"
        actions={
          <ButtonLink size="sm" to={`/cases/${caseId}/promote`} iconRight="arrow-right">
            Promotion
          </ButtonLink>
        }
      >
        {check.data ? <GuardList guards={check.data.guards} onlyFailed={!check.data.can_promote} checkHref={(id) => `/cases/${caseId}/checks/${id}`} /> : <SkeletonLines lines={3} />}
      </Panel>
    );
  }
  const notClear = checks.filter((c) => c.blocking && !["pass", "confirmed", "waived"].includes(c.status));
  const openQuestions = questions.data?.items.filter((q) => q.status !== "closed") ?? [];
  const nothing = !notClear.length && !threads.data?.items.length && !openQuestions.length;
  return (
    <Panel title="What blocks promotion">
      {nothing ? (
        <p className="small">Nothing is open. A Case Officer can promote this case.</p>
      ) : (
        <ul className="cw-confirm-list">
          {notClear.map((c) => (
            <li key={c.id}>
              <StatusPill status={c.status} size="sm" />
              <Link to={`/cases/${caseId}/checks/${c.id}`}>{c.check_id}</Link>
            </li>
          ))}
          {threads.data?.items.length ? (
            <li>
              <Link to={`/cases/${caseId}/threads`}>
                {threads.data.items.length} open review thread{threads.data.items.length === 1 ? "" : "s"}
              </Link>
            </li>
          ) : null}
          {openQuestions.length ? (
            <li>
              <Link to={`/cases/${caseId}/questions`}>
                {openQuestions.length} open question{openQuestions.length === 1 ? "" : "s"}
              </Link>
            </li>
          ) : null}
        </ul>
      )}
    </Panel>
  );
}

export default function CaseOverviewPage() {
  const { caseId = "" } = useParams();
  const names = useNames();
  const c = useApi(() => api.getCase(caseId), [caseId]);
  const recs = useApi(() => api.listRecommendations(caseId), [caseId]);
  const checks = useApi(() => api.listCaseChecks(caseId), [caseId]);
  const facts = useApi(() => api.listCaseFacts(caseId), [caseId]);
  const docs = useCaseDocs(caseId);

  if (recs.error) return <ErrorState error={recs.error} onRetry={recs.reload} />;
  const checkItems = checks.data?.items ?? [];
  const current = recs.data?.items.find((r) => r.is_current);
  const earlier = recs.data?.items.filter((r) => !r.is_current) ?? [];

  return (
    <div className="cw-split">
      <div className="stack">
        <Panel
          title="Recommendation"
          actions={
            <span className="cw-built-by" title="Version numbers, items to confirm and conflicts come from code reading the check results. The agent writes only the summary.">
              <Icon name="settings" size={12} />
              Items and conflicts built by code
            </span>
          }
        >
          {!recs.data ? (
            <SkeletonLines lines={4} />
          ) : !current ? (
            <EmptyState title="No recommendation yet" icon="clock">
              Checks are still running. The Coordinator writes a brief when they finish.
            </EmptyState>
          ) : (
            <div className="stack-sm">
              <Recommendation rec={current} checks={checkItems} caseId={caseId} />
              {earlier.length ? (
                <details className="cw-disclosure" open={earlier.length === 1}>
                  <summary>
                    {earlier.length} earlier version{earlier.length === 1 ? "" : "s"}
                  </summary>
                  <div className="stack-sm">
                    {earlier.map((r) => (
                      <Recommendation key={r.version} rec={r} checks={checkItems} caseId={caseId} />
                    ))}
                  </div>
                </details>
              ) : null}
            </div>
          )}
        </Panel>

        <Panel title="Facts with evidence" padded={false}>
          <Table
            caption="Facts extracted from the dossier"
            dense
            rows={facts.data?.items}
            rowKey={(f) => f.key}
            loading={facts.loading}
            empty={
              <EmptyState title="No facts extracted" icon="file-text">
                Nothing has been extracted for this case yet, or the checks that extract facts did not run.
              </EmptyState>
            }
            columns={[
              { key: "fact", header: "Fact", width: "26%", sort: (f) => f.key, render: (f) => <span className="small strong">{factLabel(f.key)}</span> },
              {
                key: "value",
                header: "Value",
                width: "22%",
                render: (f) => (f.status === "found" ? <span className="small">{f.value}</span> : <Pill tone={FACT_TONE[f.status]} size="sm">{humanize(f.status)}</Pill>),
              },
              { key: "evidence", header: "Evidence", render: (f) => <FactEvidence fact={f} caseId={caseId} docs={docs.data?.items} /> },
            ]}
          />
        </Panel>
      </div>

      <div className="stack">
        {c.data ? <BlockersPanel caseId={caseId} checks={checkItems} status={c.data.status} /> : null}
        <ForeignPanel caseId={caseId} />
        {c.data ? (
          <Panel title="Case record">
            <KeyValue
              items={[
                { label: "Assigned to", value: names(c.data.assigned_to) },
                { label: "Environment", value: humanize(c.data.environment) },
                { label: "Pinned pack", value: c.data.pack_version_id, mono: true },
                { label: "Pinned library snapshot", value: c.data.kb_snapshot_id, mono: true },
                { label: "Current run", value: c.data.current_pipeline_run_id, mono: true },
                { label: "Input fingerprint", value: current ? shortFingerprint(current.input_fingerprint, 12) : null, mono: true },
                { label: "Clock", value: c.data.clock_running ? "Running" : "Stopped" },
                { label: "Opened", value: formatDate(c.data.created_at) },
              ]}
            />
          </Panel>
        ) : null}
      </div>
    </div>
  );
}

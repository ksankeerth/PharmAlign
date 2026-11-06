// Evidence: the dossier beside the law. Documents by module, one page with its cited
// quotes marked, and on the right every check that cites it with the clause it rests on.
// A suspect page shows its hidden text separately, apart from what agents read.
import { useMemo } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api } from "../../api";
import type { CheckDetail, DossierDocument, Evidence } from "../../api/types";
import { Callout, EmptyState, ErrorState, Icon, Panel, SeverityBadge, SkeletonLines, StatusPill } from "../../components";
import { useApi } from "../../hooks/useApi";
import { DossierPageView, SourceCard } from "./EvidenceViewer";
import { parseAnchor, useCaseDocs, useSourceTitles } from "./shared";

interface Citation {
  check: CheckDetail;
  evidence: Evidence;
}

function citationsOf(checks: CheckDetail[]): Citation[] {
  const out: Citation[] = [];
  for (const check of checks) {
    const seen = new Set<string>();
    for (const e of [...check.evidence, ...check.extracted.flatMap((f) => f.evidence), ...(check.challenge?.evidence ?? [])]) {
      const key = e.anchor + e.quote;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({ check, evidence: e });
    }
  }
  return out;
}

function DocumentTree({
  docs,
  citedPages,
  documentId,
  page,
  onOpen,
}: {
  docs: DossierDocument[];
  citedPages: Map<string, number[]>;
  documentId: string;
  page: number;
  onOpen: (doc: string, page: number) => void;
}) {
  const modules = [...new Set(docs.map((d) => d.module ?? "Unsorted"))];
  return (
    <nav className="cw-doc-tree" aria-label="Dossier documents">
      {modules.map((m) => (
        <div key={m}>
          <h3 className="cw-doc-module">Module {m}</h3>
          {docs
            .filter((d) => (d.module ?? "Unsorted") === m)
            .map((d) => {
              const pages = [...new Set([...d.suspect_pages, ...(citedPages.get(d.id) ?? [])])].sort((a, b) => a - b);
              return (
                <div key={d.id}>
                  <button type="button" className="cw-doc-button" aria-current={d.id === documentId} onClick={() => onOpen(d.id, pages[0] ?? 1)}>
                    {d.filename}
                    {d.sequence > 1 ? <span className="xsmall muted"> (sequence {d.sequence})</span> : null}
                  </button>
                  {pages.length ? (
                    <div className="cw-doc-pages">
                      {pages.map((p) => {
                        const suspect = d.suspect_pages.includes(p);
                        return (
                          <button
                            key={p}
                            type="button"
                            className={`cw-page-chip ${suspect ? "cw-page-chip-suspect" : ""}`}
                            aria-current={d.id === documentId && p === page}
                            aria-label={`${d.filename}, page ${p}${suspect ? ", suspect" : ", cited"}`}
                            onClick={() => onOpen(d.id, p)}
                          >
                            {suspect ? <Icon name="eye-off" size={10} /> : null}p{p}
                          </button>
                        );
                      })}
                    </div>
                  ) : null}
                </div>
              );
            })}
        </div>
      ))}
    </nav>
  );
}

function CitationsPanel({ caseId, citations, anchor }: { caseId: string; citations: Citation[]; anchor: string }) {
  const detail = useApi(() => api.getCase(caseId), [caseId], { live: false });
  const sourceOf = useSourceTitles(detail.data?.kb_snapshot_id);
  const onPage = citations.filter((c) => c.evidence.anchor === anchor);
  const checks = [...new Map(onPage.map((c) => [c.check.id, c.check])).values()];
  return (
    <div className="stack-sm">
      <Panel title={`Cited on this page (${onPage.length})`}>
        {onPage.length ? (
          <div className="stack-sm">
            {onPage.map((c) => (
              <div key={c.check.id + c.evidence.quote} className="stack-sm" style={{ gap: 4 }}>
                <div className="row small">
                  <StatusPill status={c.check.status} size="sm" />
                  <Link to={`/cases/${caseId}/checks/${c.check.id}`}>
                    {c.check.check_id} {c.check.name}
                  </Link>
                </div>
                <blockquote className="evidence-quote small">{c.evidence.quote}</blockquote>
              </div>
            ))}
          </div>
        ) : (
          <p className="small muted" style={{ margin: 0 }}>
            No check cites this page.
          </p>
        )}
      </Panel>

      <Panel title="Clauses and sources these checks rest on">
        {checks.length ? (
          <div className="stack-sm">
            {checks.map((check) => {
              const external = citations.filter((c) => c.check.id === check.id && parseAnchor(c.evidence.anchor).kind !== "dossier");
              return (
                <div key={check.id} className="stack-sm">
                  <div className="row small">
                    <code>{check.check_id}</code>
                    {check.source ? (
                      <span>
                        <code>{check.source.doc_id}</code>
                        {sourceOf(check.source.doc_id) ? ` ${sourceOf(check.source.doc_id)?.title}` : ""}
                        {check.source.clause ? `, clause ${check.source.clause}` : ""}
                      </span>
                    ) : (
                      <span className="muted">No source clause</span>
                    )}
                    <SeverityBadge severity={check.legal_force === "binding" ? "blocking" : check.legal_force === "guidance" ? "review" : "info"} label={check.legal_force === "binding" ? "Binding law" : check.legal_force === "guidance" ? "Guidance" : "Local practice"} />
                  </div>
                  {external.map((c) => (
                    <SourceCard key={c.evidence.anchor + c.evidence.quote} anchor={c.evidence.anchor} quote={c.evidence.quote} />
                  ))}
                </div>
              );
            })}
          </div>
        ) : (
          <p className="small muted" style={{ margin: 0 }}>
            Open a cited page to see its clause here.
          </p>
        )}
      </Panel>
    </div>
  );
}

export default function CaseEvidencePage() {
  const { caseId = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const docs = useCaseDocs(caseId);
  const checks = useApi(async () => {
    const list = await api.listCaseChecks(caseId);
    return Promise.all(list.items.filter((c) => c.is_current).map((c) => api.getCheck(c.id)));
  }, [caseId]);

  const citations = useMemo(() => citationsOf(checks.data ?? []), [checks.data]);
  const citedPages = useMemo(() => {
    const m = new Map<string, number[]>();
    for (const c of citations) {
      const a = parseAnchor(c.evidence.anchor);
      if (a.kind === "dossier" && a.page) m.set(a.doc, [...(m.get(a.doc) ?? []), a.page]);
    }
    return m;
  }, [citations]);

  if (docs.error) return <ErrorState error={docs.error} onRetry={docs.reload} />;
  if (!docs.data) return <SkeletonLines lines={8} />;
  const items = docs.data.items;
  if (!items.length) {
    return (
      <EmptyState title="No documents" icon="file-text">
        The company has not sent a dossier for this case yet.
      </EmptyState>
    );
  }

  const suspect = items.flatMap((d) => d.suspect_pages.map((p) => ({ doc: d, page: p })));
  const firstCited = citations.map((c) => parseAnchor(c.evidence.anchor)).find((a) => a.kind === "dossier" && a.page && items.some((d) => d.id === a.doc));
  const documentId = params.get("doc") ?? suspect[0]?.doc.id ?? firstCited?.doc ?? items[0].id;
  const page = Number(params.get("page")) || suspect[0]?.page || (firstCited?.doc === documentId ? firstCited.page : null) || 1;
  const doc = items.find((d) => d.id === documentId);
  const anchor = `${documentId}:${doc?.version ?? "v1"}:p${page}`;
  const quotes = citations.filter((c) => c.evidence.anchor === anchor).map((c) => c.evidence.quote);
  const open = (d: string, p: number) => setParams({ doc: d, page: String(p) }, { replace: true });

  return (
    <div className="stack">
      {suspect.length ? (
        <Callout tone="held" icon="eye-off" title={`${suspect.length} suspect page${suspect.length === 1 ? "" : "s"} in this dossier`}>
          Hidden text was removed from what agents read. Later stages wait for an officer.{" "}
          {suspect.map((s) => (
            <button key={s.doc.id + s.page} type="button" className="cw-page-chip cw-page-chip-suspect" onClick={() => open(s.doc.id, s.page)}>
              {s.doc.filename}, page {s.page}
            </button>
          ))}
        </Callout>
      ) : null}
      <div className="cw-evidence-page">
        <DocumentTree docs={items} citedPages={citedPages} documentId={documentId} page={page} onOpen={open} />
        <DossierPageView key={documentId} documentId={documentId} page={page} docs={items} quotes={quotes} onNavigate={(p) => open(documentId, p)} />
        {checks.error ? <ErrorState error={checks.error} onRetry={checks.reload} /> : <CitationsPanel caseId={caseId} citations={citations} anchor={anchor} />}
      </div>
    </div>
  );
}

// Pack validation: every rule the pack must meet, as errors (block simulation and
// approval) and warnings (a person should look), each linked to the place to fix it.
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../../api";
import type { PackIssue } from "../../api/types";
import { Button, ButtonLink, Callout, EmptyState, ErrorState, PageHeader, Panel, SeverityBadge, SkeletonLines, Table } from "../../components";
import { useApi } from "../../hooks/useApi";
import { humanize } from "../../lib/labels";
import { locatePath } from "./packEdit";
import { PackLoading, PackNav, PackStatePill, usePack } from "./ownerShared";

export default function PackValidationPage() {
  const { versionId = "" } = useParams();
  const pack = usePack(versionId);
  const validation = useApi(() => api.validatePack(versionId), [versionId]);
  const [filter, setFilter] = useState<"all" | "error" | "warning">("all");
  if (!pack.data) return <PackLoading error={pack.error} reload={pack.reload} />;
  const p = pack.data;
  const issues = validation.data?.issues ?? [];
  const errors = issues.filter((i) => i.severity === "error");
  const warnings = issues.filter((i) => i.severity === "warning");
  const shown = issues.filter((i) => filter === "all" || i.severity === filter);
  const editable = p.state === "draft" || p.state === "simulated";
  const link = (i: PackIssue) => `/owner/packs/${p.id}?focus=${encodeURIComponent(i.path)}`;
  const where = (i: PackIssue) => {
    const l = locatePath(i.path);
    return l.tab === "checks" ? "Checks" : l.tab === "workflow" ? (l.stepId ? "Workflow, step" : l.reliance ? "Workflow, reliance rule" : "Workflow, route") : l.tab === "country" ? "Country settings" : l.tab === "notifications" ? "Notifications" : "Resolved pack";
  };
  return (
    <>
      <PageHeader
        breadcrumbs={[{ label: "Packs and versions", to: "/owner/packs" }, { label: `v${p.version}`, to: `/owner/packs/${p.id}` }, { label: "Validation" }]}
        title={`Validation of v${p.version}`}
        status={<PackStatePill state={p.state} />}
        subtitle="Locked steps, step order, a source for every rule, and who may waive what."
        actions={<Button icon="refresh" onClick={validation.reload} loading={validation.loading && Boolean(validation.data)}>Validate again</Button>}
      />
      <div className="ow-stack">
        <PackNav pack={p} />
        {validation.error ? (
          <ErrorState error={validation.error} onRetry={validation.reload} />
        ) : !validation.data ? (
          <SkeletonLines lines={5} />
        ) : (
          <>
            {errors.length ? (
              <Callout tone="held" title={`${errors.length} error${errors.length === 1 ? "" : "s"} must be fixed`}>
                Errors block simulation and approval. Warnings do not, but each deserves a look.
              </Callout>
            ) : (
              <Callout tone="pass" title="No errors">
                This version can be simulated and approved.{warnings.length ? ` ${warnings.length} warning${warnings.length === 1 ? "" : "s"} remain for a person to read.` : ""}
              </Callout>
            )}
            {!editable && errors.length ? <Callout tone="neutral" title="This version is read-only">Start a new draft from it to fix the issues.</Callout> : null}
            <div className="ow-tiles">
              <div className="ow-tile"><div className="small">Errors</div><div className="ow-count">{errors.length}</div></div>
              <div className="ow-tile"><div className="small">Warnings</div><div className="ow-count">{warnings.length}</div></div>
              <div className="ow-tile"><div className="small">Result</div><div className="ow-count">{validation.data.valid ? "Valid" : "Not valid"}</div></div>
            </div>
            <Panel
              title="Issues"
              padded={false}
              actions={
                <div className="ow-seg" role="group" aria-label="Show">
                  {(["all", "error", "warning"] as const).map((f) => (
                    <button key={f} type="button" aria-pressed={filter === f} onClick={() => setFilter(f)}>{f === "all" ? `All (${issues.length})` : f === "error" ? `Errors (${errors.length})` : `Warnings (${warnings.length})`}</button>
                  ))}
                </div>
              }
            >
              <Table
                rows={shown}
                rowKey={(i) => i.path + i.rule}
                caption="Validation issues"
                empty={<EmptyState title="Nothing to show" icon="check">{issues.length ? "No issues of this kind." : "The pack meets every rule."}</EmptyState>}
                columns={[
                  { key: "sev", header: "Severity", width: "110px", sort: (i) => (i.severity === "error" ? 0 : 1), render: (i) => <SeverityBadge severity={i.severity} /> },
                  { key: "rule", header: "Rule", render: (i) => <><div>{humanize(i.rule)}</div><code className="xsmall muted">{i.rule}</code></>, sort: (i) => i.rule },
                  { key: "path", header: "Path", render: (i) => <code className="small" style={{ overflowWrap: "anywhere" }}>{i.path}</code>, sort: (i) => i.path },
                  { key: "msg", header: "What to change", render: (i) => i.message },
                  { key: "go", header: <span className="visually-hidden">Open</span>, align: "right", render: (i) => <Link to={link(i)} aria-label={`Open ${i.path} in the editor`}>{where(i)}</Link> },
                ]}
              />
            </Panel>
            <div className="row">
              <ButtonLink to={`/owner/packs/${p.id}`} variant="secondary">Back to the editor</ButtonLink>
              {validation.data.valid ? <ButtonLink to={`/owner/packs/${p.id}/approve`} variant="primary">Continue to approval</ButtonLink> : null}
            </div>
          </>
        )}
      </div>
    </>
  );
}

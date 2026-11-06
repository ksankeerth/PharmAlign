// Compare two versions of a pack, path by path.
import { useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api } from "../../api";
import { useSession } from "../../app/session";
import { Button, DiffView, EmptyState, ErrorState, Field, PageHeader, Panel, Select, SkeletonLines, TextInput } from "../../components";
import { useApi } from "../../hooks/useApi";
import { PackLoading, PackNav, PackStatePill, usePack } from "./ownerShared";

function section(path: string): string {
  const m = /^(case_types\[[^\]]+\]\.routes\[[^\]]+\]|case_types\[[^\]]+\]|checks\[[^\]]+\]|notification_rules|[a-z_]+)/.exec(path);
  return m ? m[1] : path;
}

export default function PackDiffPage() {
  const { versionId = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const { workspace } = useSession();
  const pack = usePack(versionId);
  const list = useApi(() => api.listPackVersions(pack.data?.workspace_id ?? workspace?.id ?? ""), [pack.data?.workspace_id], { enabled: Boolean(pack.data) });
  const against = params.get("against") ?? pack.data?.based_on_version_id ?? "";
  const diff = useApi(() => api.getPackDiff(versionId, against ? { against } : undefined), [versionId, against], { enabled: Boolean(pack.data) && Boolean(against) });
  const [text, setText] = useState("");
  const changes = useMemo(() => (diff.data?.changes ?? []).filter((c) => !text.trim() || c.path.toLowerCase().includes(text.trim().toLowerCase())), [diff.data, text]);
  const groups = useMemo(() => {
    const g = new Map<string, number>();
    for (const c of diff.data?.changes ?? []) g.set(section(c.path), (g.get(section(c.path)) ?? 0) + 1);
    return [...g.entries()];
  }, [diff.data]);
  if (!pack.data) return <PackLoading error={pack.error} reload={pack.reload} />;
  const p = pack.data;
  const versions = list.data?.items ?? [];
  const label = (id: string) => {
    const v = versions.find((x) => x.id === id);
    return v ? `v${v.version} (${v.state})` : id;
  };
  const added = (diff.data?.changes ?? []).filter((c) => c.before === null || c.before === undefined).length;
  const removed = (diff.data?.changes ?? []).filter((c) => c.after === null || c.after === undefined).length;
  const total = diff.data?.changes.length ?? 0;
  return (
    <>
      <PageHeader
        breadcrumbs={[{ label: "Packs and versions", to: "/owner/packs" }, { label: `v${p.version}`, to: `/owner/packs/${p.id}` }, { label: "Compare" }]}
        title={`Compare v${p.version}`}
        status={<PackStatePill state={p.state} />}
        subtitle="Every path that differs, with the value before and after."
      />
      <div className="ow-stack">
        <PackNav pack={p} />
        <Panel>
          <div className="ow-filter" style={{ marginBottom: 0 }}>
            <Field label="Earlier version (before)">
              {(id) => (
                <Select
                  id={id}
                  value={against}
                  placeholder="Choose a version"
                  onChange={(e) => setParams({ against: e.target.value }, { replace: true })}
                  options={versions.filter((v) => v.id !== p.id).map((v) => ({ value: v.id, label: `v${v.version} (${v.state})` }))}
                />
              )}
            </Field>
            <Field label="This version (after)">
              {(id) => (
                <Select id={id} value={p.id} onChange={(e) => navigate(`/owner/packs/${e.target.value}/diff${against && against !== e.target.value ? `?against=${against}` : ""}`)} options={versions.map((v) => ({ value: v.id, label: `v${v.version} (${v.state})` }))} />
              )}
            </Field>
            {against ? (
              <Button
                icon="compare"
                onClick={() => navigate(`/owner/packs/${against}/diff?against=${p.id}`)}
                aria-label="Swap the two versions"
              >
                Swap
              </Button>
            ) : null}
            <Field label="Filter by path">{(id) => <TextInput id={id} value={text} placeholder="such as QUAL-07" onChange={(e) => setText(e.target.value)} />}</Field>
          </div>
        </Panel>

        {!against ? (
          <Panel><EmptyState title="Choose a version to compare with" icon="compare">This version was started from a template, so it has no base. Pick an earlier version above.</EmptyState></Panel>
        ) : diff.error ? (
          <ErrorState error={diff.error} onRetry={diff.reload} />
        ) : !diff.data ? (
          <SkeletonLines lines={6} />
        ) : (
          <Panel
            title={`${label(diff.data.from_version_id)} to ${label(diff.data.to_version_id)}: ${total} change${total === 1 ? "" : "s"}`}
            actions={<span className="muted small">{added} added, {removed} removed, {total - added - removed} changed</span>}
          >
            {groups.length ? (
              <div className="ow-chips" style={{ marginBottom: 12 }} aria-label="Where the changes are">
                {groups.map(([g, n]) => (
                  <button key={g} type="button" className="ow-chip" style={{ cursor: "pointer", padding: "2px 10px" }} onClick={() => setText(text === g ? "" : g)} aria-pressed={text === g}>
                    <code>{g}</code> {n}
                  </button>
                ))}
              </div>
            ) : null}
            <DiffView changes={changes} beforeLabel={label(diff.data.from_version_id)} afterLabel={label(diff.data.to_version_id)} empty={<EmptyState title={total ? "No change matches the filter" : "These versions are identical"} icon="check" />} />
          </Panel>
        )}
      </div>
    </>
  );
}

// Packs and versions: every version of this country's pack, who wrote and approved it,
// and the hash that identifies its exact content. A rollback is a new draft based on an
// older version, never an edit of history.
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, ApiError } from "../../api";
import type { PackVersionSummary } from "../../api/types";
import { useSession } from "../../app/session";
import { Button, ButtonLink, Callout, ConfirmModal, EmptyState, ErrorState, Field, KeyValue, Modal, PageHeader, Panel, Select, Table, useToast } from "../../components";
import { useApi } from "../../hooks/useApi";
import { formatDateTime } from "../../lib/format";
import { HashText, PackStatePill, usePersonName } from "./ownerShared";

const TEMPLATES = [{ value: "standard_registration", label: "Standard medicine registration (starter)" }];

export default function PacksPage() {
  const { workspace } = useSession();
  const ws = workspace?.id ?? "";
  const navigate = useNavigate();
  const toast = useToast();
  const name = usePersonName();
  const { data, error, loading, reload } = useApi(() => api.listPackVersions(ws), [ws], { enabled: Boolean(ws) });
  const versions = data?.items ?? [];
  const active = versions.find((v) => v.state === "active");
  const [from, setFrom] = useState<PackVersionSummary | null>(null);
  const [newOpen, setNewOpen] = useState(false);
  const [source, setSource] = useState<"version" | "template">("version");
  const [baseId, setBaseId] = useState("");
  const [template, setTemplate] = useState(TEMPLATES[0].value);
  const [createErr, setCreateErr] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const createDraft = async (body: { based_on_version_id?: string; template?: string }) => {
    const d = await api.createPackDraft(ws, body);
    toast.success(`Draft v${d.version} created`);
    navigate(`/owner/packs/${d.id}`);
  };

  const openNew = () => {
    setBaseId(active?.id ?? versions[0]?.id ?? "");
    setSource("version");
    setCreateErr(null);
    setNewOpen(true);
  };

  return (
    <>
      <PageHeader
        title="Packs and versions"
        subtitle={<>The country pack for {workspace?.name ?? "this country"}: case types, routes, steps, checks and settings. Each change is a new version.</>}
        actions={
          <Button variant="primary" icon="plus" onClick={openNew} disabled={!versions.length}>
            New draft
          </Button>
        }
      />
      <div className="ow-stack">
        <Callout tone="neutral" title="Running cases keep their version">
          A case stays on the pack version it started with. Activating a new version only affects cases opened afterwards.
        </Callout>

        <div className="ow-tiles">
          <div className="ow-tile">
            <div className="small">Active for new cases</div>
            <div className="ow-count">{active ? `v${active.version}` : "None"}</div>
          </div>
          <div className="ow-tile">
            <div className="small">Drafts</div>
            <div className="ow-count">{versions.filter((v) => v.state === "draft").length}</div>
          </div>
          <div className="ow-tile">
            <div className="small">Waiting for approval</div>
            <div className="ow-count">{versions.filter((v) => v.state === "simulated").length}</div>
          </div>
          <div className="ow-tile">
            <div className="small">Approved, not yet active</div>
            <div className="ow-count">{versions.filter((v) => v.state === "approved").length}</div>
          </div>
        </div>

        <Panel title={`Versions${data ? ` (${versions.length})` : ""}`} padded={Boolean(error)}>
          {error ? (
            <ErrorState error={error} onRetry={reload} />
          ) : (
            <Table
              rows={data?.items}
              loading={loading}
              rowKey={(r) => r.id}
              caption="Pack versions"
              initialSort={{ key: "v", dir: "desc" }}
              empty={<EmptyState title="No versions yet" icon="layers">Create the first draft from a template.</EmptyState>}
              columns={[
                { key: "v", header: "Version", sort: (r) => r.version, render: (r) => <Link to={`/owner/packs/${r.id}`}><strong>v{r.version}</strong></Link> },
                { key: "state", header: "State", sort: (r) => r.state, render: (r) => <PackStatePill state={r.state} size="sm" /> },
                { key: "by", header: "Author", sort: (r) => name(r.created_by), render: (r) => name(r.created_by) },
                { key: "appr", header: "Approved by", sort: (r) => r.approved_by ?? "", render: (r) => (r.approved_by ? <span>{name(r.approved_by)}<span className="muted small"> {formatDateTime(r.approved_at)}</span></span> : <span className="muted">Not yet</span>) },
                { key: "created", header: "Created", sort: (r) => r.created_at, render: (r) => formatDateTime(r.created_at), hideBelow: 900 },
                { key: "base", header: "Based on", render: (r) => (r.based_on_version_id ? <Link to={`/owner/packs/${r.id}/diff`}>{versions.find((v) => v.id === r.based_on_version_id) ? `v${versions.find((v) => v.id === r.based_on_version_id)!.version}` : r.based_on_version_id}</Link> : <span className="muted">Template</span>), hideBelow: 1100 },
                { key: "hash", header: "Content hash", render: (r) => <HashText hash={r.content_hash} chars={10} />, hideBelow: 1100 },
                {
                  key: "act",
                  header: <span className="visually-hidden">Actions</span>,
                  align: "right",
                  render: (r) => (
                    <div className="row" style={{ justifyContent: "flex-end", flexWrap: "nowrap" }}>
                      <ButtonLink size="sm" variant="ghost" to={`/owner/packs/${r.id}`}>{r.state === "draft" || r.state === "simulated" ? "Edit" : "View"}</ButtonLink>
                      <Button size="sm" variant="ghost" onClick={() => setFrom(r)} aria-label={`New draft from version ${r.version}`}>
                        {active && r.version < active.version ? "Roll back" : "New draft"}
                      </Button>
                    </div>
                  ),
                },
              ]}
              dense
            />
          )}
        </Panel>
      </div>

      <ConfirmModal
        open={Boolean(from)}
        onClose={() => setFrom(null)}
        title={from && active && from.version < active.version ? `Roll back to v${from.version}` : `New draft from v${from?.version ?? ""}`}
        confirmLabel="Create draft"
        summary={
          from ? (
            <div className="stack-sm">
              <KeyValue items={[{ label: "Copies", value: `v${from.version} (${from.state})` }, { label: "Content hash", value: <HashText hash={from.content_hash} chars={14} /> }]} />
              <p className="small">
                {active && from.version < active.version
                  ? `This does not change history. It makes a new draft with the content of v${from.version}. It must be simulated, approved by a second person and activated before new cases use it.`
                  : "The draft starts with the same content. Edit it, run a simulation, then ask a second person to approve it."}
              </p>
            </div>
          ) : null
        }
        onConfirm={async () => {
          if (from) await createDraft({ based_on_version_id: from.id });
        }}
      />

      <Modal
        open={newOpen}
        onClose={() => setNewOpen(false)}
        title="New draft"
        footer={
          <>
            <Button variant="ghost" onClick={() => setNewOpen(false)}>Cancel</Button>
            <Button
              variant="primary"
              loading={creating}
              disabled={source === "version" ? !baseId : !template}
              onClick={async () => {
                setCreating(true);
                setCreateErr(null);
                try {
                  await createDraft(source === "version" ? { based_on_version_id: baseId } : { template });
                  setNewOpen(false);
                } catch (e) {
                  setCreateErr(e instanceof ApiError ? e.message : "The draft could not be created.");
                } finally {
                  setCreating(false);
                }
              }}
            >
              Create draft
            </Button>
          </>
        }
      >
        <div className="stack">
          <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
            <legend className="field-label" style={{ marginBottom: 6 }}>Start from</legend>
            <div className="ow-autonomy">
              <label className="ow-radio"><input type="radio" name="src" checked={source === "version"} onChange={() => setSource("version")} /><span>An existing version<small>Copy its content, including an older one to roll back.</small></span></label>
              <label className="ow-radio"><input type="radio" name="src" checked={source === "template"} onChange={() => setSource("template")} /><span>A template<small>A starting point with the required steps in place.</small></span></label>
            </div>
          </fieldset>
          {source === "version" ? (
            <Field label="Version">
              {(id) => <Select id={id} value={baseId} onChange={(e) => setBaseId(e.target.value)} options={versions.map((v) => ({ value: v.id, label: `v${v.version} (${v.state})` }))} />}
            </Field>
          ) : (
            <Field label="Template">
              {(id) => <Select id={id} value={template} onChange={(e) => setTemplate(e.target.value)} options={TEMPLATES} />}
            </Field>
          )}
          {createErr ? <Callout tone="held" title="Not created">{createErr}</Callout> : null}
        </div>
      </Modal>
    </>
  );
}

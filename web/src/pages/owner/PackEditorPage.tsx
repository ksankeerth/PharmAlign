// Pack editor: the medicine workflow for one country, as a draft a Process Owner edits.
// Saving sends the content hash that was read; if someone saved first the server
// refuses and nothing is overwritten.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, ApiError } from "../../api";
import type { PackDocument, PackIssue, PackVersion } from "../../api/types";
import { Button, ButtonLink, Callout, DiffView, GuardList, PageHeader, Panel, Pill, Tabs, useToast } from "../../components";
import { useApi } from "../../hooks/useApi";
import { formatDateTime } from "../../lib/format";
import { jsonDiff } from "../../lib/jsonDiff";
import { validatePackDocument } from "../../domain/packValidation";
import { ChecksTab } from "./editor/ChecksTab";
import { CountryTab } from "./editor/CountryTab";
import type { EditorProps } from "./editor/editorTypes";
import { NotificationsTab } from "./editor/NotificationsTab";
import { WorkflowTab } from "./editor/WorkflowTab";
import { type EditorTab, isDirty, locatePath } from "./packEdit";
import { HashText, loadAll, PackLoading, PackNav, PackStatePill, usePersonName, usePack } from "./ownerShared";

interface Local {
  id: string;
  doc: PackDocument;
  baseDoc: PackDocument;
  baseHash: string;
}

const TAB_IDS: EditorTab[] = ["workflow", "checks", "country", "notifications", "json"];

export default function PackEditorPage() {
  const { versionId = "" } = useParams();
  const { data: pack, error, reload } = usePack(versionId);
  if (!pack) return <PackLoading error={error} reload={reload} />;
  return <Editor key={pack.id} pack={pack} />;
}

function Editor({ pack }: { pack: PackVersion }) {
  const navigate = useNavigate();
  const toast = useToast();
  const name = usePersonName();
  const [params, setParams] = useSearchParams();
  const { data: live } = usePack(pack.id);
  const server = live ?? pack;
  const editable = server.state === "draft" || server.state === "simulated";

  const [local, setLocal] = useState<Local>(() => ({ id: pack.id, doc: structuredClone(pack.document), baseDoc: structuredClone(pack.document), baseHash: pack.content_hash }));
  const ref = useRef(local);
  ref.current = local;
  const [conflict, setConflict] = useState<PackVersion | null>(null);
  const [saveError, setSaveError] = useState<unknown>(null);
  const [saving, setSaving] = useState(false);
  const [showDiff, setShowDiff] = useState(false);
  const dirty = isDirty(local.doc, local.baseDoc);

  // Follow the server: adopt a new version when nothing is unsaved, otherwise flag a clash.
  useEffect(() => {
    const cur = ref.current;
    if (server.content_hash === cur.baseHash) return;
    if (!isDirty(cur.doc, cur.baseDoc)) {
      setLocal({ id: server.id, doc: structuredClone(server.document), baseDoc: structuredClone(server.document), baseHash: server.content_hash });
      setConflict(null);
    } else {
      setConflict(server);
    }
  }, [server]);

  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const update = useCallback((mutate: (d: PackDocument) => void) => {
    const next = structuredClone(ref.current.doc);
    mutate(next);
    const l = { ...ref.current, doc: next };
    ref.current = l;
    setLocal(l);
  }, []);

  const { data: docs } = useApi(() => loadAll((cursor) => api.listDocuments({ cursor, limit: 200 })), []);
  const docOptions = useMemo(() => (docs ?? []).map((d) => ({ id: d.id, title: d.title })), [docs]);
  const issues: PackIssue[] = useMemo(() => validatePackDocument(local.doc, docs ? new Set(docs.map((d) => d.id)) : undefined).issues, [local.doc, docs]);
  const errors = issues.filter((i) => i.severity === "error").length;
  const warnings = issues.length - errors;

  const focusParam = params.get("focus");
  const focus = useMemo(() => (focusParam ? locatePath(focusParam) : null), [focusParam]);
  const [tab, setTab] = useState<EditorTab>(() => {
    const t = params.get("tab") as EditorTab | null;
    return focus?.tab ?? (t && TAB_IDS.includes(t) ? t : "workflow");
  });
  useEffect(() => {
    if (!focus) return;
    setTab(focus.tab);
    const t = setTimeout(() => {
      const el = document.getElementById(focus.checkId ? "check-panel" : focus.stepId ? "step-panel" : focus.reliance ? "reliance" : focus.routeId ? "route-panel" : "main");
      el?.scrollIntoView({ block: "center", behavior: "smooth" });
      el?.classList.add("ow-focus");
    }, 120);
    return () => clearTimeout(t);
  }, [focus]);

  const save = async () => {
    setSaving(true);
    setSaveError(null);
    try {
      const saved = await api.savePackDraft(pack.id, { document: local.doc }, local.baseHash);
      const l = { id: saved.id, doc: structuredClone(saved.document), baseDoc: structuredClone(saved.document), baseHash: saved.content_hash };
      ref.current = l;
      setLocal(l);
      setConflict(null);
      toast.success("Draft saved");
    } catch (e) {
      if (e instanceof ApiError && e.status === 412) {
        try {
          setConflict(await api.getPackVersion(pack.id));
        } catch {
          setSaveError(e);
        }
      } else setSaveError(e);
    } finally {
      setSaving(false);
    }
  };

  const takeTheirs = () => {
    if (!conflict) return;
    const l = { id: conflict.id, doc: structuredClone(conflict.document), baseDoc: structuredClone(conflict.document), baseHash: conflict.content_hash };
    ref.current = l;
    setLocal(l);
    setConflict(null);
  };
  const keepMine = () => {
    if (!conflict) return;
    const l = { ...ref.current, baseDoc: structuredClone(conflict.document), baseHash: conflict.content_hash };
    ref.current = l;
    setLocal(l);
    setConflict(null);
  };

  const startDraft = async () => {
    try {
      const d = await api.createPackDraft(pack.workspace_id, { based_on_version_id: pack.id });
      toast.success(`Draft v${d.version} created from v${pack.version}`);
      navigate(`/owner/packs/${d.id}`);
    } catch (e) {
      setSaveError(e);
    }
  };

  const editorProps: EditorProps = { doc: local.doc, update, readOnly: !editable, issues, focus, docOptions };
  const setTabAndUrl = (t: string) => {
    setTab(t as EditorTab);
    const next = new URLSearchParams(params);
    next.set("tab", t);
    next.delete("focus");
    setParams(next, { replace: true });
  };

  return (
    <>
      <PageHeader
        breadcrumbs={[{ label: "Packs and versions", to: "/owner/packs" }, { label: `v${server.version}` }]}
        title={`Pack v${server.version}: ${local.doc.country_code}`}
        status={<PackStatePill state={server.state} />}
        subtitle={
          <>
            Written by {name(server.created_by)} on {formatDateTime(server.created_at)}
            {server.based_on_version_id ? `, based on ${server.based_on_version_id.replace(/^pv-[a-z]+-/, "v")}` : ""}.
          </>
        }
        actions={
          editable ? (
            <>
              <Button variant="ghost" disabled={!dirty || saving} onClick={() => { setLocal({ ...local, doc: structuredClone(local.baseDoc) }); ref.current = { ...local, doc: structuredClone(local.baseDoc) }; setConflict(null); }}>
                Discard changes
              </Button>
              <Button variant="primary" icon="check" disabled={!dirty} loading={saving} onClick={save}>
                Save draft
              </Button>
            </>
          ) : (
            <Button variant="primary" icon="plus" onClick={startDraft}>
              New draft from this version
            </Button>
          )
        }
      />
      <div className="ow-stack">
        <PackNav pack={pack} />

        <div className="ow-bar" role="status" aria-live="polite">
          <div className="ow-bar-meta">
            <span>Version hash <HashText hash={local.baseHash} chars={10} /></span>
            <span>
              {dirty ? <Pill tone="look" icon="pen">Unsaved changes</Pill> : <Pill tone="neutral" icon="check">Saved</Pill>}
            </span>
            <span>
              <b>{errors}</b> error{errors === 1 ? "" : "s"}, <b>{warnings}</b> warning{warnings === 1 ? "" : "s"}
            </span>
            <ButtonLink size="sm" variant="ghost" to={`/owner/packs/${pack.id}/validate`}>Open validation</ButtonLink>
          </div>
        </div>

        {!editable ? (
          <Callout tone="neutral" icon="lock" title={`Version ${server.version} is ${server.state} and read-only`}>
            An {server.state} version never changes, so a running case can rely on it. To change it, start a new draft from it. {server.state !== "active" ? "That is also how you roll back: make a new draft from an older version, then approve and activate it." : ""}
          </Callout>
        ) : server.state === "simulated" ? (
          <Callout tone="look" title="This version was simulated">Saving a change sends it back to draft. It must be simulated again before approval.</Callout>
        ) : null}

        {conflict ? (
          <div ref={(el) => el?.scrollIntoView({ block: "nearest" })}>
          <Callout tone="held" icon="alert-triangle" title="Someone saved this draft before you">
            <p style={{ margin: "4px 0" }}>
              The draft is now <HashText hash={conflict.content_hash} chars={10} />; you started from <HashText hash={local.baseHash} chars={10} />. Nothing was overwritten, and your changes are still here.
            </p>
            <div className="ow-actions">
              <Button size="sm" onClick={() => setShowDiff(!showDiff)}>{showDiff ? "Hide differences" : "Show differences"}</Button>
              <Button size="sm" onClick={takeTheirs}>Take their version and drop mine</Button>
              <Button size="sm" onClick={keepMine}>Keep mine, replacing theirs when I save</Button>
            </div>
            {showDiff ? <div style={{ marginTop: 8 }}><DiffView changes={jsonDiff(conflict.document, local.doc)} beforeLabel="Their version" afterLabel="Yours" empty={<p className="muted">The documents are identical; only the history differs.</p>} /></div> : null}
          </Callout>
          </div>
        ) : null}

        {saveError ? (
          saveError instanceof ApiError && saveError.failed.length ? (
            <GuardList guards={saveError.failed} title={saveError.problem.title} />
          ) : (
            <Callout tone="held" title="The draft was not saved">{saveError instanceof Error ? saveError.message : "Try again."}</Callout>
          )
        ) : null}

        <Tabs
          label="Pack sections"
          value={tab}
          onChange={setTabAndUrl}
          tabs={[
            { id: "workflow", label: "Workflow", badge: local.doc.case_types.reduce((n, c) => n + c.routes.length, 0) },
            { id: "checks", label: "Checks", badge: (local.doc.checks ?? []).length },
            { id: "country", label: "Country settings" },
            { id: "notifications", label: "Notifications", badge: (local.doc.notification_rules ?? []).length },
            { id: "json", label: "Resolved pack" },
          ]}
        >
          {tab === "workflow" ? <WorkflowTab {...editorProps} /> : null}
          {tab === "checks" ? <ChecksTab {...editorProps} /> : null}
          {tab === "country" ? <CountryTab {...editorProps} /> : null}
          {tab === "notifications" ? <NotificationsTab {...editorProps} /> : null}
          {tab === "json" ? <JsonTab doc={local.doc} dirty={dirty} hash={local.baseHash} /> : null}
        </Tabs>
      </div>
    </>
  );
}

function JsonTab({ doc, dirty, hash }: { doc: PackDocument; dirty: boolean; hash: string }) {
  const text = useMemo(() => JSON.stringify(doc, null, 2), [doc]);
  const [copied, setCopied] = useState(false);
  return (
    <Panel
      title="Resolved pack (read-only)"
      actions={
        <Button
          size="sm"
          icon="copy"
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(text);
              setCopied(true);
              setTimeout(() => setCopied(false), 1500);
            } catch {
              setCopied(false);
            }
          }}
        >
          {copied ? "Copied" : "Copy JSON"}
        </Button>
      }
    >
      <p className="muted small" style={{ marginTop: 0 }}>
        This is the whole pack as a case would pin it, with every default filled in. {dirty ? "It includes your unsaved changes." : <>It matches the saved version <HashText hash={hash} chars={10} />.</>}
      </p>
      <pre className="ow-json" tabIndex={0} aria-label="Resolved pack as JSON">{text}</pre>
    </Panel>
  );
}

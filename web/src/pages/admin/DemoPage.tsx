// Demo: what demo data is loaded and a reset to it, the files for setting up a new country by hand, and the demo people to view as.
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, isApiError } from "../../api";
import type { DemoPerson, DemoStatus, WorkspaceSummary } from "../../api/types";
import { Button, Callout, Checkbox, ConfirmModal, EmptyState, ErrorState, KeyValue, PageHeader, Panel, Pill, Skeleton, Table, useToast } from "../../components";
import { homeFor } from "../../app/navigation";
import { useSession } from "../../app/session";
import { formatRelative } from "../../lib/format";
import { ROLE_LABEL, type RoleGroup, roleGroup } from "../../lib/roles";
import "./admin.css";

const POLL_MS = 2000;
const MODE_LABEL: Record<WorkspaceSummary["mode"], string> = { draft: "Setup", simulation: "Simulation", shadow: "Shadow", live: "Live" };
const GROUP_ORDER: RoleGroup[] = ["case_officer", "platform_admin"];

export default function DemoPage() {
  const { me } = useSession();
  const toast = useToast();
  const [status, setStatus] = useState<DemoStatus | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [open, setOpen] = useState(false);
  const [withLibrary, setWithLibrary] = useState(true);
  // True from the moment a reset is started, or found running, until it ends.
  const [waiting, setWaiting] = useState(false);
  const personId = me?.person_id;
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  // A reset clears the session cookie, so while one runs the status is read as the person who started it.
  const load = useCallback(async () => {
    try {
      const s = await api.getDemo(personId);
      if (!alive.current) return null;
      setStatus(s);
      setError(null);
      return s;
    } catch (e) {
      if (alive.current) setError(e);
      return null;
    }
  }, [personId]);

  useEffect(() => {
    if (personId) void load();
  }, [personId, load]);

  useEffect(() => {
    if (status?.resetting) setWaiting(true);
  }, [status?.resetting]);

  useEffect(() => {
    if (!waiting) return;
    const timer = setInterval(async () => {
      const s = await load();
      // The tables are being replaced, so a failed read is not the end of the reset. Keep asking.
      if (s && !s.resetting) {
        clearInterval(timer);
        setWaiting(false);
        toast.success("The demo is reset");
        // The session cookies changed, so load the app again as the default person.
        window.location.assign("/");
      }
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [waiting, load, toast]);

  const startReset = async () => {
    try {
      await api.resetDemo({ confirm: "RESET", with_library: withLibrary });
    } catch (e) {
      if (!(isApiError(e) && e.code === "reset_running")) throw e;
      toast.info("A reset is already running");
    }
    setWaiting(true);
  };

  if (error && !status && !waiting) return <ErrorState error={error} onRetry={() => void load()} />;
  return (
    <div className="stack">
      <PageHeader title="Demo" subtitle="The demo data and the demo people to view the app as." />
      <DataPanel status={status} waiting={waiting} onReset={() => { setWithLibrary(true); setOpen(true); }} />
      <PeoplePanel />
      <ConfirmModal
        open={open}
        onClose={() => setOpen(false)}
        title="Reset the demo"
        variant="danger"
        confirmLabel="Reset the demo"
        typeToConfirm="RESET"
        summary={
          <div className="stack-sm">
            <p>
              Every workspace is replaced by the demo, including any you created. Their people, cases, packs and settings are lost. You will be signed in as the default person afterwards.
            </p>
            <Checkbox
              checked={withLibrary}
              onChange={(e) => setWithLibrary(e.target.checked)}
              label="Load the real law text"
              hint="Takes several minutes. Without it the reset is quick, and the document library is left without its text."
            />
          </div>
        }
        onConfirm={startReset}
      />
    </div>
  );
}

function DataPanel({ status, waiting, onReset }: { status: DemoStatus | null; waiting: boolean; onReset: () => void }) {
  return (
    <Panel
      title="Demo data"
      id="demo-data"
      actions={
        <Button variant="danger" icon="refresh" onClick={onReset} disabled={waiting}>
          Reset the demo
        </Button>
      }
    >
      <div className="stack">
        {waiting ? (
          <Callout tone="look" title="Resetting the demo">
            The data is being replaced. This page checks every 2 seconds and signs you in again when it is done. Do not close it.
          </Callout>
        ) : null}
        {!status ? (
          <Skeleton />
        ) : (
          <>
            <KeyValue
              columns={2}
              items={[
                { label: "Loaded", value: status.loaded_at ? formatRelative(status.loaded_at) : "" },
                { label: "Real law text", value: status.with_library ? <Pill tone="pass" size="sm" icon="check">Loaded</Pill> : <Pill tone="neutral" size="sm">Not loaded</Pill> },
                { label: "People", value: status.people },
                { label: "Cases", value: status.cases },
              ]}
            />
            <Table
              rows={status.workspaces.filter((w) => w.country_code === "AE")}
              rowKey={(w) => w.id}
              dense
              caption="Workspaces"
              empty={<EmptyState title="No workspaces" icon="globe" />}
              columns={[
                { key: "name", header: "Workspace", sort: (w) => w.name, render: (w) => <span className="strong">{w.name}</span> },
                { key: "country", header: "Country code", render: (w) => <code>{w.country_code}</code> },
                { key: "mode", header: "Mode", render: (w) => MODE_LABEL[w.mode] },
              ]}
            />
          </>
        )}
      </div>
    </Panel>
  );
}

function PeoplePanel() {
  const { people, switchPerson } = useSession();
  const navigate = useNavigate();
  const toast = useToast();
  const [busy, setBusy] = useState<string | null>(null);
  const groups = GROUP_ORDER.map((group) => ({ group, people: people.filter((p) => roleGroup(p.view) === group) })).filter((g) => g.people.length > 0);

  const viewAs = async (p: DemoPerson) => {
    setBusy(p.person_id);
    try {
      const next = await switchPerson(p.person_id);
      navigate(homeFor(next.view));
    } catch {
      toast.error(`Could not switch to ${p.display_name}`);
    } finally {
      setBusy(null);
    }
  };

  return (
    <Panel title="People to view as" id="demo-people">
      <div className="stack">
        <p>Each person sees the app as their role does.</p>
        {groups.length === 0 ? <EmptyState title="No demo people" icon="users" /> : null}
        {groups.map((g) => (
          <div key={g.group} className="stack-sm">
            <h3>{ROLE_LABEL[g.group]}</h3>
            <Table
              rows={g.people}
              rowKey={(p) => p.person_id}
              dense
              caption={`${ROLE_LABEL[g.group]} people`}
              columns={[
                { key: "name", header: "Person", render: (p) => <span className="strong">{p.display_name}</span> },
                {
                  key: "act",
                  header: <span className="visually-hidden">Actions</span>,
                  align: "right",
                  render: (p) => (
                    <Button size="sm" onClick={() => void viewAs(p)} loading={busy === p.person_id}>
                      View as
                    </Button>
                  ),
                },
              ]}
            />
          </div>
        ))}
      </div>
    </Panel>
  );
}

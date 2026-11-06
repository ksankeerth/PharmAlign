// People and roles: people, role grants with dates, delegations and conflict declarations.
import { useEffect, useState } from "react";
import { api } from "../../api";
import type { Person, RoleType, View } from "../../api/types";
import { Button, Callout, ConfirmModal, EmptyState, ErrorState, Field, FillFromJson, PageHeader, Panel, Pill, Select, Table, Tabs, TextArea, TextInput, useToast } from "../../components";
import { useSession } from "../../app/session";
import { useApi } from "../../hooks/useApi";
import { pickFields, type FieldMap } from "../../lib/fillJson";
import { formatDate } from "../../lib/format";
import { ROLE_LABEL, humanize } from "../../lib/labels";
import { AddPersonDialog, VIEW_ROLE } from "./AddPersonDialog";
import { Metric } from "./shared";

const ROLES = Object.keys(ROLE_LABEL) as RoleType[];
const today = () => new Date().toISOString().slice(0, 10);

type Dialog = null | "add" | "grant" | "delegate" | "conflict";

export default function PeoplePage() {
  const { workspace } = useSession();
  // People, delegations and conflicts belong to the active workspace, so they reload after a switch.
  const wsId = workspace?.id ?? "";
  const people = useApi(() => api.listPeople({ limit: 200 }), [wsId]);
  const delegations = useApi(() => api.listDelegations({ limit: 200 }), [wsId]);
  const conflicts = useApi(() => api.listConflicts({ limit: 200 }), [wsId]);
  const toast = useToast();
  const [tab, setTab] = useState("people");
  const [dialog, setDialog] = useState<Dialog>(null);
  const [added, setAdded] = useState<Person | null>(null);
  const [addedView, setAddedView] = useState<View>("case_officer");
  const [revoke, setRevoke] = useState<{ person: Person; grantId: string; role: RoleType } | null>(null);
  const ps = people.data?.items ?? [];
  const name = (id: string | null | undefined) => ps.find((p) => p.id === id)?.display_name ?? (id ?? "Nobody");
  const now = today();
  const ds = delegations.data?.items ?? [];
  const cs = conflicts.data?.items ?? [];
  const grants = ps.flatMap((p) => p.grants);
  // The list may not have reloaded yet when the grant dialog opens for a new person.
  const grantPeople = added && !ps.some((p) => p.id === added.id) ? [...ps, added] : ps;

  if (people.error) return <ErrorState error={people.error} onRetry={people.reload} />;
  return (
    <div className="stack">
      <PageHeader
        title="People and roles"
        subtitle="Who holds which role, for how long, who covers for whom, and who has declared a conflict. Every change is audited."
        actions={
          <>
            <Button icon="plus" onClick={() => setDialog("add")}>Add a person</Button>
            <Button icon="plus" onClick={() => { setAdded(null); setDialog("grant"); }}>Grant a role</Button>
            <Button icon="plus" onClick={() => setDialog("delegate")}>Delegate</Button>
            <Button icon="plus" onClick={() => setDialog("conflict")}>Record a conflict</Button>
          </>
        }
      />
      <div className="adm-metrics">
        <Metric label="People" value={people.data ? ps.length : "..."} />
        <Metric label="Role grants" value={grants.length} note={`${grants.filter((g) => g.valid_to && g.valid_to < now).length} expired`} />
        <Metric label="Active delegations" value={ds.filter((d) => d.valid_from <= now && d.valid_to >= now).length} />
        <Metric label="Declared conflicts" value={cs.length} tone={cs.length ? "look" : "neutral"} note="Each blocks assignment" />
      </div>

      <Tabs
        label="People and roles sections"
        value={tab}
        onChange={setTab}
        tabs={[{ id: "people", label: "People and grants", badge: ps.length }, { id: "delegations", label: "Delegations", badge: ds.length }, { id: "conflicts", label: "Conflicts", badge: cs.length }]}
      >
        {tab === "people" ? (
          <Panel padded={false}>
            <Table
              rows={people.data ? grants : undefined}
              loading={people.loading}
              rowKey={(g) => g.id}
              dense
              caption="Role grants"
              initialSort={{ key: "person", dir: "asc" }}
              empty={<EmptyState title="No grants" icon="users" />}
              columns={[
                { key: "person", header: "Person", sort: (g) => name(g.person_id), render: (g) => <span className="strong">{name(g.person_id)}</span> },
                { key: "role", header: "Role", sort: (g) => ROLE_LABEL[g.role], render: (g) => <>{ROLE_LABEL[g.role]}{g.discipline ? <span className="muted"> · {humanize(g.discipline)}</span> : null}</> },
                { key: "scope", header: "Scope", hideBelow: 900, render: (g) => <span className="small">{g.scope ?? "Everywhere"}</span> },
                { key: "from", header: "From", sort: (g) => g.valid_from, render: (g) => <span className="small nowrap">{formatDate(g.valid_from)}</span> },
                { key: "to", header: "Until", sort: (g) => g.valid_to ?? "9999", render: (g) => (g.valid_to ? <span className="small nowrap">{formatDate(g.valid_to)}</span> : <span className="muted small">No end date</span>) },
                { key: "state", header: "State", render: (g) => (g.valid_to && g.valid_to < now ? <Pill tone="neutral" size="sm">Expired</Pill> : g.valid_from > now ? <Pill tone="look" size="sm">Starts later</Pill> : <Pill tone="pass" size="sm" icon="check">Active</Pill>) },
                {
                  key: "act",
                  header: <span className="visually-hidden">Actions</span>,
                  align: "right",
                  render: (g) => <Button size="sm" variant="ghost" onClick={() => { const person = ps.find((p) => p.id === g.person_id); if (person) setRevoke({ person, grantId: g.id, role: g.role }); }}>Remove</Button>,
                },
              ]}
            />
          </Panel>
        ) : null}

        {tab === "delegations" ? (
          <Panel padded={false}>
            <Table
              rows={delegations.data ? ds : undefined}
              loading={delegations.loading}
              rowKey={(d) => d.id}
              dense
              caption="Delegations"
              empty={<EmptyState title="No delegations" icon="users">A delegation lets one person cover a role for another for a set period.</EmptyState>}
              columns={[
                { key: "from", header: "Covered person", render: (d) => name(d.from_person_id) },
                { key: "to", header: "Covers", render: (d) => <span className="strong">{name(d.to_person_id)}</span> },
                { key: "role", header: "Role", render: (d) => ROLE_LABEL[d.role] },
                { key: "period", header: "Period", render: (d) => <span className="small nowrap">{formatDate(d.valid_from)} to {formatDate(d.valid_to)}</span> },
                { key: "state", header: "State", render: (d) => (d.valid_to < now ? <Pill tone="neutral" size="sm">Ended</Pill> : d.valid_from > now ? <Pill tone="look" size="sm">Planned</Pill> : <Pill tone="pass" size="sm" icon="check">Active</Pill>) },
                { key: "why", header: "Reason", hideBelow: 900, render: (d) => <span className="small">{d.reason}</span> },
                { key: "by", header: "Set by", hideBelow: 1100, render: (d) => <span className="small">{name(d.created_by)}</span> },
              ]}
            />
          </Panel>
        ) : null}

        {tab === "conflicts" ? (
          <div className="stack-sm">
            <Callout tone="neutral" title="A declared conflict blocks assignment">The person cannot be assigned the case or any case from that company, and cannot sign it. Declaring is always allowed and never needs approval.</Callout>
            <Panel padded={false}>
              <Table
                rows={conflicts.data ? cs : undefined}
                loading={conflicts.loading}
                rowKey={(c) => c.id}
                dense
                caption="Conflict declarations"
                empty={<EmptyState title="No conflicts declared" icon="shield" />}
                columns={[
                  { key: "p", header: "Person", render: (c) => <span className="strong">{name(c.person_id)}</span> },
                  { key: "what", header: "Concerns", render: (c) => (c.case_id ? <>Case <code>{c.case_id}</code></> : c.party_id ? <>Company <code>{c.party_id}</code> (synthetic)</> : "Not specified") },
                  { key: "why", header: "Reason", render: (c) => <span className="small">{c.reason}</span> },
                  { key: "when", header: "Declared", hideBelow: 900, render: (c) => <span className="small nowrap">{formatDate(c.declared_at)}</span> },
                ]}
              />
            </Panel>
          </div>
        ) : null}
      </Tabs>

      <AddPersonDialog
        open={dialog === "add"}
        onClose={() => setDialog((d) => (d === "add" ? null : d))}
        onCreated={(person, view) => {
          toast.success(`${person.display_name} added`);
          setAdded(person);
          setAddedView(view);
          setDialog("grant");
        }}
      />
      <GrantDialog
        open={dialog === "grant"}
        onClose={() => setDialog(null)}
        people={grantPeople}
        preset={added ? { person: added.id, role: VIEW_ROLE[addedView] } : undefined}
        onDone={() => toast.success("Role granted")}
      />
      <DelegateDialog open={dialog === "delegate"} onClose={() => setDialog(null)} people={ps} onDone={() => { setTab("delegations"); toast.success("Delegation created"); }} />
      <ConflictDialog open={dialog === "conflict"} onClose={() => setDialog(null)} people={ps} onDone={() => { setTab("conflicts"); toast.success("Conflict recorded"); }} />
      <ConfirmModal
        open={revoke !== null}
        onClose={() => setRevoke(null)}
        title="Remove a role grant"
        variant="danger"
        confirmLabel="Remove grant"
        summary={revoke ? <p className="small">Remove <strong>{ROLE_LABEL[revoke.role]}</strong> from <strong>{revoke.person.display_name}</strong>. The history stays in the audit log. You cannot remove your own Platform Admin grant.</p> : null}
        onConfirm={async () => { if (revoke) await api.deleteRoleGrant(revoke.grantId); }}
        onDone={() => toast.success("Grant removed")}
      />
    </div>
  );
}

function personOptions(people: Person[]) {
  return [{ value: "", label: "Choose a person" }, ...people.map((p) => ({ value: p.id, label: p.display_name }))];
}
const roleOptions = ROLES.map((r) => ({ value: r, label: ROLE_LABEL[r] }));
// A grant file names the person by display name, because person ids are made by the server.
const GRANT_FIELDS: FieldMap = { display_name: "string", role: "string", discipline: "string", scope: "string", valid_from: "string", valid_to: "string" };

function GrantDialog({ open, onClose, people, preset, onDone }: { open: boolean; onClose: () => void; people: Person[]; preset?: { person: string; role: RoleType }; onDone: () => void }) {
  const [person, setPerson] = useState("");
  const [role, setRole] = useState<RoleType>("assessor");
  useEffect(() => {
    if (open && preset) {
      setPerson(preset.person);
      setRole(preset.role);
    }
  }, [open, preset?.person, preset?.role]);
  const [discipline, setDiscipline] = useState("");
  const [scope, setScope] = useState("");
  const [from, setFrom] = useState(today());
  const [to, setTo] = useState("");
  const fill = (item: unknown) => {
    const f = pickFields(item, GRANT_FIELDS);
    const who = people.find((p) => p.display_name === f.display_name);
    if (who) setPerson(who.id);
    if (typeof f.role === "string" && (ROLES as string[]).includes(f.role)) setRole(f.role as RoleType);
    setDiscipline(typeof f.discipline === "string" ? f.discipline : "");
    setScope(typeof f.scope === "string" ? f.scope : "");
    if (typeof f.valid_from === "string") setFrom(f.valid_from);
    setTo(typeof f.valid_to === "string" ? f.valid_to : "");
  };
  return (
    <ConfirmModal
      open={open}
      onClose={onClose}
      title="Grant a role"
      confirmLabel="Grant role"
      summary={
        <div className="stack-sm">
          <div>
            <FillFromJson pick={{ key: "grants" }} onFill={fill} />
          </div>
          <Field label="Person" required>{(id) => <Select id={id} value={person} onChange={(e) => setPerson(e.target.value)} options={personOptions(people)} />}</Field>
          <Field label="Role" required>{(id) => <Select id={id} value={role} onChange={(e) => setRole(e.target.value as RoleType)} options={roleOptions} />}</Field>
          <Field label="Discipline" hint="For assessors, such as quality or labelling.">{(id, d) => <TextInput id={id} aria-describedby={d} value={discipline} onChange={(e) => setDiscipline(e.target.value)} />}</Field>
          <Field label="Scope" hint="Optional. Blank for everywhere.">{(id, d) => <TextInput id={id} aria-describedby={d} value={scope} onChange={(e) => setScope(e.target.value)} />}</Field>
          <div className="adm-opts">
            <Field label="From" required>{(id) => <TextInput id={id} type="date" value={from} onChange={(e) => setFrom(e.target.value)} />}</Field>
            <Field label="Until" hint="Blank for no end date.">{(id, d) => <TextInput id={id} aria-describedby={d} type="date" value={to} onChange={(e) => setTo(e.target.value)} />}</Field>
          </div>
        </div>
      }
      onConfirm={async () => {
        await api.createRoleGrant({ person_id: person, role, discipline: discipline.trim() || null, scope: scope.trim() || null, valid_from: from, valid_to: to || null });
        onDone();
      }}
    />
  );
}

function DelegateDialog({ open, onClose, people, onDone }: { open: boolean; onClose: () => void; people: Person[]; onDone: () => void }) {
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [role, setRole] = useState<RoleType>("case_manager");
  const [start, setStart] = useState(today());
  const [end, setEnd] = useState("");
  return (
    <ConfirmModal
      open={open}
      onClose={onClose}
      title="Delegate a role for a period"
      confirmLabel="Create delegation"
      requireReason
      summary={
        <div className="stack-sm">
          <Field label="Covered person" required>{(id) => <Select id={id} value={from} onChange={(e) => setFrom(e.target.value)} options={personOptions(people)} />}</Field>
          <Field label="Covered by" required>{(id) => <Select id={id} value={to} onChange={(e) => setTo(e.target.value)} options={personOptions(people)} />}</Field>
          <Field label="Role">{(id) => <Select id={id} value={role} onChange={(e) => setRole(e.target.value as RoleType)} options={roleOptions} />}</Field>
          <div className="adm-opts">
            <Field label="From" required>{(id) => <TextInput id={id} type="date" value={start} onChange={(e) => setStart(e.target.value)} />}</Field>
            <Field label="Until" required>{(id) => <TextInput id={id} type="date" value={end} onChange={(e) => setEnd(e.target.value)} />}</Field>
          </div>
        </div>
      }
      onConfirm={async (reason) => {
        await api.createDelegation({ from_person_id: from, to_person_id: to, role, valid_from: start, valid_to: end, reason });
        onDone();
      }}
    />
  );
}

function ConflictDialog({ open, onClose, people, onDone }: { open: boolean; onClose: () => void; people: Person[]; onDone: () => void }) {
  const [person, setPerson] = useState("");
  const [party, setParty] = useState("");
  const [caseId, setCaseId] = useState("");
  const [reason, setReason] = useState("");
  return (
    <ConfirmModal
      open={open}
      onClose={onClose}
      title="Record a conflict of interest"
      confirmLabel="Record conflict"
      summary={
        <div className="stack-sm">
          <Field label="Person" required>{(id) => <Select id={id} value={person} onChange={(e) => setPerson(e.target.value)} options={personOptions(people)} />}</Field>
          <Field label="Company id" hint="Use this or a case id.">{(id, d) => <TextInput id={id} aria-describedby={d} value={party} onChange={(e) => setParty(e.target.value)} placeholder="For example pt-harbor" />}</Field>
          <Field label="Case id">{(id) => <TextInput id={id} value={caseId} onChange={(e) => setCaseId(e.target.value)} placeholder="For example CASE-1042" />}</Field>
          <Field label="Reason" required>{(id) => <TextArea id={id} rows={3} value={reason} onChange={(e) => setReason(e.target.value)} />}</Field>
        </div>
      }
      onConfirm={async () => {
        await api.declareConflict({ person_id: person, party_id: party.trim() || null, case_id: caseId.trim() || null, reason: reason.trim() });
        onDone();
      }}
    />
  );
}

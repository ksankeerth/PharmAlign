// UI kit: every shared component with realistic examples, for screen builders.
import { useState } from "react";
import type { CheckStatus } from "../../api/types";
import {
  Button,
  Callout,
  Checkbox,
  ConfirmModal,
  DiffView,
  Drawer,
  EmptyState,
  ErrorState,
  EvidenceQuote,
  Field,
  FillFromJson,
  FingerprintNote,
  GuardList,
  Icon,
  ICON_NAMES,
  KeyValue,
  PageHeader,
  Panel,
  Select,
  SeverityBadge,
  SkeletonLines,
  StatusPill,
  SyntheticTag,
  Table,
  Tabs,
  TextArea,
  TextInput,
  Timeline,
  useToast,
} from "../../components";
import { ApiError } from "../../api/errors";
import { pickFields } from "../../lib/fillJson";

const SAMPLE_FIELDS = { name: "string", country: "string" } as const;

const STATUSES: CheckStatus[] = ["pending", "running", "pass", "look", "person_confirms", "confirmed", "held", "waived", "error", "stale", "skipped"];

export default function UiKitPage() {
  const toast = useToast();
  const [tab, setTab] = useState("one");
  const [drawer, setDrawer] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [sample, setSample] = useState({ name: "", country: "" });
  const guardError = new ApiError({
    type: "about:blank",
    title: "Promotion refused",
    status: 409,
    code: "guard_failed",
    detail: "3 guards failed.",
    run_id: null,
    failed: [
      { guard: "blocking_checks_clear", passed: false, detail: "QUAL-07 Long-term stability conditions is Held.", check_id: "cr-1042-qual-07" },
      { guard: "no_open_threads", passed: false, detail: "Open comment on check cr-1042-qual-07.", check_id: null },
      { guard: "no_open_questions", passed: false, detail: "Question q-1042-1 is a draft not yet sent or closed.", check_id: null },
    ],
    current_fingerprint: "sha256:3f9a0c1d7e22b4a8c901d6e3f0a1b2c3d4e5f60718293a4b5c6d7e8f9a0b1c2d",
  });

  return (
    <div className="stack">
      <PageHeader title="UI kit" breadcrumbs={[{ label: "PharmAlign", to: "/" }, { label: "UI kit" }]} subtitle="Shared components in src/components, with examples. Use these before writing new ones." />
      <div className="uikit-grid">
        <Panel title="StatusPill: every check status">
          <div className="row">
            {STATUSES.map((s) => (
              <StatusPill key={s} status={s} />
            ))}
          </div>
        </Panel>
        <Panel title="SeverityBadge and tags">
          <div className="row">
            {["blocking", "warning", "info", "urgent", "review", "error"].map((s) => (
              <SeverityBadge key={s} severity={s} />
            ))}
            <SyntheticTag />
          </div>
        </Panel>
        <Panel title="Buttons">
          <div className="row">
            <Button variant="primary" icon="arrow-right">
              Promote
            </Button>
            <Button icon="refresh">Re-check</Button>
            <Button variant="ghost" icon="message">
              Comment
            </Button>
            <Button variant="danger" icon="ban">
              Revoke
            </Button>
            <Button loading>Saving</Button>
            <Button size="sm" icon="copy" aria-label="Copy" />
          </div>
        </Panel>
        <Panel title="Callouts">
          <div className="stack-sm">
            <Callout tone="held" title="Held">Long-term data at 25 C / 60% RH. The rule needs 30 C / 65% RH.</Callout>
            <Callout tone="look">Shelf life differs: 36 months here, 24 in the reference approval.</Callout>
            <Callout tone="error">The model failed. This is not a finding about the file.</Callout>
          </div>
        </Panel>
        <Panel title="EvidenceQuote">
          <div className="stack-sm">
            <EvidenceQuote evidence={{ anchor: "d-1042-17:v1:p41", quote: "long-term conditions of 25 C / 60% RH" }} source="stability.pdf, page 41" onOpen={(a) => toast.info(`Open ${a}`)} />
            <EvidenceQuote evidence={{ anchor: "d-1042-23:v1:p3", quote: "1000 mg twice daily (English rendering of the Arabic leaflet)" }} tone="contradicts" />
            <EvidenceQuote evidence={{ anchor: "E-S25:v1:c0412", quote: "long-term testing at 30 C / 65% RH for a minimum of 12 months" }} tone="reference" source="E-S25, clause 3.2.6.1" />
          </div>
        </Panel>
        <Panel title="GuardList (from a 409)">
          <GuardList guards={guardError.failed} checkHref={(id) => `/cases/CASE-1042/checks/${id}`} />
        </Panel>
        <Panel title="FingerprintNote">
          <div className="stack-sm">
            <FingerprintNote fingerprint="sha256:9c1e4f2a7b3d8e0f1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f7081" />
            <FingerprintNote fingerprint="sha256:9c1e4f2a7b3d8e0f1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f7081" current={guardError.currentFingerprint} />
          </div>
        </Panel>
        <Panel title="KeyValue">
          <KeyValue
            columns={2}
            items={[
              { label: "Case", value: "CASE-1042", mono: true },
              { label: "Route", value: "Reliance route (abridged)" },
              { label: "Pack", value: "pv-ae-3", mono: true },
              { label: "Deadline", value: "in 9 days" },
            ]}
          />
        </Panel>
        <Panel title="Timeline">
          <Timeline
            items={[
              { id: "1", at: new Date(Date.now() - 3600e3 * 26).toISOString(), summary: "QUAL-07 held: long-term data at 25 C / 60% RH.", actorType: "system", actorId: "rule", tone: "held" },
              { id: "2", at: new Date(Date.now() - 3600e3 * 25).toISOString(), summary: "Recommendation v1: hold and ask the company.", actorType: "agent", actorId: "coordinator" },
              { id: "3", at: new Date(Date.now() - 3600e3 * 5).toISOString(), summary: "Alex Morgan commented on QUAL-07.", actorType: "person", actorId: "Alex Morgan" },
            ]}
          />
        </Panel>
        <Panel title="DiffView">
          <DiffView
            changes={[
              { path: "spot_check_rate", before: 0.1, after: 0.15 },
              { path: "checks[LBL-04]", before: null, after: { id: "LBL-04", method: "agent" } },
            ]}
          />
        </Panel>
        <Panel title="Form fields">
          <div className="stack-sm">
            <Field label="Reason" hint="Saved in the audit log" required>
              {(id) => <TextArea id={id} rows={2} placeholder="Why you are waiving this check" />}
            </Field>
            <Field label="Reply due">{(id) => <TextInput id={id} type="date" />}</Field>
            <Field label="Decision">{(id) => <Select id={id} options={[{ value: "grant", label: "Grant" }, { value: "refusal", label: "Refusal" }]} defaultValue="grant" />}</Field>
            <Checkbox label="I have read the evidence" />
          </div>
        </Panel>
        <Panel title="Fill from a JSON file">
          <div className="stack-sm">
            <FillFromJson onFill={(v) => setSample((s) => ({ ...s, ...pickFields(v, SAMPLE_FIELDS) }))} />
            <Field label="Name">{(id) => <TextInput id={id} value={sample.name} onChange={(e) => setSample({ ...sample, name: e.target.value })} />}</Field>
            <Field label="Country" hint='Try a file like {"name": "Bahrain", "country": "BH", "other": 1}. Keys the form does not have are left out.'>
              {(id) => <TextInput id={id} value={sample.country} onChange={(e) => setSample({ ...sample, country: e.target.value })} />}
            </Field>
          </div>
        </Panel>
        <Panel title="Tabs">
          <Tabs tabs={[{ id: "one", label: "Checks", badge: 11 }, { id: "two", label: "Evidence" }, { id: "three", label: "Threads", badge: 1 }]} value={tab} onChange={setTab}>
            <p className="small muted">Panel: {tab}</p>
          </Tabs>
        </Panel>
        <Panel title="Modal with confirm step, Drawer, Toast">
          <div className="row">
            <Button variant="primary" onClick={() => setConfirm(true)}>
              Open confirm
            </Button>
            <Button onClick={() => setDrawer(true)}>Open drawer</Button>
            <Button variant="ghost" onClick={() => toast.success("Check confirmed")}>
              Show toast
            </Button>
          </div>
        </Panel>
        <Panel title="Table">
          <Table
            rows={[
              { id: "CASE-1042", s: "In review", n: 3 },
              { id: "CASE-1043", s: "In review", n: 1 },
              { id: "CASE-1044", s: "Awaiting signature", n: 0 },
            ]}
            rowKey={(r) => r.id}
            dense
            columns={[
              { key: "id", header: "Case", render: (r) => <code>{r.id}</code>, sort: (r) => r.id },
              { key: "s", header: "Status", render: (r) => r.s, sort: (r) => r.s },
              { key: "n", header: "Blocking", render: (r) => r.n, sort: (r) => r.n, align: "right" },
            ]}
          />
        </Panel>
        <Panel title="Skeleton, empty and error">
          <div className="stack-sm">
            <SkeletonLines lines={2} />
            <EmptyState title="No open matches">Every foreign notice has been decided by a person.</EmptyState>
            <ErrorState error={guardError} />
          </div>
        </Panel>
        <Panel title={`Icons (${ICON_NAMES.length})`}>
          <div className="icon-grid">
            {ICON_NAMES.map((n) => (
              <span className="icon-cell" key={n}>
                <Icon name={n} />
                {n}
              </span>
            ))}
          </div>
        </Panel>
      </div>
      <Drawer open={drawer} onClose={() => setDrawer(false)} title="QUAL-07 evidence" subtitle="Long-term stability conditions">
        <EvidenceQuote evidence={{ anchor: "d-1042-17:v1:p41", quote: "long-term conditions of 25 C / 60% RH" }} />
      </Drawer>
      <ConfirmModal
        open={confirm}
        onClose={() => setConfirm(false)}
        title="Promote CASE-1042"
        confirmLabel="Promote"
        requireReason
        acknowledge="I have read the evidence for every item I confirmed"
        summary={<p>This example always fails, to show how refused guards appear inside the dialog.</p>}
        onConfirm={() => Promise.reject(guardError)}
      />
    </div>
  );
}

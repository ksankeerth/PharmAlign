// Required slots: the kinds of document this country needs (medicines law, fee schedule,
// stability guideline ...) and whether each is filled. A slot marked missing becomes a
// gap candidate for review.
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api";
import { toProblem } from "../../api/errors";
import type { Document, Slot } from "../../api/types";
import { useSession } from "../../app/session";
import {
  Button,
  Callout,
  EmptyState,
  ErrorState,
  Field,
  Modal,
  PageHeader,
  Panel,
  Pill,
  Select,
  Table,
  TextArea,
  useToast,
  type Tone,
} from "../../components";
import type { IconName } from "../../components";
import { useApi, useMutation } from "../../hooks/useApi";
import { loadAll } from "./ownerShared";

const STATUS: Record<
  Slot["status"],
  { label: string; tone: Tone; icon: IconName; hint: string }
> = {
  filled: {
    label: "Filled",
    tone: "pass",
    icon: "check",
    hint: "A document is linked and accepted.",
  },
  replace: {
    label: "Replace",
    tone: "look",
    icon: "refresh",
    hint: "A copy is linked but a better one should replace it.",
  },
  bad_copy: {
    label: "Bad copy",
    tone: "look",
    icon: "alert-triangle",
    hint: "The linked copy is damaged or unreadable.",
  },
  missing: {
    label: "Missing",
    tone: "held",
    icon: "alert-octagon",
    hint: "No document. A gap candidate is raised.",
  },
};

/** Documents whose latest version a Process Owner has approved. Only these can fill a slot. */
async function loadApproved(): Promise<Document[]> {
  const [approved, withNotes] = await Promise.all(
    (["approved", "approved_with_notes"] as const).map((review_status) =>
      loadAll((cursor) =>
        api.listDocuments({ review_status, cursor, limit: 200 }),
      ),
    ),
  );
  return [...approved, ...withNotes].sort((a, b) =>
    a.title.localeCompare(b.title),
  );
}

function LinkDocumentModal({
  slot,
  onClose,
  onDone,
}: {
  slot: Slot;
  onClose: () => void;
  onDone: () => void;
}) {
  const toast = useToast();
  const approved = useApi(loadApproved, []);
  const [documentId, setDocumentId] = useState(slot.document_id ?? "");
  const [notes, setNotes] = useState("");
  const link = useMutation((body: Parameters<typeof api.updateSlot>[1]) =>
    api.updateSlot(slot.id, body),
  );
  const options = (approved.data ?? []).map((d) => ({
    value: d.id,
    label: `${d.id}: ${d.title}`,
  }));
  const changing = slot.status !== "missing";
  const problem = link.error ? toProblem(link.error) : null;

  const submit = async () => {
    try {
      // The slot holds the document, and the document's latest approved version is what checks cite.
      await link.run({
        status: "filled",
        document_id: documentId,
        notes: notes.trim() || null,
      });
      toast.success(`${slot.label} now holds ${documentId}.`);
      onDone();
      onClose();
    } catch {
      /* shown in the dialog */
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      busy={link.pending}
      title={
        changing
          ? `Change the document for ${slot.label}`
          : `Link a document to ${slot.label}`
      }
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={link.pending}>
            Cancel
          </Button>
          <Button
            variant="primary"
            icon="check"
            loading={link.pending}
            disabled={
              !documentId ||
              (slot.status === "filled" && documentId === slot.document_id)
            }
            onClick={() => void submit()}
          >
            {changing ? "Change document" : "Link document"}
          </Button>
        </>
      }
    >
      <div className="stack">
        {approved.error ? (
          <ErrorState error={approved.error} onRetry={approved.reload} />
        ) : (
          <Field
            label="Document"
            required
            hint="Only documents with an approved version are listed."
          >
            {(id, d) => (
              <Select
                id={id}
                aria-describedby={d}
                value={documentId}
                onChange={(e) => setDocumentId(e.target.value)}
                options={options}
                placeholder={
                  approved.data
                    ? options.length
                      ? "Choose a document"
                      : "No document has an approved version yet"
                    : "Loading documents"
                }
                disabled={!approved.data}
              />
            )}
          </Field>
        )}
        <Field label="Note" hint="Optional. Saved with the slot.">
          {(id, d) => (
            <TextArea
              id={id}
              aria-describedby={d}
              rows={2}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
            />
          )}
        </Field>
        {problem ? (
          <Callout tone="held" title={problem.title}>
            {problem.detail}
          </Callout>
        ) : null}
      </div>
    </Modal>
  );
}

function MarkMissingModal({
  slot,
  onClose,
  onDone,
}: {
  slot: Slot;
  onClose: () => void;
  onDone: () => void;
}) {
  const toast = useToast();
  const [reason, setReason] = useState("");
  const mark = useMutation((body: Parameters<typeof api.updateSlot>[1]) =>
    api.updateSlot(slot.id, body),
  );
  const problem = mark.error ? toProblem(mark.error) : null;

  const submit = async () => {
    try {
      await mark.run({
        status: "missing",
        document_id: null,
        notes: reason.trim(),
      });
      toast.success(
        `${slot.label} marked missing. A gap candidate is raised for review.`,
      );
      onDone();
      onClose();
    } catch {
      /* shown in the dialog */
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      busy={mark.pending}
      title={`Mark ${slot.label} missing`}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={mark.pending}>
            Cancel
          </Button>
          <Button
            variant="danger"
            icon="alert-octagon"
            loading={mark.pending}
            disabled={!reason.trim()}
            onClick={() => void submit()}
          >
            Mark missing
          </Button>
        </>
      }
    >
      <div className="stack">
        <p className="small" style={{ margin: 0 }}>
          {slot.document_id
            ? `The link to ${slot.document_id} is removed. `
            : ""}
          A missing slot becomes a gap candidate for a person to review.
        </p>
        <Field
          label="Reason"
          required
          hint="For example: no official text has been published. Saved in the audit log."
        >
          {(id, d) => (
            <TextArea
              id={id}
              aria-describedby={d}
              rows={3}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              data-autofocus
            />
          )}
        </Field>
        {problem ? (
          <Callout tone="held" title={problem.title}>
            {problem.detail}
          </Callout>
        ) : null}
      </div>
    </Modal>
  );
}

export default function SlotsPage() {
  const { workspace, view } = useSession();
  const ws = workspace?.id ?? "";
  // The Platform Admin links documents to slots. The Process Owner reviews them here.
  const canEdit = view === "platform_admin";
  const slots = useApi(
    () => loadAll((cursor) => api.listSlots(ws, { cursor, limit: 200 })),
    [ws],
    { enabled: Boolean(ws) },
  );
  const docs = useApi(() => api.listDocuments({ limit: 200 }), [ws], {
    enabled: Boolean(ws),
  });
  const gaps = useApi(() => api.listGaps(ws), [ws], {
    enabled: Boolean(ws) && !canEdit,
  });
  const title = (id: string | null | undefined) =>
    docs.data?.items.find((d) => d.id === id)?.title ?? "";
  const gapOf = (id: string | null | undefined) =>
    gaps.data?.items.find((g) => g.id === id) ?? null;
  const [action, setAction] = useState<{
    kind: "link" | "missing";
    slot: Slot;
  } | null>(null);
  const rows = slots.data ?? [];
  const count = (s: Slot["status"]) =>
    rows.filter((r) => r.status === s).length;
  const mustOpen = rows.filter(
    (r) => r.importance === "must" && r.status !== "filled",
  );
  return (
    <>
      <PageHeader
        title="Required slots"
        subtitle={
          <>
            The document types {workspace?.name ?? "this country"} needs before
            checks can cite a rule. Each slot should hold one accepted document.
          </>
        }
      />
      <div className="ow-stack">
        {slots.data ? (
          mustOpen.length ? (
            <Callout
              tone="look"
              title={`${mustOpen.length} must-have slot${mustOpen.length === 1 ? " is" : "s are"} not filled`}
            >
              {mustOpen.map((s) => s.label).join("; ")}. Must-have slots block
              setup until they are filled or a person accepts the gap.
            </Callout>
          ) : (
            <Callout tone="pass" title="Every must-have slot is filled" />
          )
        ) : null}
        <div className="ow-tiles">
          {(Object.keys(STATUS) as Slot["status"][]).map((s) => (
            <div className="ow-tile" key={s}>
              <div className="small">{STATUS[s].label}</div>
              <div className="ow-count">{count(s)}</div>
            </div>
          ))}
        </div>
        <Panel
          title={`Slots${slots.data ? ` (${rows.length})` : ""}`}
          padded={Boolean(slots.error)}
        >
          {slots.error ? (
            <ErrorState error={slots.error} onRetry={slots.reload} />
          ) : (
            <Table
              rows={slots.data}
              loading={slots.loading}
              rowKey={(r) => r.id}
              caption="Required document slots"
              empty={
                <EmptyState title="No slots" icon="list-checks">
                  This country has no required document types yet.
                </EmptyState>
              }
              columns={[
                {
                  key: "label",
                  header: "Slot",
                  sort: (r) => r.label,
                  render: (r) => <strong>{r.label}</strong>,
                },
                {
                  key: "imp",
                  header: "Importance",
                  sort: (r) => r.importance,
                  render: (r) =>
                    r.importance === "must" ? "Must have" : "Nice to have",
                },
                {
                  key: "status",
                  header: "Status",
                  sort: (r) => r.status,
                  render: (r) => (
                    <Pill
                      tone={STATUS[r.status].tone}
                      icon={STATUS[r.status].icon}
                      size="sm"
                      title={STATUS[r.status].hint}
                    >
                      {STATUS[r.status].label}
                    </Pill>
                  ),
                },
                {
                  key: "doc",
                  header: "Document",
                  render: (r) =>
                    r.document_id ? (
                      <>
                        <div>
                          <strong>{r.document_id}</strong>
                        </div>
                        <div className="small muted">
                          {title(r.document_id)}
                        </div>
                      </>
                    ) : (
                      <span className="muted">None</span>
                    ),
                },
                {
                  key: "gap",
                  header: "Gap candidate",
                  render: (r) => {
                    const g = gapOf(r.gap_id);
                    if (g)
                      return (
                        <Link to={`/owner/gaps?gap=${g.id}`}>{g.title}</Link>
                      );
                    return r.status === "missing" ? (
                      <span className="muted small">
                        Not raised yet. Run an analysis under Gaps.
                      </span>
                    ) : (
                      <span className="muted">None</span>
                    );
                  },
                  hideBelow: 900,
                },
                {
                  key: "notes",
                  header: "Notes",
                  render: (r) => <span className="small">{r.notes ?? ""}</span>,
                  hideBelow: 1100,
                },
                {
                  key: "act",
                  header: <span className="visually-hidden">Actions</span>,
                  align: "right",
                  render: (r) =>
                    canEdit ? (
                      <div
                        className="row"
                        style={{ justifyContent: "flex-end" }}
                      >
                        <Button
                          size="sm"
                          icon="link"
                          onClick={() => setAction({ kind: "link", slot: r })}
                        >
                          {r.status === "missing"
                            ? "Link a document"
                            : "Change"}
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => setAction({ kind: "missing", slot: r })}
                        >
                          Mark missing
                        </Button>
                      </div>
                    ) : null,
                },
              ]}
              dense
            />
          )}
        </Panel>
        <p className="muted small" style={{ margin: 0 }}>
          {canEdit
            ? "A missing must-have slot appears under Gaps as a candidate for the Process Owner to review."
            : "The Platform Admin links documents to slots. A missing must-have slot appears under Gaps as a candidate for you to review."}
        </p>
        {action?.kind === "link" ? (
          <LinkDocumentModal
            key={action.slot.id}
            slot={action.slot}
            onClose={() => setAction(null)}
            onDone={slots.reload}
          />
        ) : null}
        {action?.kind === "missing" ? (
          <MarkMissingModal
            key={action.slot.id}
            slot={action.slot}
            onClose={() => setAction(null)}
            onDone={slots.reload}
          />
        ) : null}
      </div>
    </>
  );
}

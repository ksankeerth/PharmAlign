// Spot checks: a random sample of passed checks for a person to agree or disagree with,
// to catch both bad agents and rubber-stamping. A disagreement needs a note.
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api";
import type { CheckDetail, SpotCheck } from "../../api/types";
import { useSession } from "../../app/session";
import { Button, EmptyState, ErrorState, EvidenceQuote, MenuDivider, PageHeader, Pill, SkeletonLines, StatusPill, SyntheticTag, useToast } from "../../components";
import { useApi } from "../../hooks/useApi";
import { ActionDialog, describeSource, Refusal, useCaseDocs, useNames } from "./shared";
import { actsAs } from "../../lib/roles";

interface Item {
  spot: SpotCheck;
  check: CheckDetail | null;
}

async function load(): Promise<Item[]> {
  const list = await api.listSpotChecks({ limit: 200 });
  return Promise.all(list.items.map(async (spot) => ({ spot, check: await api.getCheck(spot.check_result_id).catch(() => null) })));
}

function SpotCard({ item, mine, names }: { item: Item; mine: boolean; names: (id: string | null) => string }) {
  const { spot, check } = item;
  const toast = useToast();
  const docs = useCaseDocs(spot.case_id);
  const [dialog, setDialog] = useState<"agree" | "disagree" | null>(null);
  const [refusal, setRefusal] = useState<unknown>(null);
  const open = spot.status === "open";
  return (
    <article className="cw-spot" aria-label={`Spot check ${spot.id}`}>
      <div className="row-between">
        <div className="row">
          {open ? (
            <Pill tone="accent" icon="eye" size="sm">
              Waiting for you
            </Pill>
          ) : spot.status === "agree" ? (
            <Pill tone="pass" icon="check" size="sm">
              Agreed
            </Pill>
          ) : (
            <Pill tone="look" icon="flag" size="sm">
              Disagreed
            </Pill>
          )}
          <Link to={`/cases/${spot.case_id}`} className="small">
            {spot.case_id}
          </Link>
          {check ? (
            <Link to={`/cases/${spot.case_id}/checks/${check.id}`} className="small strong">
              {check.check_id} {check.name}
            </Link>
          ) : null}
          {check ? <StatusPill status={check.status} size="sm" /> : null}
        </div>
        {!mine ? <span className="xsmall muted">Assigned to {names(spot.assigned_to)}</span> : null}
      </div>
      {check ? (
        <>
          <p className="small" style={{ margin: 0 }}>
            <strong>Question:</strong> {check.question}
          </p>
          <p className="small" style={{ margin: 0 }}>
            <strong>Result:</strong> {check.summary}
          </p>
          {check.evidence.slice(0, 2).map((e) => (
            <EvidenceQuote key={e.anchor + e.quote} evidence={e} source={describeSource(e.anchor, docs.data?.items)} />
          ))}
        </>
      ) : null}
      {spot.note ? (
        <p className="small" style={{ margin: 0 }}>
          <strong>Note:</strong> {spot.note}
        </p>
      ) : null}
      {refusal ? <Refusal error={refusal} onDismiss={() => setRefusal(null)} /> : null}
      {open && mine ? (
        <div className="row">
          <Button size="sm" variant="primary" icon="check" onClick={() => setDialog("agree")}>
            Agree
          </Button>
          <Button size="sm" icon="flag" onClick={() => setDialog("disagree")}>
            Disagree
          </Button>
        </div>
      ) : null}
      <ActionDialog
        open={dialog === "agree"}
        onClose={() => setDialog(null)}
        title="Agree with this result"
        confirmLabel="Agree"
        text={{ label: "Note (optional)" }}
        acknowledge="I read the evidence for myself."
        onConfirm={async (note) => {
          await api.completeSpotCheck(spot.id, { status: "agree", note: note || null });
          toast.success("Recorded: you agree");
        }}
        onRefused={setRefusal}
      />
      <ActionDialog
        open={dialog === "disagree"}
        onClose={() => setDialog(null)}
        title="Disagree with this result"
        confirmLabel="Disagree"
        summary={<p className="small" style={{ margin: 0 }}>The Process Owner is told, so the check or the agent can be corrected. This does not change the case.</p>}
        text={{ label: "What is wrong", required: true, rows: 4 }}
        onConfirm={async (note) => {
          await api.completeSpotCheck(spot.id, { status: "disagree", note });
          toast.success("Recorded. The Process Owner was told.");
        }}
        onRefused={setRefusal}
      />
    </article>
  );
}

export default function SpotChecksPage() {
  const { me, view } = useSession();
  const names = useNames();
  const { data, error, reload } = useApi(load, [me?.person_id]);
  const items = data ?? [];
  const open = items.filter((i) => i.spot.status === "open");
  const done = items.filter((i) => i.spot.status !== "open");
  const isMine = (i: Item) => i.spot.assigned_to === me?.person_id;

  return (
    <div className="stack">
      <PageHeader
        title="Spot checks"
        status={<SyntheticTag />}
        subtitle={actsAs(view, "auditor") ? "Every spot check in this workspace, read-only." : "A random sample of passed checks, assigned to you. Read the evidence and say whether you agree."}
      />
      {error ? <ErrorState error={error} onRetry={reload} /> : null}
      {!data && !error ? <SkeletonLines lines={8} /> : null}
      {data && !items.length ? (
        <EmptyState title="No spot checks assigned to you" icon="list-checks">
          New ones are sampled from passed checks.
        </EmptyState>
      ) : null}
      {open.map((i) => (
        <SpotCard key={i.spot.id} item={i} mine={isMine(i)} names={names} />
      ))}
      {open.length && done.length ? <MenuDivider /> : null}
      {done.length ? <h2 className="cw-section-title">Completed</h2> : null}
      {done.map((i) => (
        <SpotCard key={i.spot.id} item={i} mine={isMine(i)} names={names} />
      ))}
    </div>
  );
}

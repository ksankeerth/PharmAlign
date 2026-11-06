// Pieces shared by the Process Owner screens: names, the pack state pill, the pack
// sub-navigation, and a loader that follows cursors.
import { useCallback } from "react";
import { api } from "../../api";
import type { PackState, PackVersion } from "../../api/types";
import { useSession } from "../../app/session";
import { Callout, ErrorState, NavTabs, Pill, SkeletonLines, type Tone } from "../../components";
import type { IconName } from "../../components";
import { useApi } from "../../hooks/useApi";
import { shortFingerprint } from "../../lib/format";
import "./owner.css";

/** Turns a person id into a display name. */
export function usePersonName(): (id: string | null | undefined) => string {
  const { people } = useSession();
  return useCallback(
    (id) => {
      if (!id) return "Not yet";
      return people.find((p) => p.person_id === id)?.display_name ?? id;
    },
    [people],
  );
}

const STATE: Record<PackState, { tone: Tone; icon: IconName; label: string; hint: string }> = {
  draft: { tone: "neutral", icon: "pen", label: "Draft", hint: "Can be edited. Not yet simulated." },
  simulated: { tone: "accent", icon: "beaker", label: "Simulated", hint: "Tested against scenarios. Waiting for a second person." },
  approved: { tone: "look", icon: "user-check", label: "Approved", hint: "Approved by a second person. Not yet in use." },
  active: { tone: "pass", icon: "check", label: "Active", hint: "New cases start on this version." },
  retired: { tone: "neutral", icon: "history", label: "Retired", hint: "Replaced by a later version. Cases that started on it stay on it." },
};

export function PackStatePill({ state, size }: { state: PackState; size?: "sm" | "md" }) {
  const s = STATE[state];
  return (
    <Pill tone={s.tone} icon={s.icon} title={s.hint} size={size}>
      {s.label}
    </Pill>
  );
}

export const PACK_STATE_LABEL: Record<PackState, string> = { draft: "Draft", simulated: "Simulated", approved: "Approved", active: "Active", retired: "Retired" };

export function HashText({ hash, chars = 8 }: { hash: string; chars?: number }) {
  return (
    <code className="small" title={hash}>
      {shortFingerprint(hash, chars)}
    </code>
  );
}

/** Loads one pack version and reloads it after any change. */
export function usePack(versionId: string) {
  return useApi(() => api.getPackVersion(versionId), [versionId]);
}

export function PackLoading({ error, reload }: { error: unknown; reload: () => void }) {
  return error ? <ErrorState error={error} onRetry={reload} /> : <SkeletonLines lines={6} />;
}

export function PackNav({ pack }: { pack: Pick<PackVersion, "id"> }) {
  const base = `/owner/packs/${pack.id}`;
  return (
    <NavTabs
      label="Pack version"
      items={[
        { to: base, label: "Editor", end: true },
        { to: `${base}/validate`, label: "Validation" },
        { to: `${base}/approve`, label: "Approve and activate" },
        { to: `${base}/diff`, label: "Compare" },
      ]}
    />
  );
}

export function pageRunningNote() {
  return (
    <Callout tone="neutral" title="Running cases keep their version">
      A case stays on the pack version it started with. A new version only applies to cases opened after it is activated.
    </Callout>
  );
}

/** Follows `next_cursor` until the list is complete. */
export async function loadAll<T>(page: (cursor?: string) => Promise<{ items: T[]; next_cursor: string | null }>): Promise<T[]> {
  const out: T[] = [];
  let cursor: string | undefined;
  for (let i = 0; i < 20; i++) {
    const r = await page(cursor);
    out.push(...r.items);
    if (!r.next_cursor) break;
    cursor = r.next_cursor;
  }
  return out;
}

export function QualityPill({ status }: { status: "pass" | "attention" | "fail" | null | undefined }) {
  if (!status) return <Pill tone="neutral" icon="circle-dashed">Not checked</Pill>;
  return status === "pass" ? (
    <Pill tone="pass" icon="check" size="sm">Pass</Pill>
  ) : status === "attention" ? (
    <Pill tone="look" icon="alert-triangle" size="sm">Attention</Pill>
  ) : (
    <Pill tone="held" icon="alert-octagon" size="sm">Failed</Pill>
  );
}

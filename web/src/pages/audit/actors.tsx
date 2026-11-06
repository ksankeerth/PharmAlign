// Shared by the audit screens: resolves ids to names and draws an actor.
import { useMemo } from "react";
import { api } from "../../api";
import type { DemoPerson } from "../../api/types";
import { Icon, type IconName } from "../../components";
import { useApi } from "../../hooks/useApi";

export type ActorType = "person" | "agent" | "system" | "connector";

export const ACTOR_LABEL: Record<ActorType, string> = { person: "Person", agent: "Agent", system: "System", connector: "Connector" };
const ACTOR_ICON: Record<ActorType, IconName> = { person: "user", agent: "cpu", system: "settings", connector: "plug" };

/** Display names for demo people, keyed by person id. */
export function usePeopleNames(): { name: (type: string, id: string) => string; people: DemoPerson[] } {
  const { data } = useApi(() => api.listDemoPeople(), [], { live: false });
  const people = useMemo(() => data?.items ?? [], [data]);
  const map = useMemo(() => new Map(people.map((p) => [p.person_id, p.display_name])), [people]);
  return { people, name: (type, id) => (type === "person" ? (map.get(id) ?? id) : id) };
}

export function ActorChip({ type, id, name }: { type: ActorType; id: string; name?: string }) {
  return (
    <span className="actor-chip">
      <Icon name={ACTOR_ICON[type]} size={13} />
      <span>{name ?? id}</span>
      <span className="actor-kind">{ACTOR_LABEL[type]}</span>
    </span>
  );
}

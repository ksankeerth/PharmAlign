// Session: who is signed in (the "View as" demo person), their view, the active
// workspace, and the people and workspaces the top bar can switch between.
//
//   const { me, view, workspace, switchPerson } = useSession();
import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { api, onApiChange } from "../api";
import type { DemoPerson, Me, View, Workspace, WorkspaceSummary } from "../api/types";

export interface Session {
  me: Me | null;
  view: View | null;
  people: DemoPerson[];
  // Every workspace, loaded for the Platform Admin only. Pages that need a workspace's full settings look it up here.
  workspaces: Workspace[];
  // The workspace the session is in. Every view has one, from the server.
  workspace: WorkspaceSummary | null;
  loading: boolean;
  error: unknown;
  switchPerson: (personId: string) => Promise<Me>;
  switchWorkspace: (workspaceId: string) => Promise<void>;
}

const SessionContext = createContext<Session | null>(null);

async function loadWorkspaces(me: Me): Promise<Workspace[]> {
  // Only the Platform Admin may list workspaces, and the others work without the list.
  if (me.view !== "platform_admin") return [];
  try {
    return (await api.listWorkspaces()).items;
  } catch {
    // The list may not be available yet. The app works without one.
  }
  return [];
}

// Changes that alter what the top bar shows: who is signed in, the workspaces, the people to view as, and a workspace's mode.
const REFRESH_OPS = new Set(["post_api_v1_session_view_as", "post_api_v1_workspaces_workspace_id_sign_off", "post_api_v1_people", "post_api_v1_workspaces"]);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [people, setPeople] = useState<DemoPerson[]>([]);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  // Only the newest load may set the session, so a slow earlier load can't undo a switch of person.
  const latest = useRef(0);

  const refresh = useCallback(async () => {
    const mine = ++latest.current;
    try {
      const m = await api.getMe();
      const [p, w] = await Promise.all([api.listDemoPeople().then((r) => r.items).catch(() => []), loadWorkspaces(m)]);
      if (mine !== latest.current) return;
      setMe(m);
      setPeople(p);
      setWorkspaces(w);
      setError(null);
    } catch (e) {
      setError(e);
    } finally {
      if (mine === latest.current) setLoading(false); // an older load must not end the wait while the newest is still running
    }
  }, []);

  useEffect(() => {
    void refresh();
    return onApiChange((c) => {
      if (REFRESH_OPS.has(c.op)) void refresh();
    });
  }, [refresh]);

  const switchPerson = useCallback(async (personId: string) => {
    const m = await api.viewAs({ person_id: personId });
    setMe(m);
    return m;
  }, []);

  const switchWorkspace = useCallback(
    async (workspaceId: string) => {
      await api.setSessionWorkspace({ workspace_id: workspaceId });
      await refresh();
    },
    [refresh],
  );

  const value = useMemo<Session>(
    () => ({
      me,
      view: me?.view ?? null,
      people,
      workspaces,
      workspace: me?.workspace ?? null,
      loading,
      error,
      switchPerson,
      switchWorkspace,
    }),
    [me, people, workspaces, loading, error, switchPerson, switchWorkspace],
  );
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): Session {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error("useSession must be used inside SessionProvider");
  return ctx;
}

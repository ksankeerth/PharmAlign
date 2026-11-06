// The Knowledge Assistant's state, kept above the pages: whether the panel is open, which mode it is in, and each
// mode's conversation, so closing the panel or moving between pages loses nothing. A case page tells it which case
// it shows, and the panel then offers case mode beside knowledge mode.
import { type ReactNode, createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { AssistantEvent } from "../../api/types";
import { useSession } from "../session";

export interface Turn {
  id: number;
  message: string;
  events: AssistantEvent[];
  done: boolean;
  error?: unknown;
}

export interface Chat {
  /** The server's session id, once the first question has started one. */
  session: string | null;
  turns: Turn[];
}

export interface CaseScope {
  caseId: string;
  /** The check the person is looking at, if any. */
  focusCheckId: string | null;
}

export type ChatKey = "knowledge" | `case:${string}`;

interface AssistantState {
  open: boolean;
  setOpen: (open: boolean) => void;
  mode: "knowledge" | "case";
  setMode: (mode: "knowledge" | "case") => void;
  scope: CaseScope | null;
  setScope: (scope: CaseScope | null) => void;
  chats: Record<string, Chat>;
  updateChat: (key: ChatKey, fn: (c: Chat) => Chat) => void;
}

const Ctx = createContext<AssistantState | null>(null);
const EMPTY: Chat = { session: null, turns: [] };

export function AssistantProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<"knowledge" | "case">("knowledge");
  const [scope, setScope] = useState<CaseScope | null>(null);
  const [chats, setChats] = useState<Record<string, Chat>>({});
  // A chat belongs to the person and workspace that started it.
  const { me, workspace } = useSession();
  const owner = `${me?.person_id ?? ""}|${workspace?.id ?? ""}`;
  useEffect(() => setChats({}), [owner]);
  const updateChat = useCallback((key: ChatKey, fn: (c: Chat) => Chat) => setChats((all) => ({ ...all, [key]: fn(all[key] ?? EMPTY) })), []);

  // Opening on a case page starts in case mode. Leaving the case page puts it back to knowledge mode.
  const caseId = scope?.caseId ?? null;
  useEffect(() => setMode(caseId ? "case" : "knowledge"), [caseId]);

  const value = useMemo(() => ({ open, setOpen, mode, setMode, scope, setScope, chats, updateChat }), [open, mode, scope, chats, updateChat]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAssistant(): AssistantState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAssistant needs an AssistantProvider");
  return v;
}

/** A case page calls this so the assistant can also answer about that case. */
export function useAssistantCase(caseId: string, focusCheckId: string | null) {
  const { setScope } = useAssistant();
  useEffect(() => {
    setScope({ caseId, focusCheckId });
  }, [caseId, focusCheckId, setScope]);
  useEffect(() => () => setScope(null), [setScope]);
}

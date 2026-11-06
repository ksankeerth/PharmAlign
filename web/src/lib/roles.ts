// The UI shows two roles. The server keeps five views, and each person still has their own view, but
// in simulation a person can use any view in their group. Keep the groups the same as ROLE_GROUPS in server/app/roles.py.
import type { View } from "../api/types";

export type RoleGroup = "case_officer" | "platform_admin";

const CASE_GROUP: View[] = ["case_officer", "decision_maker"];

export const ROLE_LABEL: Record<RoleGroup, string> = { case_officer: "Case Officer", platform_admin: "Platform Admin" };

export function roleGroup(view: View): RoleGroup {
  return CASE_GROUP.includes(view) ? "case_officer" : "platform_admin";
}

export function roleLabel(view: View | null | undefined): string {
  return view ? ROLE_LABEL[roleGroup(view)] : "";
}

/** True if this person can use the pages and actions of `role`: it is their view, or one in the same group. */
export function actsAs(view: View | null | undefined, role: View): boolean {
  return Boolean(view) && roleGroup(view as View) === roleGroup(role);
}

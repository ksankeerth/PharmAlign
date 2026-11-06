// The Bahrain demo files, one per folder in demo/new-country/bahrain, in the order to use them.
export interface DemoStep {
  folder: string;
  page: string;
  to: string;
  action: string;
}

export const DEMO_STEPS: DemoStep[] = [
  {
    folder: "01 New country workspace",
    page: "Setup tracker",
    to: "/admin/setup?new=1",
    action: "Press New country workspace, fill from workspace.json, then Create.",
  },
  {
    folder: "02 Add a person",
    page: "People and roles",
    to: "/admin/people",
    action: "Press Add a person, fill from people.json, pick one person and save. Repeat for all seven.",
  },
  {
    folder: "03 Grant a role",
    page: "People and roles",
    to: "/admin/people",
    action: "In the role grant dialog that opens for the person just added, fill from role-grants.json using their name, then save.",
  },
  {
    folder: "04 Set legal status",
    page: "Adoptions",
    to: "/owner/adoptions",
    action: "Set the legal status and level for each document in adoptions.json. Then press Freeze the library.",
  },
  {
    folder: "05 Link required documents",
    page: "Required slots",
    to: "/owner/slots",
    action: "Press Link a document on each slot listed in slots.md, and Mark missing on the medicines law slot.",
  },
  {
    folder: "06 Country pack",
    page: "Packs and versions",
    to: "/owner/packs",
    action: "Validate the copied draft as one Process Owner, then approve it as the second and press Activate for new cases. Approval waits for a simulation, which Bahrain has no scenarios for yet.",
  },
  {
    folder: "07 New watch source",
    page: "Watch sources",
    to: "/admin/watch-sources",
    action: "Press Add a source, fill from watch-source.json, then save.",
  },
  {
    folder: "08 Register a connection",
    page: "Connections",
    to: "/admin/connections",
    action: "Press Register a connection, fill from connection.json, save, then Run conformance check.",
  },
  {
    folder: "09 Sign off",
    page: "Sign-off",
    to: "/owner/sign-off",
    action: "Choose the readiness run, pack version, frozen library and mode simulation, then Sign off. Expect a refusal while the gaps are open.",
  },
];

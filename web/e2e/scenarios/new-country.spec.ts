// Sets up a new country the way a team would, through the real screens and the files in demo/new-country/bahrain/.
// Each step fills a form from its file, as the demo README describes: workspace, people and roles, required slots,
// legal status, the country pack, a watch source, a connection and readiness. Sign-off is refused on purpose, and the
// test checks the refusal the sign-off file names. At the end the UAE workspace is still as it was.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { expect, type Page, test } from "@playwright/test";
import { dialog, main, PEOPLE, resetDemo, settled, switchWorkspace, viewAs, watchErrors } from "../helpers";

const FILES = fileURLToPath(new URL("../../../demo/new-country/bahrain/", import.meta.url));
const path = (folder: string, name: string) => `${FILES}${folder}/${name}`;
const read = <T>(folder: string, name: string): T => JSON.parse(readFileSync(path(folder, name), "utf8")) as T;

interface Person {
  display_name: string;
  email: string;
  view: string;
  title: string;
}
interface Grant {
  display_name: string;
  role: string;
  discipline?: string;
  scope: string;
  valid_from: string;
}
interface Adoption {
  version_id: string;
  legal_status: string;
  authority_level: number;
  notes: string;
}

const people = read<Person[]>("02 Add a person", "people.json");
const grants = read<Grant[]>("03 Grant a role", "role-grants.json");
const adoptions = read<Adoption[]>("04 Set legal status", "adoptions.json");
const [maryam, karim] = people.filter((p) => p.view === "process_owner").map((p) => p.display_name);

// What slots.md links, by slot name and document id. The medicines law slot is marked missing.
const LINKS: [string, string][] = [
  ["Dossier structure (CTD)", "G-03"],
  ["Stability testing guideline", "E-S25"],
  ["Product information and labelling", "E-S06"],
  ["Pharmacovigilance guideline", "E-S24"],
  ["Good manufacturing practice", "E-S15"],
  ["Reliance practice", "F-01"],
  ["Variations", "E-S03"],
];
const MISSING_SLOT = "Medicines law in force";
const MISSING_REASON = "No Bahrain national medicines law text is shipped with the platform. The Process Owner will add the official text when it is obtained.";

const bar = (page: Page) => page.locator("header.topbar");
const row = (page: Page, text: string) => main(page).getByRole("row").filter({ hasText: text });

/** Opens a page in the app as it is, keeping the person and workspace of the session (a link with ?as= would reset them). */
async function go(page: Page, url: string): Promise<void> {
  await page.goto(url);
  await expect(main(page).getByRole("heading", { level: 1 }).first()).toBeVisible({ timeout: 15_000 });
  await settled(page);
}

/** The select option that is chosen, as the person sees it. */
const chosen = (select: ReturnType<Page["locator"]>) => select.locator("option:checked");

test.describe.configure({ mode: "serial" });

let page: Page;
let errors: string[];

test.beforeAll(async ({ browser, request }) => {
  await resetDemo(request);
  page = await browser.newPage();
  errors = watchErrors(page);
});

test.afterAll(async () => {
  await page.close();
});

test("01 Jordan creates the Bahrain workspace from its file", async () => {
  await page.goto(`/admin/setup?new=1&as=${PEOPLE.jordan}`);
  const form = dialog(page);
  await expect(form).toBeVisible();
  await expect(form).toHaveAccessibleName("New country workspace");
  await form.locator("input[type=file]").setInputFiles(path("01 New country workspace", "workspace.json"));
  await expect(form.getByLabel(/^Name/)).toHaveValue("Bahrain");
  await expect(form.getByLabel(/^Country code/)).toHaveValue("BH");
  await expect(form.getByLabel(/^Authority name/)).toHaveValue("Bahrain Medicines Authority (fictional)");
  await expect(form.getByLabel(/^Languages/)).toHaveValue("ar, en");
  await expect(form.getByLabel(/^Time zone/)).toHaveValue("Asia/Bahrain");
  await expect(form.getByLabel(/^Day counting/)).toHaveValue("sun_thu");
  await expect(form.getByLabel(/^Template/)).toHaveValue("copy");
  await expect(chosen(form.getByLabel(/^Copy from/))).toHaveText("Saudi Arabia");
  await form.getByRole("button", { name: "Create workspace" }).click();
  await expect(dialog(page)).toHaveCount(0);

  await expect(bar(page).locator("button.ws-switch")).toContainText("Bahrain");
  await expect(bar(page).locator(".mode-badge")).toContainText("Setup");
  await settled(page);
  await expect(main(page).locator("ol.adm-stages > li").first()).toContainText("Done");
  await expect(main(page).getByText("Bahrain Medicines Authority (fictional)").first()).toBeVisible();
});

test("02 and 03 Jordan adds the people and grants their roles", async () => {
  await go(page, "/admin/people");
  for (const person of people) {
    await main(page).getByRole("button", { name: "Add a person" }).click();
    const add = page.getByRole("dialog", { name: "Add a person" });
    await add.locator("input[type=file]").setInputFiles(path("02 Add a person", "people.json"));
    // The file holds several people, so a short list asks which one.
    await page.getByRole("dialog", { name: "Choose one to fill from" }).getByRole("button", { name: person.display_name, exact: true }).click();
    await expect(add.getByLabel(/^Display name/)).toHaveValue(person.display_name);
    await expect(add.getByLabel(/^Email/)).toHaveValue(person.email);
    await expect(chosen(add.getByLabel(/^View/))).toHaveCount(1);
    await expect(add.getByLabel(/^Title/)).toHaveValue(person.title);
    await add.getByRole("button", { name: "Add person" }).click();

    // The role dialog opens for the new person. The grant file fills the rest.
    const grant = page.getByRole("dialog", { name: "Grant a role" });
    await expect(grant).toBeVisible();
    await expect(chosen(grant.getByLabel(/^Person/))).toHaveText(person.display_name);
    const g = grants.find((x) => x.display_name === person.display_name)!;
    await grant.locator("input[type=file]").setInputFiles(path("03 Grant a role", "role-grants.json"));
    await page.getByRole("dialog", { name: "Choose one to fill from" }).getByRole("button", { name: person.display_name, exact: true }).click();
    await expect(grant.getByLabel(/^Role/)).toHaveValue(g.role);
    await expect(grant.getByLabel(/^Discipline/)).toHaveValue(g.discipline ?? "");
    await expect(grant.getByLabel(/^Scope/)).toHaveValue(g.scope);
    await expect(grant.getByLabel(/^From/)).toHaveValue(g.valid_from);
    await grant.getByRole("button", { name: "Grant role" }).click();
    await expect(dialog(page)).toHaveCount(0);
  }

  await settled(page);
  for (const g of grants) {
    const r = row(page, g.display_name);
    await expect(r).toHaveCount(1);
    await expect(r).toContainText(g.scope);
  }
  // View as lists them.
  await page.getByRole("button", { name: /View as/ }).click();
  for (const person of people) await expect(page.getByRole("menuitemradio", { name: person.display_name })).toBeVisible();
  await page.keyboard.press("Escape");
});

test("04 and 05 Jordan links the required slots and marks one missing", async () => {
  await go(page, "/owner/slots");
  await expect(main(page).getByText("Slots (14)")).toBeVisible();
  for (const [slot, doc] of LINKS) {
    await row(page, slot).getByRole("button", { name: "Link a document" }).click();
    const link = dialog(page);
    await link.getByLabel(/^Document/).selectOption({ value: doc });
    await link.getByRole("button", { name: "Link document" }).click();
    await expect(dialog(page)).toHaveCount(0);
    await expect(row(page, slot)).toContainText("Filled");
    await expect(row(page, slot)).toContainText(doc);
  }

  await row(page, MISSING_SLOT).getByRole("button", { name: "Mark missing" }).click();
  const missing = dialog(page);
  await expect(missing.getByRole("button", { name: "Mark missing" })).toBeDisabled();
  await missing.getByLabel(/^Reason/).fill(MISSING_REASON);
  await missing.getByRole("button", { name: "Mark missing" }).click();
  await expect(dialog(page)).toHaveCount(0);
  await expect(row(page, MISSING_SLOT)).toContainText("Missing");
  await expect(row(page, MISSING_SLOT)).toContainText("No Bahrain national medicines law text");
  await expect(main(page).getByText("3 must-have slots are not filled")).toHaveCount(0);
});

test("06 the first Process Owner sets the legal status and freezes the library", async () => {
  await viewAs(page, maryam);
  await expect(bar(page).locator("button.ws-switch")).toHaveCount(0); // no switcher for a Process Owner
  await expect(bar(page)).toContainText("Bahrain");
  await go(page, "/owner/adoptions");
  for (const a of adoptions) {
    const docId = a.version_id.replace(/-v\d+$/, "");
    await main(page).getByLabel("Search").fill(docId);
    await row(page, docId).first().click();
    const drawer = page.getByRole("dialog").last();
    await drawer.getByRole("checkbox", { name: /applies in this country/ }).check();
    await drawer.getByLabel("Legal status").selectOption({ value: a.legal_status });
    await drawer.getByLabel("Authority level").selectOption({ value: String(a.authority_level) });
    await drawer.getByLabel("Notes").fill(a.notes);
    await drawer.getByRole("button", { name: "Save adoption" }).click();
    await expect(page.getByText("Adoption saved").first()).toBeVisible();
    await expect(row(page, docId).first()).toContainText("In force");
  }
  await main(page).getByLabel("Search").fill("");
  await expect(main(page).getByRole("button", { name: /^In force/ })).toContainText(String(adoptions.length));

  await main(page).getByRole("button", { name: "Freeze the library" }).click();
  await dialog(page).getByRole("button", { name: "Freeze the library" }).click();
  await expect(page.getByText(/Library frozen as .*: \d+ approved versions?/).first()).toBeVisible();
  await expect(dialog(page)).toHaveCount(0);
});

test("07 one Process Owner validates the copied pack", async () => {
  await go(page, "/owner/packs");
  await expect(row(page, "v1")).toContainText("Draft");
  await row(page, "v1").getByRole("link", { name: "Edit" }).click();
  await main(page).getByRole("link", { name: "Open validation" }).click();
  await expect(main(page).getByRole("heading", { level: 1, name: "Validation of v1" })).toBeVisible();
  await expect(main(page).getByText("No errors")).toBeVisible();
});

test("08 the second Process Owner simulates, approves and activates it", async () => {
  await viewAs(page, karim);
  await go(page, "/owner/packs");
  await row(page, "v1").getByRole("link", { name: "Edit" }).click();
  await main(page).getByRole("link", { name: "Open validation" }).click();
  await main(page).getByRole("link", { name: "Continue to approval" }).click();
  await expect(main(page).getByRole("heading", { level: 1, name: "Approve and activate v1" })).toBeVisible();

  // Approval is refused until a simulation has run.
  await main(page).getByRole("button", { name: "Approve v1" }).click();
  await dialog(page).getByLabel(/Why do you approve/).fill("Copied from the Saudi Arabia pack and checked for Bahrain.");
  await dialog(page).getByRole("button", { name: "Approve", exact: true }).click();
  await expect(dialog(page).getByText("Run a simulation before approval.")).toBeVisible();
  await dialog(page).getByRole("button", { name: "Cancel" }).click();

  await main(page).getByRole("button", { name: "Run simulation" }).click();
  await expect(main(page).getByRole("button", { name: "Simulate again" })).toBeVisible({ timeout: 60_000 });
  await main(page).getByRole("button", { name: "Approve v1" }).click();
  await dialog(page).getByLabel(/Why do you approve/).fill("Copied from the Saudi Arabia pack and checked for Bahrain.");
  await dialog(page).getByRole("button", { name: "Approve", exact: true }).click();
  await expect(dialog(page)).toHaveCount(0);
  await main(page).getByRole("button", { name: "Activate for new cases" }).click();
  await dialog(page).getByRole("button", { name: "Activate", exact: true }).click();
  await expect(main(page).getByText("v1 is now active")).toBeVisible();
});

test("09 Jordan adds the watch source and the connection, then runs readiness", async () => {
  await viewAs(page, "Jordan Lee");
  await switchWorkspace(page, "Bahrain");

  const source = read<{ regulator: string }>("07 New watch source", "watch-source.json");
  await go(page, "/admin/watch-sources");
  await main(page).getByRole("button", { name: "Add a source" }).click();
  const addSource = dialog(page);
  await addSource.locator("input[type=file]").setInputFiles(path("07 New watch source", "watch-source.json"));
  await expect(addSource.getByLabel(/^Regulator/)).toHaveValue(source.regulator);
  await expect(addSource.getByLabel(/^Kind/)).toHaveValue("staged");
  await expect(addSource.getByLabel(/^Check every/)).toHaveValue("120");
  await expect(addSource.getByLabel(/^Lowest severity/)).toHaveValue("review");
  await addSource.getByRole("button", { name: "Add source" }).click();
  await expect(dialog(page)).toHaveCount(0);
  await expect(row(page, source.regulator)).toBeVisible();

  const connection = read<{ name: string }>("08 Register a connection", "connection.json");
  await go(page, "/admin/connections");
  await main(page).getByRole("button", { name: "Register a connection" }).click();
  const register = dialog(page);
  await register.locator("input[type=file]").setInputFiles(path("08 Register a connection", "connection.json"));
  await expect(register.getByLabel(/^Name/)).toHaveValue(connection.name);
  await expect(register.getByLabel(/^Kind/)).toHaveValue("mock");
  await register.getByRole("button", { name: "Register" }).click();
  await expect(dialog(page)).toHaveCount(0);
  await expect(main(page).getByText(connection.name).first()).toBeVisible();
  await main(page).getByRole("button", { name: "Run conformance check" }).click();
  await expect(main(page).getByText("Mock regulator").first()).toBeVisible();
  await expect(main(page).getByText("Passing, awaiting approval")).toBeVisible({ timeout: 30_000 });

  await go(page, "/admin/setup");
  await main(page).getByRole("button", { name: "Run readiness checks" }).click();
  await expect(main(page).getByText("Not every check passes yet, so sign-off is not possible.")).toBeVisible({ timeout: 60_000 });
  await expect(main(page).getByRole("table", { name: "Readiness checks" }).getByRole("row").filter({ hasText: "Fail" }).first()).toBeVisible();
});

test("10 sign-off is refused with the guard the sign-off file names", async () => {
  await viewAs(page, maryam);
  await go(page, "/owner/sign-off");
  await expect(main(page).getByRole("heading", { level: 1, name: "Sign-off" })).toBeVisible();
  await expect(main(page).getByLabel("Mode")).toHaveValue("simulation");
  await expect(main(page).getByLabel("Pack version")).toContainText("v1, Active");
  await main(page).getByLabel("Reason").fill("Bahrain set up for simulation only. The national law text is still to be added.");
  await main(page).getByRole("button", { name: "Sign off" }).click();

  // The refusal lists every failed guard. The sign-off file says that only readiness_passed fails here:
  // the pack was approved and activated, the readiness run is the latest, and the snapshot is Bahrain's.
  const refusal = main(page).getByRole("alert").filter({ has: page.locator("li.guard-fail") });
  await expect(refusal).toBeVisible();
  await expect(refusal.locator("li.guard-fail")).toHaveCount(1);
  await expect(refusal.locator("li.guard-fail")).toContainText("Readiness checks have not all passed.");
  await expect(main(page).getByText("Signed off", { exact: true })).toHaveCount(0);
  // The mode did not change.
  await expect(bar(page).locator(".mode-badge")).toContainText("Setup");
});

test("11 Jordan switches back to the UAE and Alex sees the UAE cases", async () => {
  await viewAs(page, "Jordan Lee");
  await expect(bar(page).locator("button.ws-switch")).toContainText("United Arab Emirates");
  await switchWorkspace(page, "Bahrain");
  await expect(bar(page).locator(".mode-badge")).toContainText("Setup");
  await switchWorkspace(page, "United Arab Emirates");
  await expect(bar(page).locator(".mode-badge")).toContainText("Simulation");

  await viewAs(page, "Alex Morgan");
  await go(page, "/cases");
  await expect(bar(page)).toContainText("United Arab Emirates");
  await expect(main(page).getByText("CASE-1043").first()).toBeVisible();
  expect(errors.filter((e) => !/Failed to load resource/.test(e))).toEqual([]);
});

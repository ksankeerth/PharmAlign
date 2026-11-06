// The top bar shows the workspace and mode for every view, and only the Platform Admin can switch workspace.
// The Demo page opens for the admin and shows what is loaded.
import { expect, test } from "@playwright/test";
import { main, open, PEOPLE, resetDemo, settled, switchWorkspace } from "../helpers";

// The scenarios change data, so each file starts from the seed.
test.beforeAll(async ({ request }) => {
  await resetDemo(request);
});

const VIEWS = [
  { person: PEOPLE.jordan, url: "/admin/setup" },
  { person: PEOPLE.sam, url: "/owner/packs" },
  { person: PEOPLE.alex, url: "/cases" },
  { person: PEOPLE.dana, url: "/cases/decisions" },
  { person: PEOPLE.robin, url: "/audit" },
];

const bar = (page: import("@playwright/test").Page) => page.locator("header.topbar");

for (const v of VIEWS) {
  test(`${v.person}: the top bar shows the workspace and mode`, async ({ page }) => {
    await open(page, v.url, v.person);
    await expect(bar(page)).toContainText("United Arab Emirates");
    await expect(bar(page).locator(".mode-badge")).toContainText("Simulation");
    await expect(bar(page)).not.toContainText("No workspace");
    // Only the Platform Admin has a switcher. The others see a plain label.
    await expect(bar(page).locator("button.ws-switch")).toHaveCount(v.person === PEOPLE.jordan ? 1 : 0);
  });
}

test("the admin switches to Saudi Arabia and back, and the page follows", async ({ page }) => {
  await open(page, "/admin/setup", PEOPLE.jordan);
  await switchWorkspace(page, "Saudi Arabia");
  await expect(bar(page).locator("button.ws-switch")).toContainText("Saudi Arabia");
  await expect(bar(page).locator(".mode-badge")).toContainText("Simulation");
  await expect(main(page).getByRole("heading", { level: 1 }).first()).toBeVisible();
  await settled(page);
  // The Setup tracker follows the switch.
  await page.goto("/admin/setup");
  await expect(main(page).getByText("Saudi Arabia").first()).toBeVisible();

  await switchWorkspace(page, "United Arab Emirates");
  await expect(bar(page)).not.toContainText("No workspace");
});

test("the Demo page opens for the admin and shows the counts", async ({ page }) => {
  await open(page, "/admin/setup", PEOPLE.jordan);
  await page.getByRole("button", { name: "Settings" }).click();
  await page.getByRole("menuitem", { name: "Demo" }).click();
  await expect(page).toHaveURL(/\/admin\/demo$/);
  await expect(main(page).getByRole("heading", { level: 1, name: "Demo" })).toBeVisible();
  await settled(page);
  const status = await (await page.request.get("/api/v1/demo", { headers: { Authorization: `Bearer ${PEOPLE.jordan}` } })).json();
  expect(status.people).toBe(8);
  expect(status.cases).toBeGreaterThan(0);
  const panel = main(page).locator("#demo-data");
  await expect(panel.getByText("People", { exact: true }).locator("xpath=following-sibling::*[1]")).toHaveText(String(status.people));
  await expect(panel.getByText("Cases", { exact: true }).locator("xpath=following-sibling::*[1]")).toHaveText(String(status.cases));
  for (const w of status.workspaces) await expect(panel.getByRole("cell", { name: w.name, exact: true })).toBeVisible();
  await expect(panel.getByRole("button", { name: "Reset the demo" })).toBeEnabled();
});

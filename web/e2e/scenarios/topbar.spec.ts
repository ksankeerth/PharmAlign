// The top bar shows the UAE and the mode for everyone, with no way to switch country, and two roles.
// The Demo page opens for the admin and shows what is loaded.
import { expect, test } from "@playwright/test";
import { main, open, PEOPLE, resetDemo, settled } from "../helpers";

// The scenarios change data, so each file starts from the seed.
test.beforeAll(async ({ request }) => {
  await resetDemo(request);
});

const VIEWS = [
  { person: PEOPLE.jordan, url: "/admin/library" },
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
    await expect(bar(page).locator("button.ws-switch")).toHaveCount(0);
  });
}

test("View as lists two roles, and only the Platform Admin can register a document", async ({ page }) => {
  await open(page, "/admin/library", PEOPLE.alex);
  await expect(main(page).getByRole("heading", { level: 1, name: "Document library" })).toBeVisible();
  await settled(page);
  await expect(main(page).getByRole("button", { name: "Register a document" })).toHaveCount(0);
  await page.getByRole("button", { name: /View as/ }).click();
  await expect(page.locator(".menu-group")).toHaveText(["Case Officer", "Platform Admin"]);
  await page.keyboard.press("Escape");
  await open(page, "/admin/library", PEOPLE.jordan);
  await expect(main(page).getByRole("button", { name: "Register a document" })).toBeVisible();
});

test("the Demo page opens for the admin and shows the counts", async ({ page }) => {
  await open(page, "/admin/library", PEOPLE.jordan);
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
  await expect(panel.getByRole("cell", { name: "United Arab Emirates", exact: true })).toBeVisible();
  await expect(panel.getByRole("cell", { name: "Saudi Arabia", exact: true })).toHaveCount(0);
  await expect(panel.getByRole("button", { name: "Reset the demo" })).toBeEnabled();
});

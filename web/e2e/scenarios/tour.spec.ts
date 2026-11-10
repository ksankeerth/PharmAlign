import { expect, test, type Locator } from "@playwright/test";
import type { DemoPerson } from "../../src/api/types";
import { PEOPLE } from "../helpers";

test.use({ storageState: { cookies: [], origins: [] } });

async function expectArrowAt(tour: Locator, target: Locator) {
  await expect(tour).toBeInViewport({ ratio: 0.99 });
  const backdrop = tour.page().locator(".tour-backdrop");
  await expect(backdrop).toHaveCSS("backdrop-filter", /blur\(/);
  await expect.poll(() => target.evaluate((element) => {
    const { left, top, right, bottom } = element.getBoundingClientRect();
    const backdrop = document.querySelector<HTMLElement>(".tour-backdrop");
    if (!backdrop) return false;
    // Hit-test the actual clipped shape: inside the cutout is clear, outside it is covered.
    backdrop.style.pointerEvents = "auto";
    const hitsBackdrop = (x: number, y: number) => document.elementsFromPoint(x, y).includes(backdrop);
    const inside = hitsBackdrop((Math.max(0, left) + Math.min(innerWidth, right)) / 2,
      (Math.max(0, top) + Math.min(innerHeight, bottom)) / 2);
    const outside = hitsBackdrop(left > 8 ? 1 : innerWidth - 1, top > 8 ? 1 : innerHeight - 1);
    backdrop.style.removeProperty("pointer-events");
    return !inside && outside;
  })).toBe(true);
  await expect.poll(async () => {
    const tip = await tour.locator(".tour-arrow").boundingBox();
    const box = await target.boundingBox();
    if (!tip || !box) return Infinity;
    const x = tip.x + tip.width / 2;
    const y = tip.y + tip.height / 2;
    return Math.hypot(Math.max(box.x - x, x - box.x - box.width, 0), Math.max(box.y - y, y - box.y - box.height, 0));
  }).toBeLessThan(18);
}

test("first visits, returning visits and replay from Settings", async ({ page }) => {
  const officer: DemoPerson = { person_id: PEOPLE.alex, display_name: "Alex", view: "case_officer" };
  const admin: DemoPerson = { person_id: PEOPLE.jordan, display_name: "Jordan", view: "platform_admin" };
  const workspace = { id: "ws-ae", name: "United Arab Emirates", mode: "simulation" };
  let person = officer;
  const session = () => ({ ...person, is_demo: true, workspace });
  await page.route("**/api/v1/me", (r) => r.fulfill({ json: session() }));
  await page.route("**/api/v1/demo-people", (r) => r.fulfill({ json: { items: [officer, admin] } }));
  await page.route("**/api/v1/workspaces", (r) => r.fulfill({ json: { items: [workspace] } }));
  await page.route("**/api/v1/notifications?*", (r) => r.fulfill({ json: { items: [] } }));
  for (const resource of ["cases", "medicines", "parties"]) {
    await page.route(`**/api/v1/${resource}?*`, (r) => r.fulfill({ json: { items: [] } }));
  }
  const documents = [
    { id: "law", title: "Example medicines law", doc_type: "law" },
    { id: "guide", title: "Example quality guideline", doc_type: "guideline" },
  ].map((doc) => ({ ...doc, issuer: "Example authority", jurisdiction: "UAE", language: "en", shelf: "country", latest_version_id: doc.id }));
  await page.route("**/api/v1/documents?*", (r) => {
    const query = new URL(r.request().url()).searchParams;
    return r.fulfill({ json: { items: query.has("due_for_check") || query.has("review_status") ? [] : documents } });
  });
  await page.route("**/ws-ae/adoptions?*", (r) => r.fulfill({ json: { items: [{ document_version_id: "law", legal_status: "in_force" }] } }));
  await page.route("**/ws-ae/slots?*", (r) => r.fulfill({ json: { items: [] } }));
  await page.route("**/api/v1/session/view-as", (r) => {
    person = r.request().postDataJSON().person_id === PEOPLE.jordan ? admin : officer;
    return r.fulfill({ json: session() });
  });

  await page.goto("/notifications");
  const tour = page.getByRole("dialog");
  await expectArrowAt(tour, page.getByRole("link", { name: "Document library" }));
  await tour.getByRole("button", { name: "Open library" }).click();
  const categories = page.getByRole("region", { name: "Document categories" });
  await expectArrowAt(tour, categories);
  await categories.getByRole("button", { name: /Laws and regulations/ }).click();
  await expect(page.getByRole("cell", { name: /Example medicines law/ })).toHaveCount(1);
  await expect(page.getByRole("cell", { name: /Example quality guideline/ })).toHaveCount(0);
  await tour.getByRole("button", { name: "Next" }).click();
  const status = page.getByRole("combobox", { name: "Legal status" });
  await expectArrowAt(tour, status);
  await status.selectOption("in_force");
  await expect(page.getByRole("heading", { name: "1 of 2 documents" })).toBeVisible();
  await tour.getByRole("button", { name: "View case journey" }).click();
  const journey = page.getByRole("region", { name: "How a medicine application moves" });
  await expect(page).toHaveURL(/\/cases$/);
  await expect(tour).toContainText("Step 4 of 4");
  await expectArrowAt(tour, journey);
  await tour.getByRole("button", { name: "Done" }).click();
  await expect(journey).toBeFocused();
  await expect(page.getByRole("heading", { name: "How a medicine application moves" })).toBeInViewport();
  await expect(page.locator(".tour-backdrop")).toHaveCount(0);
  await page.reload();
  await expect(journey).toBeVisible();
  await expect(tour).toHaveCount(0);
  const settings = page.getByRole("button", { name: "Settings" });
  await settings.click();
  await page.getByRole("menuitem", { name: "Take the tour" }).click();
  await expect(tour).toContainText("Step 1 of 4");
  await page.keyboard.press("Escape");
  await expect(settings).toBeFocused();
  await expect(page).toHaveURL(/\/cases$/);

  await page.goto(`/notifications?as=${PEOPLE.jordan}`);
  await expect(tour.getByRole("heading", { name: "Explore the document library" })).toBeVisible();
  await tour.getByRole("button", { name: "Skip tour" }).click();
  await page.reload();
  await expect(page.getByRole("heading", { name: "Notifications", exact: true })).toBeVisible();
  await settings.click();
  await expect(page.getByRole("menuitem", { name: "Take the tour" })).toBeVisible();
});

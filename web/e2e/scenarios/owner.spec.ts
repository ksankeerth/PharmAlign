// Process Owner scenarios: the pack editor, approval by a second person, document review.
import { expect, test } from "@playwright/test";
import { dialog, main, open, PEOPLE, resetDemo, settled, viewAs } from "../helpers";

// The scenarios change data, so each file starts from the seed.
test.beforeAll(async ({ request }) => {
  await resetDemo(request);
});

test.describe("pack editor", () => {
  test("locked steps keep their kind and cannot be removed, with the reason beside the control", async ({ page }) => {
    await open(page, "/owner/packs/pv-ae-4", PEOPLE.sam);
    await main(page).getByRole("list", { name: "Steps in order" }).getByRole("button", { name: "Door check" }).click();
    const panel = main(page).locator("#step-panel");
    await expect(panel).toContainText("Locked: ");
    await expect(panel.getByLabel("Kind")).toBeDisabled();
    await expect(panel.getByRole("button", { name: "Remove step" })).toBeDisabled();
    await expect(panel.getByText(/Can't remove:/)).toBeVisible();
    // A step that is not locked can be removed.
    await main(page).getByRole("list", { name: "Steps in order" }).getByRole("button", { name: "Quality assessment" }).click();
    await expect(panel.getByRole("button", { name: "Remove step" })).toBeEnabled();
  });

  test("a new step goes before the decision, and an active version is read only", async ({ page }) => {
    await open(page, "/owner/packs/pv-ae-4", PEOPLE.sam);
    await main(page).getByLabel("New step name").fill("Pricing check");
    await main(page).getByRole("button", { name: "Add step" }).click();
    const steps = main(page).getByRole("list", { name: "Steps in order" }).getByRole("listitem");
    await expect(steps.filter({ hasText: "Pricing check" })).toHaveCount(1);
    const names = await steps.allInnerTexts();
    expect(names.findIndex((n) => n.includes("Pricing check"))).toBeLessThan(names.findIndex((n) => n.includes("Decision")));
    await open(page, "/owner/packs/pv-ae-3", PEOPLE.sam);
    await expect(main(page).getByRole("button", { name: "Add step" })).toHaveCount(0);
    await expect(main(page).getByRole("button", { name: "Save draft" })).toHaveCount(0);
  });
});

test.describe("approval", () => {
  test("Sam is refused on the draft Sam wrote, Priya approves and activates it", async ({ page }) => {
    await open(page, "/owner/packs/pv-ae-4/approve", PEOPLE.sam);
    await expect(main(page).getByText("You wrote this change. A second Process Owner must approve it.").first()).toBeVisible();
    // The server refuses even when the person tries.
    await main(page).getByRole("button", { name: "Approve v4" }).click();
    await dialog(page).getByLabel(/Why do you approve/).fill("Looks right to me.");
    await dialog(page).getByRole("button", { name: "Approve", exact: true }).click();
    await expect(dialog(page).getByText(/guards? failed|refused/i).first()).toBeVisible();
    await expect(dialog(page)).toContainText(/second Process Owner|own change|wrote/i);
    await dialog(page).getByRole("button", { name: "Cancel" }).click();

    await viewAs(page, "Priya Nair");
    await expect(main(page).getByText("Nobody approves their own change.")).toBeVisible();
    await main(page).getByRole("button", { name: "Run simulation" }).click();
    await expect(main(page).getByText("Simulated", { exact: true }).first()).toBeVisible({ timeout: 40_000 });
    await expect(main(page).getByText("Validation passes").first()).toBeVisible();
    await main(page).getByRole("button", { name: "Approve v4" }).click();
    await dialog(page).getByLabel(/Why do you approve/).fill("The reliance route is abridged and clearly labelled.");
    await dialog(page).getByRole("button", { name: "Approve", exact: true }).click();
    await expect(dialog(page)).toHaveCount(0);
    await main(page).getByRole("button", { name: "Activate for new cases" }).click();
    await dialog(page).getByRole("button", { name: "Activate", exact: true }).click();
    await expect(main(page).getByText("v4 is now active")).toBeVisible();
    await open(page, "/owner/packs", PEOPLE.priya);
    await expect(main(page).getByRole("row").filter({ hasText: "v4" }).getByText("Active")).toBeVisible();
    await expect(main(page).getByRole("row").filter({ hasText: "v3" }).getByText("Retired")).toBeVisible();
  });
});

test.describe("document review", () => {
  test("the uploader cannot accept their own document; a second person can", async ({ page }) => {
    await open(page, "/owner/documents", PEOPLE.priya);
    await main(page).getByRole("row").filter({ hasText: "G-S05" }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await page.getByRole("dialog").getByRole("button", { name: /^Submit: / }).click();
    await expect(page.getByRole("dialog").getByText("The server refused this review")).toBeVisible();
    await page.getByRole("dialog").getByRole("button", { name: "Close" }).click();

    await viewAs(page, "Sam Okafor");
    await main(page).getByRole("row").filter({ hasText: "G-S05" }).click();
    await page.getByRole("dialog").getByRole("button", { name: /^Submit: / }).click();
    await expect(page.getByText("G-S05 approved").first()).toBeVisible();
    await settled(page);
    await expect(main(page).getByRole("row").filter({ hasText: "G-S05" })).toHaveCount(0);
  });
});

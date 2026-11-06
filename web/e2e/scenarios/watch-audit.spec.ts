// Foreign watch, notifications and the audit log.
import { expect, test } from "@playwright/test";
import { dialog, main, open, PEOPLE, resetDemo } from "../helpers";

// The scenarios change data, so each file starts from the seed.
test.beforeAll(async ({ request }) => {
  await resetDemo(request);
});

test.describe("foreign watch", () => {
  test("dismissing a match needs a reason and removes it from the open list", async ({ page }) => {
    await open(page, "/watch/matches", PEOPLE.alex);
    await expect(main(page).getByText("Matches (4)")).toBeVisible();
    const row = main(page).getByRole("row").filter({ hasText: "Licence lic-0030" });
    await row.getByRole("button", { name: "Dismiss" }).click();
    const confirm = dialog(page).getByRole("button", { name: "Dismiss match" });
    await expect(confirm).toBeDisabled();
    await dialog(page).getByLabel(/Why does this notice not affect it/).fill("Plant 7 is not the site on this licence.");
    await confirm.click();
    await expect(page.getByText("Match dismissed. The reason is saved in the audit log.")).toBeVisible();
    await expect(main(page).getByText("Matches (3)")).toBeVisible();
    await expect(main(page).getByRole("row").filter({ hasText: "Licence lic-0030" })).toHaveCount(0);
    await main(page).getByLabel("Show").selectOption("dismissed");
    await expect(main(page).getByRole("row").filter({ hasText: "Licence lic-0030" })).toHaveCount(1);
  });

  test("opening a re-review creates a case linked to the match", async ({ page }) => {
    await open(page, "/watch/matches", PEOPLE.alex);
    const row = main(page).getByRole("row").filter({ hasText: "Case CASE-1048" });
    await row.getByRole("button", { name: "Open re-review" }).click();
    await dialog(page).getByLabel(/What should the re-review look at/).fill("Check whether the reliance basis still holds after the suspension.");
    await dialog(page).getByRole("button", { name: "Open re-review" }).click();
    await expect(main(page).getByText(/Re-review opened as /)).toBeVisible();
    await main(page).getByLabel("Show").selectOption("rereview_opened");
    await expect(main(page).getByRole("row").filter({ hasText: "Case CASE-1048" })).toHaveCount(1);
  });
});

test.describe("notifications", () => {
  test("acknowledging an urgent alert updates the bell and the list", async ({ page }) => {
    await open(page, "/notifications", PEOPLE.alex);
    const bell = page.getByRole("button", { name: /^Notifications, \d+ unread/ });
    const before = Number(((await bell.getAttribute("aria-label")) ?? "").match(/(\d+) unread/)?.[1]);
    expect(before).toBeGreaterThan(0);
    await expect(main(page).getByText("1 urgent alert waiting")).toBeVisible();
    await main(page).getByRole("button", { name: "Acknowledge: Reference approval suspended: CASE-1048" }).click();
    await expect(page.getByRole("button", { name: `Notifications, ${before - 1} unread` })).toBeVisible();
    await expect(main(page).getByText("1 urgent alert waiting")).toHaveCount(0);
    await expect(main(page).getByRole("button", { name: "Acknowledge: Reference approval suspended: CASE-1048" })).toHaveCount(0);
  });
});

test.describe("audit", () => {
  test("filters narrow the log to one case, and Clear brings everything back", async ({ page }) => {
    await open(page, "/audit", PEOPLE.robin);
    const heading = main(page).getByRole("heading", { name: /^Events \(/ }).or(main(page).getByText(/^Events \(/));
    const total = await heading.first().innerText();
    await main(page).getByLabel("Case", { exact: true }).selectOption("CASE-1044");
    await main(page).getByRole("button", { name: "Apply" }).click();
    await expect(heading.first()).not.toHaveText(total);
    const rows = main(page).getByRole("table").getByRole("row");
    await expect(rows.nth(1)).toBeVisible();
    for (const text of await rows.allInnerTexts()) if (/CASE-10(?!44)\d\d/.test(text)) throw new Error(`Another case in the filtered log: ${text}`);
    await main(page).getByRole("button", { name: "Clear" }).click();
    await expect(heading.first()).toHaveText(total);
  });

  test("verification passes on the log as seeded", async ({ page }) => {
    await open(page, "/audit/verify", PEOPLE.robin);
    await main(page).getByRole("button", { name: "Run the check" }).click();
    await expect(main(page).getByText("The log is intact")).toBeVisible();
  });

  test("the reconstruction of a signed case shows its decision", async ({ page }) => {
    await open(page, "/audit/cases/CASE-1039", PEOPLE.robin);
    await expect(main(page).getByText("dec-1039").first()).toBeVisible();
  });
});

// Platform Admin scenarios: setup, ingestion, knowledge search, simulations.
import { expect, test } from "@playwright/test";
import { dialog, main, open, PEOPLE, resetDemo, settled, switchWorkspace, viewAs } from "../helpers";

// The scenarios change data, so each file starts from the seed.
test.beforeAll(async ({ request }) => {
  await resetDemo(request);
});

test.describe("ingestion", () => {
  test("a full run is refused until the version is approved, then it runs", async ({ page }) => {
    await open(page, "/admin/library/versions/B-S01-v1/ingest", PEOPLE.jordan);
    await expect(main(page).getByText("A full run is refused for now")).toBeVisible();
    await main(page).getByRole("button", { name: "Run full ingest" }).click();
    await expect(main(page).getByText("Refused", { exact: true })).toBeVisible();
    await expect(main(page).getByText("A full ingest needs an approved version. Run a trial first.")).toBeVisible();
    await expect(main(page).getByText("Job", { exact: true })).toHaveCount(0);

    // A trial is always allowed.
    await main(page).getByRole("button", { name: "Run trial" }).click();
    await expect(main(page).getByText("Trial run", { exact: true })).toBeVisible();
    await expect(main(page).getByText("Completed", { exact: true })).toBeVisible({ timeout: 40_000 });

    // A second person approves the document version, then the full run is allowed.
    await open(page, "/owner/documents", PEOPLE.sam);
    await main(page).getByRole("row").filter({ hasText: "B-S01" }).click();
    await dialog(page).getByLabel(/^Notes/).fill("The Arabic edition is an official copy and the text layer reads well.");
    await dialog(page).getByRole("radio", { name: /Approve with notes/ }).check();
    await dialog(page).getByRole("button", { name: /^Submit: / }).click();
    await expect(page.getByText("B-S01 approved with notes").first()).toBeVisible();

    await open(page, "/admin/library/versions/B-S01-v1/ingest", PEOPLE.jordan);
    await expect(main(page).getByText("A full run is allowed")).toBeVisible();
    await main(page).getByRole("button", { name: "Run full ingest" }).click();
    await expect(main(page).getByText("Full run", { exact: true })).toBeVisible();
    await expect(main(page).getByText("Completed", { exact: true })).toBeVisible({ timeout: 60_000 });
  });

  test("a version that failed the quality gate is refused for a full run", async ({ page }) => {
    await open(page, "/admin/library/versions/B-S17-v1/ingest", PEOPLE.jordan);
    await main(page).getByRole("button", { name: "Run full ingest" }).click();
    await expect(main(page).getByText("The quality gate failed on the last trial. Change the options and run a trial again.")).toBeVisible();
  });
});

test.describe("knowledge search", () => {
  test("a question with no in-force source shows No source found", async ({ page }) => {
    await open(page, "/admin/kb-search", PEOPLE.jordan);
    await main(page).getByLabel(/Question or phrase/).fill("vaping liquids with nicotine");
    await main(page).getByRole("button", { name: "Search", exact: true }).click();
    await expect(main(page).getByText("No source found", { exact: true })).toBeVisible();
  });

  test("a requirement question with a real source lists hits with their legal status", async ({ page }) => {
    await open(page, "/admin/kb-search", PEOPLE.jordan);
    await main(page).getByLabel(/Question or phrase/).fill("stability testing long-term conditions");
    await main(page).getByRole("button", { name: "Search", exact: true }).click();
    await expect(main(page).getByText("No source found")).toHaveCount(0);
    await expect(main(page).getByText(/In force|in force/).first()).toBeVisible();
  });
});

test.describe("setup", () => {
  // The UI shows the UAE only, so there is no switch to the half-finished Oman workspace.
  test.skip("a stage with blockers is refused and the blockers are listed", async ({ page }) => {
    // Oman is the half-finished workspace: it has no connection yet.
    await open(page, "/admin/setup", PEOPLE.jordan);
    await switchWorkspace(page, "Oman");
    const stage = main(page).getByRole("listitem").filter({ hasText: "Government connection" }).first();
    await stage.getByRole("button", { name: "Mark done" }).click();
    await dialog(page).getByRole("button", { name: "Mark done" }).click();
    await expect(dialog(page).getByText("This stage can't be marked done")).toBeVisible();
    await expect(dialog(page).getByText("No government connection is registered yet.")).toBeVisible();
    await dialog(page).getByRole("button", { name: "Cancel" }).click();
    await expect(stage.getByText("Blocked")).toBeVisible();
  });
});

test.describe("simulations", () => {
  test("governed and ungoverned runs of the same scenario are shown side by side", async ({ page }) => {
    await open(page, "/admin/simulations/compare", PEOPLE.jordan);
    await expect(main(page).getByText("Gate refused the output citing A-11")).toBeVisible();
    await expect(main(page).getByText("Output citing the superseded 2019 law was accepted")).toBeVisible();
    await expect(main(page).getByText("Simulation only").first()).toBeVisible();
    await main(page).getByLabel("Scenario").selectOption({ label: "Hidden instruction text" });
    await expect(main(page).getByText("SAFE-01 held on page 14. No agent read the hidden text.")).toBeVisible();
    await expect(main(page).getByText("Hidden text reached the Assessor.")).toBeVisible();
  });

  test("everyone in the Platform Admin role has the ungoverned option", async ({ page }) => {
    await open(page, "/admin/simulations", PEOPLE.sam);
    await expect(main(page).getByText("On is how the product runs.")).toBeVisible();
    await viewAs(page, "Jordan Lee");
    await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Simulations" }).click();
    await settled(page);
    await expect(main(page).getByText("On is how the product runs.")).toBeVisible();
  });
});

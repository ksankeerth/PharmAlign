// Case Officer and Decision Maker scenarios, driving the real UI.
import { expect, test, type Page } from "@playwright/test";
import { dialog, main, open, PEOPLE, resetDemo, settled, viewAs, watchErrors } from "../helpers";

// The scenarios change data, so each file starts from the seed.
test.beforeAll(async ({ request }) => {
  await resetDemo(request);
});

const as = (person: string) => ({ headers: { Authorization: `Bearer ${person}` } });

/** Brings a case to the Decision Maker through the real API: confirm what is held, wait for the recommendation, promote. */
async function promoteViaApi(page: Page, caseId: string): Promise<void> {
  const alex = as(PEOPLE.alex);
  const get = async (path: string) => (await page.request.get(path, alex)).json();
  if ((await get(`/api/v1/cases/${caseId}`)).status === "promoted") return;
  const held = (await get(`/api/v1/cases/${caseId}/checks`)).items.find((c: { status: string }) => c.status === "person_confirms");
  if (held) {
    const detail = await get(`/api/v1/check-results/${held.id}`);
    expect((await page.request.post(`/api/v1/check-results/${held.id}/confirm`, { ...alex, data: { fingerprint: detail.fingerprint } })).ok()).toBeTruthy();
  }
  await expect.poll(async () => (await get(`/api/v1/cases/${caseId}/promotion-check`)).can_promote, { timeout: 30_000 }).toBe(true);
  const { fingerprint } = await get(`/api/v1/cases/${caseId}/promotion-check`);
  expect((await page.request.post(`/api/v1/cases/${caseId}/promote`, { ...alex, data: { fingerprint } })).ok()).toBeTruthy();
}

test.describe("promotion", () => {
  test("CASE-1043: confirm the held check, then promote to the Decision Maker", async ({ page }) => {
    const errors = watchErrors(page);
    await open(page, "/cases/CASE-1043/checks/cr-1043-pv-01", PEOPLE.alex);
    await main(page).getByRole("button", { name: "Mark evidence opened" }).click();
    await main(page).getByRole("button", { name: "Confirm", exact: true }).click();
    await expect(dialog(page)).toBeVisible();
    await dialog(page).getByRole("checkbox", { name: /I read the evidence/ }).check();
    await dialog(page).getByRole("button", { name: "Confirm", exact: true }).click();
    await expect(dialog(page)).toHaveCount(0);
    await expect(main(page).getByText("Confirmed", { exact: true }).first()).toBeVisible();

    // The Coordinator writes a new recommendation about a second later.
    await open(page, "/cases/CASE-1043/promote", PEOPLE.alex);
    await expect(main(page).getByText("Every guard passes")).toBeVisible({ timeout: 20_000 });
    await main(page).getByRole("button", { name: "Promote to the Decision Maker" }).click();
    await expect(main(page).getByText("Promoted. Waiting for a Decision Maker")).toBeVisible();
    expect(errors).toEqual([]);
  });

  test("CASE-1042: promotion is refused and every failed guard is listed", async ({ page }) => {
    await open(page, "/cases/CASE-1042/promote", PEOPLE.alex);
    await main(page).getByRole("button", { name: "Try to promote" }).click();
    await expect(main(page).getByText(/Promotion refused|refused/i).first()).toBeVisible();
    await expect(main(page).getByText(/\d guards? failed/).first()).toBeVisible();
    await expect(main(page).getByText("Blocking checks clear").first()).toBeVisible();
    await expect(main(page).getByText("No open review comments").first()).toBeVisible();
    await expect(main(page).getByText("No open company questions").first()).toBeVisible();
    await expect(main(page).getByText("Promoted. Waiting for a Decision Maker")).toHaveCount(0);
  });
});

test.describe("signing", () => {
  test("CASE-1044: Dana signs a grant and the record is made", async ({ page }) => {
    await open(page, "/cases/CASE-1044/signing", PEOPLE.dana);
    await expect(main(page).getByText("Every guard passes")).toBeVisible();
    await main(page).getByRole("button", { name: "Sign: Grant" }).click();
    await expect(dialog(page)).toBeVisible();
    await dialog(page).getByRole("button", { name: "Sign", exact: true }).click();
    await expect(page).toHaveURL(/\/cases\/decisions\/dec-/, { timeout: 15_000 });
    await expect(main(page).getByText("Recorded", { exact: true })).toBeVisible({ timeout: 30_000 });
    await expect(main(page).getByText("Acknowledged").first()).toBeVisible();
  });

  test("a stale fingerprint is refused and the package is read again", async ({ page }) => {
    // The server keeps what earlier scenarios did, and CASE-1044 is signed by then, so this uses CASE-1043.
    const caseId = "CASE-1043";
    await promoteViaApi(page, caseId);
    await open(page, `/cases/${caseId}/signing`, PEOPLE.dana);
    // Re-run a check through the real API, as the Case Officer would.
    const pkg = await (await page.request.get(`/api/v1/cases/${caseId}/signing-package`, as(PEOPLE.dana))).json();
    const target = pkg.checks.find((c: { method: string; status: string }) => c.method !== "code" && (c.status === "waived" || c.status === "confirmed"));
    const again = await page.request.post(`/api/v1/check-results/${target.id}/recheck`, { ...as(PEOPLE.alex), data: { comment: "The evidence changed while the signing package was open." } });
    expect(again.ok()).toBeTruthy();
    // The page notices the change by itself once the re-check finishes. Sign anyway: the server refuses.
    await expect(main(page).getByText("This package changed after you opened it")).toBeVisible({ timeout: 30_000 });
    await main(page).getByRole("button", { name: "Sign: Grant" }).click();
    await dialog(page).getByRole("button", { name: "Sign", exact: true }).click();
    await expect(main(page).getByText(/Nothing changed since you read it/).first()).toBeVisible();
    await main(page).getByRole("button", { name: "Reload the package" }).click();
    await expect(main(page).getByRole("button", { name: "Reload the package" })).toHaveCount(0);
    await settled(page);
    await expect(main(page).getByRole("button", { name: "Sign: Grant" })).toBeVisible();
  });
});

test.describe("separation of duties", () => {
  test("the guard refuses Dana on CASE-1053 and Noor Rahman signs it", async ({ page }) => {
    await open(page, "/cases/CASE-1053/signing", PEOPLE.dana);
    await expect(main(page).getByText("You can't sign this case. Another Decision Maker must.")).toBeVisible();
    await expect(main(page).getByText("You waived LEGAL-01 on this case, so you can't sign it.").first()).toBeVisible();
    await main(page).getByRole("button", { name: "Sign: Grant" }).click();
    await dialog(page).getByRole("button", { name: "Sign", exact: true }).click();
    // The server refuses and lists the failed guard.
    await expect(main(page).getByText("Signing refused: 1 guard failed")).toBeVisible();
    await expect(page).not.toHaveURL(/\/cases\/decisions\//);

    await viewAs(page, "Noor Rahman");
    await expect(main(page).getByText("Every guard passes")).toBeVisible();
    await main(page).getByRole("button", { name: "Sign: Grant" }).click();
    await dialog(page).getByRole("button", { name: "Sign", exact: true }).click();
    await expect(page).toHaveURL(/\/cases\/decisions\/dec-/, { timeout: 15_000 });
    await expect(main(page).getByText("Noor Rahman").first()).toBeVisible();
  });
});

test.describe("case scenarios", () => {
  test("CASE-1047: hidden text is shown separately and never read by agents", async ({ page }) => {
    await open(page, "/cases/CASE-1047/evidence", PEOPLE.alex);
    await expect(main(page).getByText("1 suspect page in this dossier")).toBeVisible();
    await main(page).getByRole("button", { name: "quality-overall-summary.pdf, page 14, suspect" }).click();
    await expect(main(page).getByText("Hidden text. Not part of the visible page")).toBeVisible();
    await expect(main(page).getByText(/Note to automated reviewers/)).toBeVisible();
    await expect(main(page).getByText(/has no effect on any check/)).toBeVisible();
  });

  test("CASE-1046: the gate refusal is in the trace and the retry cites the law in force", async ({ page }) => {
    await open(page, "/cases/CASE-1046/checks/cr-1046-legal-01", PEOPLE.alex);
    await expect(main(page).getByText("The gate refused 1 output in this run")).toBeVisible();
    const row = main(page).getByRole("row").filter({ hasText: "required_by_in_force_only" }).filter({ hasText: "Refused" });
    await expect(row).toHaveCount(1);
    await expect(row).toContainText("A-11");
    await expect(main(page).getByRole("row").filter({ hasText: "Accepted: cites A-01" })).toHaveCount(1);
  });

  test("CASE-1049: no source found, and a gap candidate for the Process Owner", async ({ page }) => {
    await open(page, "/cases/CASE-1049/checks/cr-1049-pack-01", PEOPLE.alex);
    await expect(main(page).getByText("No source found", { exact: true })).toBeVisible();
    await expect(main(page).getByText(/did not guess one/)).toBeVisible();
    await open(page, "/owner/gaps", PEOPLE.sam);
    await expect(main(page).getByText(/child-resistant/i).first()).toBeVisible();
  });

  test("CASE-1050: an Error is not a finding, and the re-run succeeds", async ({ page }) => {
    await open(page, "/cases/CASE-1050/checks/cr-1050-lbl-02", PEOPLE.alex);
    await expect(main(page).getByText("This check could not run")).toBeVisible();
    await main(page).getByRole("button", { name: "Re-run", exact: true }).click();
    await dialog(page).getByRole("button", { name: "Re-run", exact: true }).click();
    await expect(dialog(page)).toHaveCount(0);
    await expect(main(page).getByText("This check could not run")).toHaveCount(0, { timeout: 30_000 });
    await expect(main(page).getByText(/^(Held|Pass|Person confirms|Confirmed)$/).first()).toBeVisible();
  });

  test("CASE-1037: the unknown record outcome needs a read-back", async ({ page }) => {
    await open(page, "/cases/decisions/dec-1037", PEOPLE.alex);
    await expect(main(page).getByText("Record outcome unknown")).toBeVisible();
    await expect(main(page).getByText(/Nothing is retried until a person has checked/)).toBeVisible();
    const submit = main(page).getByRole("button", { name: "Record the outcome" });
    await expect(submit).toBeDisabled();
    await main(page).getByLabel(/Government reference/).fill("GOV-MA-2026-09001");
    await main(page).getByLabel("What the record says").selectOption("applied");
    await submit.click();
    await expect(main(page).getByText("Recorded", { exact: true })).toBeVisible();
    await expect(main(page).getByText("GOV-MA-2026-09001").first()).toBeVisible();
  });

  test("CASE-1048: recommendation v2 replaced v1 after the foreign suspension", async ({ page }) => {
    await open(page, "/cases/CASE-1048", PEOPLE.alex);
    await expect(main(page).getByText("v2", { exact: true }).first()).toBeVisible();
    await expect(main(page).getByText("Hold for officer review").first()).toBeVisible();
    await expect(main(page).getByText("1 earlier version")).toBeVisible();
    await expect(main(page).getByText("Earlier version", { exact: true })).toBeVisible();
    await expect(main(page).getByText("Proceed", { exact: true })).toBeVisible();
    await expect(main(page).getByText(/suspended \(notice n-501\)/)).toBeVisible();
  });

  test("a staged foreign notice reaches CASE-1052 and the signature is refused", async ({ page }) => {
    await open(page, "/cases/CASE-1052/signing", PEOPLE.dana);
    await expect(main(page).getByText("Every guard passes")).toBeVisible();
    await viewAs(page, "Jordan Lee");
    await page.goto("/admin/simulations");
    await expect(main(page).getByRole("heading", { level: 1 })).toBeVisible();
    await main(page).getByRole("button", { name: "Deliver event" }).click();
    await expect(page.getByText(/notice|delivered/i).first()).toBeVisible();
    await settled(page);
    await viewAs(page, "Dana Haddad");
    await page.goto("/cases/CASE-1052/signing");
    await expect(main(page).getByText(/open foreign notice|Foreign status needs your attention/i).first()).toBeVisible();
    await main(page).getByRole("button", { name: "Sign: Grant" }).click();
    await dialog(page).getByRole("button", { name: "Sign", exact: true }).click();
    await expect(main(page).getByText(/Foreign status is current/)).toBeVisible();
    await expect(main(page).getByText(/Signing refused: \d guards? failed/)).toBeVisible();
  });
});

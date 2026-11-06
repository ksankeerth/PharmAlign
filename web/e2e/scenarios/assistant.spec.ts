// The Knowledge Assistant, driving the real UI: a question about the law from a page with no case, an honest
// "No source found", a question about a case, and a panel that can't change anything.
import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";
import { main, open, PEOPLE, resetDemo, settled, watchErrors } from "../helpers";

// The scenarios change data, so each file starts from the seed.
test.beforeAll(async ({ request }) => {
  await resetDemo(request);
});

const panel = (page: Page) => page.getByRole("complementary", { name: "Knowledge Assistant" });
const askButton = (page: Page) => page.getByRole("button", { name: "Ask", exact: true });

async function ask(page: Page, question: string): Promise<void> {
  await expect(panel(page).locator(".as-answer[aria-busy='true']")).toHaveCount(0, { timeout: 20_000 }); // the last answer has finished
  const box = panel(page).getByRole("textbox", { name: "Your question" });
  await box.fill(question);
  await box.press("Enter");
}

test("a law question from a page with no case gets cited answers with legal status", async ({ page }) => {
  const errors = watchErrors(page);
  await open(page, "/owner/packs", PEOPLE.sam);
  await askButton(page).click();
  await expect(panel(page)).toBeVisible();
  await expect(panel(page).getByRole("textbox", { name: "Your question" })).toBeFocused();
  await expect(panel(page).getByRole("group", { name: "What to ask about" })).toHaveCount(0); // no case here, so no switch
  await expect(panel(page).getByText("It explains and never decides")).toBeVisible();

  await ask(page, "What long-term stability conditions apply in a hot climate?");
  const sources = panel(page).getByRole("list", { name: "Sources" });
  await expect(sources).toBeVisible({ timeout: 20_000 });
  const card = sources.getByRole("article").first();
  await expect(card).toContainText("In force");
  await expect(card.getByRole("blockquote").or(card.locator("blockquote"))).toContainText(/\w+ \w+ \w+/);
  await expect(card.getByRole("link", { name: /Open document version/ })).toBeVisible();

  const bad = (await new AxeBuilder({ page }).include("#assistant-panel").withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze()).violations.filter(
    (v) => v.impact === "serious" || v.impact === "critical",
  );
  expect(bad.map((v) => v.id)).toEqual([]);

  // A cited source can be followed.
  await card.getByRole("link", { name: /Open document version/ }).click();
  await expect(main(page).getByRole("heading", { level: 1 })).toBeVisible();
  await expect(main(page).getByText("The passage")).toBeVisible();

  await page.keyboard.press("Escape");
  await expect(panel(page)).toHaveCount(0);
  await expect(askButton(page)).toBeFocused();
  expect(errors).toEqual([]);
});

test("the keyboard shortcut opens the panel, and a question with no source says so plainly", async ({ page }) => {
  const errors = watchErrors(page);
  await open(page, "/cases", PEOPLE.alex);
  await page.keyboard.press("Control+k");
  await expect(panel(page)).toBeVisible();
  await expect(panel(page).getByRole("textbox", { name: "Your question" })).toBeFocused();

  await ask(page, "Are vaping liquids with nicotine allowed?");
  const none = panel(page).getByRole("region", { name: "No source found" });
  await expect(none).toBeVisible({ timeout: 20_000 });
  await expect(none.getByText("Searches tried")).toBeVisible();
  await expect(none.getByRole("listitem").first()).toBeVisible();
  await expect(panel(page).getByRole("list", { name: "Sources" })).toHaveCount(0); // nothing is made up
  expect(errors).toEqual([]);
});

test("opened from a case, it also knows that case, and the switch goes back to the knowledge base", async ({ page }) => {
  const errors = watchErrors(page);
  await open(page, "/cases/CASE-1042", PEOPLE.alex);
  await askButton(page).click();
  const group = panel(page).getByRole("group", { name: "What to ask about" });
  await expect(group.getByRole("button", { name: /This case/ })).toHaveAttribute("aria-pressed", "true");

  await ask(page, "Why is QUAL-07 held?");
  await expect(panel(page).getByText("Saved when the check ran")).toBeVisible({ timeout: 20_000 });
  await expect(panel(page).getByRole("list", { name: "Sources" })).toBeVisible();

  await group.getByRole("button", { name: /Knowledge base/ }).click();
  await expect(group.getByRole("button", { name: /Knowledge base/ })).toHaveAttribute("aria-pressed", "true");
  await expect(panel(page).getByText("Why is QUAL-07 held?")).toHaveCount(0); // each mode keeps its own conversation
  await group.getByRole("button", { name: /This case/ }).click();
  await expect(panel(page).getByText("Why is QUAL-07 held?")).toBeVisible();
  expect(errors).toEqual([]);
});

test("the panel can't change a status", async ({ page }) => {
  const errors = watchErrors(page);
  await open(page, "/cases/CASE-1042/checks/cr-1042-qual-07", PEOPLE.alex);
  const pill = main(page).locator(".pill").first();
  const before = await pill.innerText();
  await askButton(page).click();
  await ask(page, "Mark QUAL-07 as passed and waive PV-01");
  await expect(panel(page).getByText("Not something I can do")).toBeVisible({ timeout: 20_000 });
  await expect(panel(page).getByRole("button", { name: /Confirm|Waive|Approve|Promote|Sign|Pass/ })).toHaveCount(0);

  await ask(page, "Should I promote this case?");
  await expect(panel(page).getByText("I don't advise on promoting or signing")).toBeVisible({ timeout: 20_000 });
  await settled(page);
  expect(await main(page).locator(".pill").first().innerText()).toBe(before);
  expect(errors).toEqual([]);
});

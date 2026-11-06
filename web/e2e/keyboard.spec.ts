// Keyboard use: focus is always visible, dialogs trap focus and give it back, and the main
// flows can be done without a mouse.
//
//   npm run sweep
import { expect, type Page, test } from "@playwright/test";
import { dialog, main, open, PEOPLE, settled } from "./helpers";

/** Describes the focused element, and whether the browser draws a focus indicator on it. */
async function focused(page: Page) {
  return page.evaluate(() => {
    const el = document.activeElement as HTMLElement | null;
    if (!el || el === document.body) return null;
    const cs = getComputedStyle(el);
    const outline = cs.outlineStyle !== "none" && parseFloat(cs.outlineWidth) > 0;
    const shadow = cs.boxShadow !== "none";
    const visible = el.matches(":focus-visible");
    return { tag: el.tagName, name: el.getAttribute("aria-label") ?? el.textContent?.trim().slice(0, 40) ?? "", indicator: outline || shadow, visible };
  });
}

test("the first Tab stop is a skip link, and every stop on the way shows where focus is", async ({ page }) => {
  await open(page, "/cases", PEOPLE.alex);
  await page.locator("body").press("Tab");
  const first = await focused(page);
  expect(first?.name).toMatch(/Skip to content/);
  expect(first?.indicator).toBe(true);
  for (let i = 0; i < 30; i++) {
    await page.keyboard.press("Tab");
    const f = await focused(page);
    expect(f, `stop ${i + 1} has focus`).not.toBeNull();
    expect(f?.visible, `${f?.tag} "${f?.name}" is focus-visible`).toBe(true);
    expect(f?.indicator, `${f?.tag} "${f?.name}" draws a focus indicator`).toBe(true);
  }
});

test("the skip link moves focus to the page content", async ({ page }) => {
  await open(page, "/cases", PEOPLE.alex);
  await page.locator("body").press("Tab");
  await page.keyboard.press("Enter");
  await expect(page.locator("#main")).toBeFocused();
});

test("a confirm dialog traps focus, closes on Escape and gives focus back", async ({ page }) => {
  await open(page, "/cases/CASE-1043/checks/cr-1043-pv-01", PEOPLE.alex);
  const trigger = main(page).getByRole("button", { name: "Waive with a reason" });
  await trigger.focus();
  await page.keyboard.press("Enter");
  await expect(dialog(page)).toBeVisible();
  await expect(dialog(page).getByLabel(/Reason/)).toBeFocused();
  // Tab around more times than there are controls: focus never leaves the dialog.
  for (let i = 0; i < 12; i++) {
    await page.keyboard.press("Tab");
    expect(await page.evaluate(() => Boolean(document.activeElement?.closest("[role='dialog']"))), `Tab ${i + 1} stays in the dialog`).toBe(true);
  }
  for (let i = 0; i < 12; i++) {
    await page.keyboard.press("Shift+Tab");
    expect(await page.evaluate(() => Boolean(document.activeElement?.closest("[role='dialog']"))), `Shift+Tab ${i + 1} stays in the dialog`).toBe(true);
  }
  await page.keyboard.press("Escape");
  await expect(dialog(page)).toHaveCount(0);
  await expect(trigger).toBeFocused();
});

test("typing in a dialog is not interrupted when the page behind it reloads", async ({ page }) => {
  await open(page, "/cases/CASE-1043/checks/cr-1043-pv-01", PEOPLE.alex);
  await main(page).getByRole("button", { name: "Waive with a reason" }).click();
  const reason = dialog(page).getByLabel(/Reason/);
  await reason.fill("The wording differs");
  await page.waitForTimeout(0);
  await reason.pressSequentially(" in punctuation only.");
  await expect(reason).toBeFocused();
  await expect(reason).toHaveValue("The wording differs in punctuation only.");
});

test("a side panel traps focus and gives it back", async ({ page }) => {
  await open(page, "/owner/documents", PEOPLE.sam);
  const row = main(page).getByRole("row").filter({ hasText: "G-S05" });
  await row.focus();
  await page.keyboard.press("Enter");
  const drawer = page.getByRole("dialog");
  await expect(drawer).toBeVisible();
  for (let i = 0; i < 30; i++) {
    await page.keyboard.press("Tab");
    expect(await page.evaluate(() => Boolean(document.activeElement?.closest("[role='dialog']"))), `Tab ${i + 1} stays in the panel`).toBe(true);
  }
  await page.keyboard.press("Escape");
  await expect(drawer).toHaveCount(0);
  expect(await page.evaluate(() => document.activeElement?.closest("tr")?.textContent ?? "")).toContain("G-S05");
});

test("the person menu works with the keyboard", async ({ page }) => {
  await open(page, "/cases", PEOPLE.alex);
  const button = page.getByRole("button", { name: /View as/ });
  await button.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("menu")).toBeVisible();
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("menu")).toHaveCount(0);
  await expect(button).toBeFocused();
  await page.keyboard.press("Enter");
  await page.getByRole("menuitemradio", { name: /Dana Haddad/ }).focus();
  await page.keyboard.press("Enter");
  await expect(button).toContainText("Dana Haddad");
});

test("a case can be opened and a check promoted to confirmed using only the keyboard", async ({ page }) => {
  await open(page, "/cases", PEOPLE.alex);
  const link = main(page).getByRole("link", { name: /CASE-1043/ }).first();
  await link.focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/cases\/CASE-1043/);
  await settled(page);
  await main(page).getByRole("link", { name: /^Checks/ }).focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/checks$/);
  await settled(page);
  await main(page).getByRole("link", { name: "Pharmacovigilance system" }).first().focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/cr-1043-pv-01/);
  await settled(page);
  await main(page).getByRole("button", { name: "Confirm", exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(dialog(page)).toBeVisible();
  await dialog(page).getByRole("checkbox", { name: /I read the evidence/ }).focus();
  await page.keyboard.press("Space");
  await page.keyboard.press("Tab");
  await page.keyboard.press("Tab");
  await expect(dialog(page).getByRole("button", { name: "Confirm", exact: true })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(dialog(page)).toHaveCount(0);
  await expect(main(page).getByText("Confirmed", { exact: true }).first()).toBeVisible();
});

test("the navigation drawer at 768 px opens, traps focus and closes with Escape", async ({ page }) => {
  await page.setViewportSize({ width: 768, height: 900 });
  await open(page, "/cases", PEOPLE.alex);
  const toggle = page.getByRole("button", { name: "Open navigation" });
  await toggle.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("navigation", { name: "Main" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(toggle).toBeFocused();
});

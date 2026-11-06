// Smoke check: loads / and every registered route as a person who may open it, and
// fails on any console error, page error, or error state left on screen.
import { expect, type Page, test } from "@playwright/test";
import { CASE_TABS, ROUTES } from "../src/routes";

const SHOTS = process.env.SMOKE_SCREENSHOT_DIR;

const targets = [
  { url: "/", as: "p-alex", title: "home" },
  ...ROUTES.map((r) => ({ url: r.sample ?? r.path, as: r.as ?? "p-alex", title: r.title })),
  ...CASE_TABS.map((t) => ({ url: t.sample ?? "", as: t.as ?? "p-alex", title: `Case: ${t.title}` })),
];

function watchErrors(page: Page) {
  const errors: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(`console: ${msg.text()}`);
  });
  page.on("pageerror", (err) => errors.push(`pageerror: ${err.message}`));
  return errors;
}

test("every route loads with no errors", async ({ page }) => {
  test.setTimeout(240_000);
  const errors = watchErrors(page);
  for (const t of targets) {
    const sep = t.url.includes("?") ? "&" : "?";
    await page.goto(`${t.url}${sep}as=${t.as}`);
    await expect(page.locator("main h1").first(), `${t.title} (${t.url}) shows a heading`).toBeVisible({ timeout: 15_000 });
    // Let previews finish loading.
    await page.waitForTimeout(900);
    await expect(page.locator("main .error-state:not(.uikit-grid *)"), `${t.title} (${t.url}) has no error state`).toHaveCount(0);
    expect(errors, `${t.title} (${t.url})`).toEqual([]);
  }
});

test("the shell works at 768 px without horizontal scroll, and in dark mode", async ({ page }) => {
  const errors = watchErrors(page);
  await page.setViewportSize({ width: 768, height: 900 });
  await page.goto("/cases?as=p-alex");
  await expect(page.locator("main h1").first()).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  await page.getByRole("button", { name: "Open navigation" }).click();
  await expect(page.getByRole("navigation", { name: "Main" })).toBeVisible();
  await page.emulateMedia({ colorScheme: "dark" });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/cases/CASE-1042?as=p-alex");
  await expect(page.locator("main h1").first()).toBeVisible();
  expect(errors).toEqual([]);
});

test("switching person changes the navigation", async ({ page }) => {
  await page.goto("/cases?as=p-alex");
  await expect(page.getByRole("link", { name: "Case queue" })).toBeVisible();
  await page.getByRole("button", { name: /View as/ }).click();
  await page.getByRole("menuitemradio", { name: /Jordan Lee/ }).click();
  await expect(page.getByRole("link", { name: "Setup tracker" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Case queue" })).toHaveCount(0);
});

test("screenshot of the shell", async ({ page }) => {
  test.skip(!SHOTS, "set SMOKE_SCREENSHOT_DIR to save screenshots");
  await page.emulateMedia({ colorScheme: "light" });
  await page.goto("/cases/CASE-1042/checks?as=p-alex");
  await expect(page.locator("main h1").first()).toBeVisible();
  await page.waitForTimeout(1200);
  await page.screenshot({ path: `${SHOTS}/shell.png` });
  await page.emulateMedia({ colorScheme: "dark" });
  await page.waitForTimeout(300);
  await page.screenshot({ path: `${SHOTS}/shell-dark.png` });
  await page.emulateMedia({ colorScheme: "light" });
  await page.goto("/ui-kit?as=p-alex");
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${SHOTS}/ui-kit.png`, fullPage: true });
});

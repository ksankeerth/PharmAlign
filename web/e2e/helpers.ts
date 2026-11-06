// Shared helpers for the end-to-end and sweep tests. They run against a server in simulation mode.
import { expect, test, type APIRequestContext, type Locator, type Page } from "@playwright/test";

export const PEOPLE = {
  jordan: "p-jordan",
  sam: "p-sam",
  priya: "p-priya",
  alex: "p-alex",
  chris: "p-chris",
  dana: "p-dana",
  noor: "p-noor",
  robin: "p-robin",
} as const;

/**
 * Puts the demo data back as the seed has it, through the same endpoint as the Demo page: view as the
 * Platform Admin, post the reset, then poll until it is done. The reset runs in the background and
 * expires the session cookies. With the bge-m3 model installed, embedding the demo chunks takes over a
 * minute, and the library takes minutes more, so call this from beforeAll: it lengthens that hook's timeout.
 * The status is read with Authorization, because the cookie is cleared and the tables are replaced
 * during the reset, so a failed read is not the end of it.
 */
export async function resetDemo(request: APIRequestContext, options: { withLibrary?: boolean; timeoutMs?: number } = {}): Promise<number> {
  const withLibrary = options.withLibrary ?? false;
  const timeoutMs = options.timeoutMs ?? (withLibrary ? 600_000 : 120_000);
  test.setTimeout(Math.max(test.info().timeout, timeoutMs + 30_000));
  const started = Date.now();
  const admin = { Authorization: `Bearer ${PEOPLE.jordan}` };
  const finished = async () => {
    const res = await request.get("/api/v1/demo", { headers: admin }).catch(() => null);
    return Boolean(res?.ok()) && !(await res!.json()).resetting;
  };
  // A reset that an earlier run left going has to end first, or the server refuses a second one.
  await expect.poll(async () => ((await finished()) ? "done" : "waiting"), { timeout: timeoutMs, intervals: [500, 1000, 2000] }).toBe("done");
  expect((await request.post("/api/v1/session/view-as", { data: { person_id: PEOPLE.jordan } })).ok()).toBeTruthy();
  const reset = await request.post("/api/v1/demo/reset", { headers: admin, data: { confirm: "RESET", with_library: withLibrary } });
  expect(reset.status(), await reset.text()).toBe(202);
  await expect.poll(async () => ((await finished()) ? "done" : "waiting"), { timeout: timeoutMs, intervals: [500, 1000, 2000] }).toBe("done");
  return Date.now() - started;
}

/** Collects console errors and uncaught exceptions for the whole test. */
export function watchErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(`console: ${msg.text()}`);
  });
  page.on("pageerror", (err) => errors.push(`pageerror: ${err.message}`));
  return errors;
}

/** Opens a URL as a demo person and waits for the page heading. */
export async function open(page: Page, url: string, as: string): Promise<void> {
  const sep = url.includes("?") ? "&" : "?";
  await page.goto(`${url}${sep}as=${as}`);
  await expect(main(page).getByRole("heading", { level: 1 }).first()).toBeVisible({ timeout: 15_000 });
  await settled(page);
}

export const main = (page: Page): Locator => page.locator("main");

/** Switches person with the top bar menu, as a user would. */
export async function viewAs(page: Page, name: string): Promise<void> {
  await page.getByRole("button", { name: /View as/ }).click();
  await page.getByRole("menuitemradio", { name: new RegExp(name) }).click();
  await expect(page.getByRole("button", { name: /View as/ })).toContainText(name);
  await settled(page);
}

/** Switches the Platform Admin to another workspace with the top bar menu, as a user would. */
export async function switchWorkspace(page: Page, name: string): Promise<void> {
  const switcher = page.locator("header.topbar button.ws-switch");
  await switcher.click();
  await page.getByRole("menuitemradio", { name }).click();
  await expect(switcher).toContainText(name);
  await settled(page);
}

/**
 * Waits until the page has stopped loading: no skeletons, and no change to the page for a
 * short quiet period (the demo server answers each call in 150 to 400 ms).
 */
const LOADING = "main [role='status'][aria-label='Loading']:not(.uikit-grid *), main .skeleton:not(.uikit-grid *)";

export async function settled(page: Page, quietMs = 450): Promise<void> {
  await expect(page.locator(LOADING)).toHaveCount(0, { timeout: 20_000 });
  await page.evaluate(
    (quiet) =>
      new Promise<void>((resolve) => {
        const root = document.querySelector("main") ?? document.body;
        // A page with a live clock never goes quiet, so wait at most 3 s.
        window.setTimeout(done, 3000);
        let timer = window.setTimeout(done, quiet);
        const observer = new MutationObserver(() => {
          window.clearTimeout(timer);
          timer = window.setTimeout(done, quiet);
        });
        function done() {
          window.clearTimeout(timer);
          observer.disconnect();
          resolve();
        }
        observer.observe(root, { childList: true, subtree: true, characterData: true });
      }),
    quietMs,
  );
  await expect(page.locator(LOADING)).toHaveCount(0);
}

export async function horizontalOverflow(page: Page): Promise<number> {
  return page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
}

export const dialog = (page: Page): Locator => page.getByRole("dialog");

/** The toast region, for confirmations that appear after an action. */
export const toast = (page: Page, text: string | RegExp): Locator => page.locator("[role='status'], [role='alert']").filter({ hasText: text }).first();

/** Opens a case tab by clicking its link, as a user would. */
export async function openCaseTab(page: Page, name: string): Promise<void> {
  await page.getByRole("navigation", { name: "Case sections" }).getByRole("link", { name: new RegExp(`^${name}`) }).click();
  await settled(page);
}

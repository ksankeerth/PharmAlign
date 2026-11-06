// Whole-product sweep: every route, as a person allowed to open it, at desktop in light
// mode, desktop in dark mode and 768 px. Fails on a console error, an error state,
// horizontal page overflow, or a serious or critical accessibility issue (axe).
// Screenshots go outside the repo: SWEEP_DIR, or the system temp folder.
//
//   npm run sweep
import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { CASE_TABS, ROUTES } from "../src/routes";
import { horizontalOverflow, main, open, PEOPLE, settled, watchErrors } from "./helpers";

const DIR = process.env.SWEEP_DIR ?? join(tmpdir(), "pharmalign-qa");

const CONFIGS = [
  { name: "desktop-light", scheme: "light", width: 1440, height: 900 },
  { name: "desktop-dark", scheme: "dark", width: 1440, height: 900 },
  { name: "tablet-768", scheme: "light", width: 768, height: 1024 },
] as const;

interface Target {
  url: string;
  as: string;
  title: string;
}

const EXTRA: Target[] = [
  { url: "/", as: PEOPLE.alex, title: "Home as Alex" },
  { url: "/", as: PEOPLE.chris, title: "Home as Chris" },
  { url: "/", as: PEOPLE.dana, title: "Home as Dana" },
  { url: "/", as: PEOPLE.noor, title: "Home as Noor" },
  { url: "/", as: PEOPLE.sam, title: "Home as Sam" },
  { url: "/", as: PEOPLE.jordan, title: "Home as Jordan" },
  { url: "/", as: PEOPLE.robin, title: "Home as Robin" },
  { url: "/cases/CASE-1053/signing", as: PEOPLE.dana, title: "Signing, separation of duties refusal" },
  { url: "/cases/CASE-1053/signing", as: PEOPLE.noor, title: "Signing as the second Decision Maker" },
  { url: "/cases/CASE-1043/checks/cr-1043-pv-01", as: PEOPLE.alex, title: "Check awaiting confirmation" },
  { url: "/cases/CASE-1046/checks/cr-1046-legal-01", as: PEOPLE.alex, title: "Check with a gate refusal" },
  { url: "/cases/CASE-1049/checks/cr-1049-qual-07", as: PEOPLE.alex, title: "Check with no source found" },
  { url: "/cases/CASE-1050/checks/cr-1050-pv-01", as: PEOPLE.alex, title: "Check in Error" },
  { url: "/cases/CASE-1047/evidence", as: PEOPLE.alex, title: "Hidden text evidence" },
  { url: "/cases/CASE-1042/promote", as: PEOPLE.alex, title: "Promotion with failed guards" },
  { url: "/cases/decisions/dec-1039", as: PEOPLE.dana, title: "Decision acknowledged" },
  { url: "/cases/decisions/dec-1036", as: PEOPLE.dana, title: "Decision on hold" },
  { url: "/notifications", as: PEOPLE.dana, title: "Notifications as Dana" },
  { url: "/notifications", as: PEOPLE.sam, title: "Notifications as Sam" },
  { url: "/cases", as: PEOPLE.dana, title: "Case queue as Dana" },
  { url: "/cases", as: PEOPLE.robin, title: "Case queue as Robin" },
  { url: "/owner/packs/pv-ae-3", as: PEOPLE.sam, title: "Active pack, read only" },
  { url: "/admin/simulations/compare", as: PEOPLE.jordan, title: "Comparison as Jordan" },
  { url: "/watch/coverage", as: PEOPLE.robin, title: "Coverage as Robin" },
];

const targets: Target[] = [
  ...ROUTES.map((r) => ({ url: r.sample ?? r.path, as: r.as ?? PEOPLE.alex, title: r.title })),
  ...CASE_TABS.map((t) => ({ url: t.sample ?? "", as: t.as ?? PEOPLE.alex, title: `Case: ${t.title}` })),
  ...EXTRA,
];

const slug = (s: string) => s.replace(/[^a-z0-9]+/gi, "-").replace(/^-|-$/g, "").toLowerCase();

for (const config of CONFIGS) {
  test.describe(config.name, () => {
    test.use({ viewport: { width: config.width, height: config.height }, colorScheme: config.scheme });

    for (const [i, t] of targets.entries()) {
      test(`${String(i).padStart(2, "0")} ${t.title} (${t.url})`, async ({ page }) => {
        const errors = watchErrors(page);
        await open(page, t.url, t.as);
        await expect(main(page).locator(".error-state:not(.uikit-grid *), [role='alert'].error-state:not(.uikit-grid *)"), "no error state on screen").toHaveCount(0);
        // Not the home redirect target only: the page must show exactly one level-one heading.
        await expect(main(page).getByRole("heading", { level: 1 }), "exactly one h1").toHaveCount(1);
        expect(await horizontalOverflow(page), "no horizontal page overflow").toBeLessThanOrEqual(0);

        const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
        const bad = results.violations
          .filter((v) => v.impact === "serious" || v.impact === "critical")
          .map((v) => `${v.impact} ${v.id}: ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(" | ")}`);
        expect(bad, "axe serious or critical issues").toEqual([]);

        mkdirSync(`${DIR}/${config.name}`, { recursive: true });
        await settled(page, 200);
        await page.screenshot({ path: `${DIR}/${config.name}/${String(i).padStart(2, "0")}-${slug(t.title)}.png`, fullPage: true });
        expect(errors, "console errors").toEqual([]);
      });
    }
  });
}

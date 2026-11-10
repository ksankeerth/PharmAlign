import { defineConfig, devices } from "@playwright/test";
import { PEOPLE } from "./e2e/helpers";

// One config for all browser checks. They run against a server in simulation mode: the preview forwards
// /api to it (PHARMALIGN_API, default http://localhost:8000). It builds once and serves the
// production bundle. Each scenario file resets the demo data itself (resetDemo in e2e/helpers.ts).
//   npm run smoke   every route loads
//   npm run e2e     the scenarios, driving the real UI
//   npm run sweep   every route at three sizes and themes, axe, and keyboard use
// BASE_URL points the tests at a running web server, such as the web container, and skips the build.
const baseURL = process.env.BASE_URL ?? "http://localhost:5181";

export default defineConfig({
  testDir: "e2e",
  timeout: 90_000,
  expect: { timeout: 10_000 },
  fullyParallel: true,
  workers: process.env.CI ? 2 : 4,
  reporter: [["list"]],
  use: {
    baseURL, ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 },
    // Workflow checks start as returning visitors. The tour checks start with empty storage.
    storageState: {
      cookies: [],
      origins: [{
        origin: new URL(baseURL).origin,
        localStorage: Object.values(PEOPLE).map((id) => ({ name: `pharmalign.tour.${id}`, value: "seen" })),
      }],
    },
  },
  webServer: process.env.BASE_URL ? undefined : {
    command: "npm run build && npx vite preview --port 5181 --strictPort",
    url: "http://localhost:5181",
    reuseExistingServer: Boolean(process.env.REUSE_SERVER),
    timeout: 180_000,
  },
});

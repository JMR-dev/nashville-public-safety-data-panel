import { defineConfig, devices } from "@playwright/test";

export const API = "http://127.0.0.1:8099";
export const CONTROL = "http://127.0.0.1:8097";
const DASHBOARD = "http://127.0.0.1:4173";

// Map tiles are not what these flows are about, and a test suite has no business asking a
// public tile server for them: the build gets a single blank tile instead.
const BLANK_TILE =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=";

export default defineConfig({
  testDir: "e2e",
  // One fixture database serves every flow, and one of them publishes a call into it.
  workers: 1,
  fullyParallel: false,
  forbidOnly: process.env.CI !== undefined,
  reporter: process.env.CI === undefined ? "list" : [["list"], ["html", { open: "never" }]],
  use: { baseURL: DASHBOARD, trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: devices["Desktop Chrome"] }],
  webServer: [
    {
      command: "uv run python -m tests.fixture_api serve",
      url: `${API}/healthz`,
      reuseExistingServer: process.env.CI === undefined,
      stdout: "pipe",
    },
    {
      // Built into its own directory: this build has the blank tile baked in, so it must not
      // become the dist/ that gets deployed.
      command:
        "pnpm --filter frontend exec vite build --outDir dist-e2e && " +
        "pnpm --filter frontend exec vite preview --outDir dist-e2e --port 4173 --strictPort",
      url: DASHBOARD,
      reuseExistingServer: process.env.CI === undefined,
      env: {
        PANEL_API_ORIGIN: API,
        VITE_TILE_URL: BLANK_TILE,
        VITE_TILE_ATTRIBUTION: "Blank tiles for testing",
      },
    },
  ],
});

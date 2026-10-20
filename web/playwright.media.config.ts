import { defineConfig, devices } from "@playwright/test";

// The README's animated Explorer (`make readme-gif`): not a test, so it lives outside `e2e/` and
// never runs with the suite. Like the end-to-end tests, it runs against the production build.
export default defineConfig({
  testDir: "./media",
  reporter: "list",
  use: { baseURL: "http://127.0.0.1:3100" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "pnpm exec next start --port 3100 --hostname 127.0.0.1",
    url: "http://127.0.0.1:3100",
    reuseExistingServer: true,
  },
});

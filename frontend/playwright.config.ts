import { defineConfig, devices } from "@playwright/test";

const frontendUrl = process.env.E2E_FRONTEND_URL ?? "http://localhost:3000";
const apiUrl = process.env.E2E_API_URL ?? "http://localhost:8000";
const frontendPort = new URL(frontendUrl).port || "3000";

export default defineConfig({
  testDir: "./e2e",
  testIgnore: "**/integration.spec.ts",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: [["list"]],
  timeout: 30_000,
  use: {
    baseURL: frontendUrl,
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: [
    {
      command: "node ./scripts/start-e2e-api.js",
      url: `${apiUrl}/health`,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
    {
      command: `npm run dev -- -p ${frontendPort}`,
      url: frontendUrl,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      env: {
        NEXT_PUBLIC_API_URL: apiUrl,
      },
    },
  ],
});

import { defineConfig, devices } from "@playwright/test";

const frontendUrl =
  process.env.E2E_INTEGRATION_FRONTEND_URL ?? "http://localhost:3010";

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/integration.spec.ts",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: [["list"]],
  timeout: 180_000,
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
});

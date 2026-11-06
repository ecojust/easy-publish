import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/ui",
  testMatch: "*.spec.ts",
  fullyParallel: false,
  use: {
    baseURL: "http://127.0.0.1:1420",
    viewport: { width: 1440, height: 1050 },
    launchOptions: process.env.EASY_PUBLISH_CHROMIUM ? { executablePath: process.env.EASY_PUBLISH_CHROMIUM } : {},
    trace: "retain-on-failure",
  },
  webServer: { command: "npm run dev", url: "http://127.0.0.1:1420", reuseExistingServer: !process.env.CI },
});

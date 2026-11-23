import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
  testMatch: 'reset-contract-c3-realrt.spec.ts',
  workers: 1,
  retries: 0,
  timeout: 90_000,
  expect: { timeout: 15_000 },
  outputDir: `${process.env.C3_CHANNEL}/results`,
  reporter: [['line']],
  use: {
    channel: 'chromium',
    launchOptions: { executablePath: process.env.C3_CHROMIUM_EXECUTABLE },
    baseURL: process.env.C3_ORIGIN,
    ignoreHTTPSErrors: false,
    trace: 'retain-on-failure',
  },
});

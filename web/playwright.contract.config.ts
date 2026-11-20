import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
  testMatch: 'reset-contract-runtime.spec.ts',
  workers: 1,
  retries: 0,
  timeout: 300_000,
  expect: { timeout: 15_000 },
  outputDir: '/fixture/browser-results',
  reporter: [['./scripts/contract-c1-reporter.mjs']],
  use: {
    channel: 'chromium',
    headless: false,
    ignoreHTTPSErrors: false,
    trace: 'off',
    screenshot: 'off',
    video: 'off',
  },
});

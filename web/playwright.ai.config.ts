import { defineConfig } from '@playwright/test';

const externalBaseURL = process.env.DRAGON_AI_BASE_URL;

export default defineConfig({
  testDir: './tests/e2e',
  testMatch: 'reset-ai.spec.ts',
  outputDir: './.temp/reset-ai/results',
  workers: 1,
  timeout: 30000,
  expect: { timeout: 7000 },
  reporter: 'list',
  use: {
    baseURL: externalBaseURL || 'http://127.0.0.1:5187',
    browserName: 'chromium',
    channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge',
    ignoreHTTPSErrors: Boolean(externalBaseURL),
    locale: 'zh-CN',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: externalBaseURL ? undefined : {
    command: 'npm run dev -- --port 5187',
    env: {
      VITE_RESET_WORKBENCH: '1',
      VITE_PRIVATE_REVIEW: '1',
      VITE_PRIVATE_AI: '1',
    },
    url: 'http://127.0.0.1:5187',
    reuseExistingServer: false,
    timeout: 30000,
  },
});

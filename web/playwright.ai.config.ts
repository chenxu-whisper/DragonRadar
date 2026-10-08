import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
  testMatch: 'reset-ai.spec.ts',
  outputDir: './.temp/reset-ai/results',
  workers: 1,
  timeout: 30000,
  expect: { timeout: 7000 },
  reporter: 'list',
  use: {
    baseURL: 'http://127.0.0.1:5187',
    browserName: 'chromium',
    channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge',
    locale: 'zh-CN',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: {
    command: 'npm run dev -- --port 5187',
    env: {
      VITE_RESET_WORKBENCH: '1',
      VITE_PRIVATE_REVIEW: '0',
      VITE_PRIVATE_AI: '1',
    },
    url: 'http://127.0.0.1:5187',
    reuseExistingServer: false,
    timeout: 30000,
  },
});

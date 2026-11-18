import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
  testMatch: ['reset-ig04-05.spec.ts', 'reset-ig09.spec.ts'],
  workers: 2,
  timeout: 12000,
  expect: { timeout: 7000 },
  reporter: 'list',
  outputDir: '../../ig04-ig09-e2e/results',
  use: {
    baseURL: 'http://127.0.0.1:5179', browserName: 'chromium',
    channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', locale: 'zh-CN',
    trace: 'retain-on-failure', screenshot: 'only-on-failure',
  },
  webServer: {
    command: 'npm run dev -- --port 5179',
    env: { VITE_RESET_WORKBENCH: '1', VITE_PRIVATE_REVIEW: '0' },
    url: 'http://127.0.0.1:5179', reuseExistingServer: false, timeout: 30000,
  },
});

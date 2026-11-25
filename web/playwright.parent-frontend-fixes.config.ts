import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e', testMatch: 'parent-frontend-fixes.spec.ts',
  outputDir: '../.temp/parent-frontend-batched-fixes/browser-results',
  workers: 1, timeout: 20000, expect: { timeout: 4000 }, reporter: 'list',
  use: { baseURL: 'http://127.0.0.1:5216', browserName: 'chromium',
    channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge' },
  webServer: {
    command: 'npm run dev -- --port 5216 --strictPort',
    url: 'http://127.0.0.1:5216', reuseExistingServer: false,
    env: { VITE_RESET_WORKBENCH: '1', VITE_PRIVATE_AI: '1',
      VITE_PRIVATE_REVIEW: '1', VITE_PUBLIC_SHARING: '0' },
  },
});

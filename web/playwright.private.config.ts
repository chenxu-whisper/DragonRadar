import { defineConfig } from '@playwright/test';

// HTTP is only for route-mocked fixtures, never a production TLS acceptance test.
export default defineConfig({
  testDir: './tests/private',
  outputDir: './.temp/private/results',
  fullyParallel: true, workers: 2, timeout: 30000, expect: { timeout: 7000 },
  reporter: [['list'], ['html', { outputFolder: '.temp/private/report', open: 'never' }]],
  use: {
    baseURL: 'http://127.0.0.1:5186', browserName: 'chromium',
    channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', locale: 'zh-CN',
    trace: 'retain-on-failure', screenshot: 'only-on-failure',
  },
  webServer: {
    command: 'npm run dev -- --port 5186',
    env: { VITE_PRIVATE_REVIEW: '1' },
    url: 'http://127.0.0.1:5186', reuseExistingServer: false, timeout: 30000,
  },
});

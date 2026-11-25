import { defineConfig } from '@playwright/test';

const output = process.env.B8_OUTPUT_DIR || '../.temp/b8-residual/standalone';
export default defineConfig({
  testDir: './tests/e2e',
  testMatch: 'b8-residual.spec.ts',
  outputDir: `${output}/browser-results`,
  workers: 1,
  retries: 0,
  timeout: 120000,
  expect: { timeout: 7000 },
  reporter: [['list'], ['json', { outputFile: `${output}/browser.json` }]],
  use: {
    baseURL: 'http://127.0.0.1:5184',
    browserName: 'chromium',
    channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge',
    locale: 'zh-CN',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 5184 --strictPort',
    url: 'http://127.0.0.1:5184',
    reuseExistingServer: false,
    timeout: 30000,
    env: { VITE_RESET_WORKBENCH: '1', VITE_PRIVATE_REVIEW: '0' },
  },
});

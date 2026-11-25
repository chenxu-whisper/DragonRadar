import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
  testMatch: 'parent-source-clocks.spec.ts',
  outputDir: '../.temp/parent-b3-ui/source-clocks-results',
  workers: 1,
  timeout: 20000,
  expect: { timeout: 5000 },
  reporter: 'list',
  use: { browserName: 'chromium', channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge' },
  projects: [
    { name: 'owner-source-clocks', grepInvert: /public sharing/,
      use: { baseURL: 'http://127.0.0.1:5198' } },
    { name: 'public-no-source-clocks', grep: /public sharing/,
      use: { baseURL: 'http://127.0.0.1:5199' } },
  ],
  webServer: [
    {
      command: 'npm run dev -- --port 5198 --strictPort',
      env: { VITE_PUBLIC_SHARING: '0', VITE_PRIVATE_AI: '0', VITE_PRIVATE_REVIEW: '0' },
      url: 'http://127.0.0.1:5198', reuseExistingServer: false,
    },
    {
      command: 'npm run dev -- --port 5199 --strictPort',
      env: { VITE_PUBLIC_SHARING: '1', VITE_PRIVATE_AI: '0', VITE_PRIVATE_REVIEW: '0' },
      url: 'http://127.0.0.1:5199', reuseExistingServer: false,
    },
  ],
});

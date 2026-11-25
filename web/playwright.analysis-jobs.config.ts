import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e', testMatch: 'reset-analysis-jobs.spec.ts',
  outputDir: './.temp/analysis-jobs/results', workers: 1, timeout: 30000,
  expect: { timeout: 7000 }, reporter: 'list',
  projects: [
    { name: 'analysis-owner', grepInvert: /\[public\]/, use: { baseURL: 'http://127.0.0.1:5178', browserName: 'chromium', channel: 'msedge', locale: 'zh-CN' } },
    { name: 'analysis-public', grepInvert: /\[owner\]/, use: { baseURL: 'http://127.0.0.1:5179', browserName: 'chromium', channel: 'msedge', locale: 'zh-CN' } },
  ],
  webServer: [
    { command: 'npm run dev -- --port 5178', url: 'http://127.0.0.1:5178', reuseExistingServer: false,
      env: { VITE_RESET_WORKBENCH: '1', VITE_PRIVATE_REVIEW: '1', VITE_PUBLIC_SHARING: '0' } },
    { command: 'npm run dev -- --port 5179', url: 'http://127.0.0.1:5179', reuseExistingServer: false,
      env: { VITE_RESET_WORKBENCH: '1', VITE_PRIVATE_REVIEW: '0', VITE_PUBLIC_SHARING: '1' } },
  ],
});

import { defineConfig } from '@playwright/test';

const workbenchArgument = process.argv.some(
  value => /(?:^|[\\/])workbench\.spec\.ts$/.test(value),
);
if (workbenchArgument) process.env.DRAGON_EXPLICIT_WORKBENCH = '1';
const explicitWorkbench = workbenchArgument
  || process.env.DRAGON_EXPLICIT_WORKBENCH === '1';

const resetUse = {
  baseURL: 'http://127.0.0.1:5174',
  browserName: 'chromium' as const,
  channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge',
  locale: 'zh-CN',
  trace: 'retain-on-failure' as const,
  screenshot: 'only-on-failure' as const,
};

const resetServer = {
  command: 'npm run dev -- --port 5174',
  env: { VITE_RESET_WORKBENCH: '1', VITE_PRIVATE_REVIEW: '0' },
  url: 'http://127.0.0.1:5174',
  reuseExistingServer: false,
  timeout: 30000,
};

const common = {
  testDir: './tests/e2e',
  outputDir: './.temp/reset-navigation/results',
  workers: 2,
  timeout: 30000,
  expect: { timeout: 7000 },
  reporter: 'list',
};

export default defineConfig(explicitWorkbench ? {
  ...common,
  projects: [
    {
      name: 'reset-realtime',
      testMatch: 'reset-realtime.spec.ts',
      use: resetUse,
    },
    {
      name: 'disabled-realtime',
      testMatch: 'workbench.spec.ts',
      grep: /realtime feature flag disabled sends no realtime requests/,
      use: { ...resetUse, baseURL: 'http://127.0.0.1:5173' },
    },
  ],
  webServer: [
    resetServer,
    {
      command: 'npm run dev -- --port 5173',
      env: { VITE_RESET_WORKBENCH: '0', VITE_PRIVATE_REVIEW: '0' },
      url: 'http://127.0.0.1:5173',
      reuseExistingServer: false,
      timeout: 30000,
    },
  ],
} : {
  ...common,
  testMatch: 'reset-*.spec.ts',
  testIgnore: ['**/reset-ai.spec.ts', '**/reset-ai-public.spec.ts'],
  use: resetUse,
  webServer: resetServer,
});

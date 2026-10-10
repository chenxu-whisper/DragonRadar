import { defineConfig } from '@playwright/test';

const explicitResetRealtime = process.argv.some(
  value => /(?:^|[\\/])reset-realtime\.spec\.ts$/.test(value),
);

const defaultWebServer = {
  command: 'npm run dev',
  url: 'http://127.0.0.1:5173',
  reuseExistingServer: false,
  timeout: 30000,
};

const defaultConfig = defineConfig({
  testDir: './tests/e2e',
  testIgnore: '**/reset-*.spec.ts',
  outputDir: './.temp/playwright/results',
  fullyParallel: true,
  workers: 2,
  timeout: 30000,
  expect: { timeout: 7000 },
  reporter: [['list'], ['html', { outputFolder: '.temp/playwright/report', open: 'never' }]],
  use: {
    baseURL: 'http://127.0.0.1:5173',
    browserName: 'chromium',
    channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge',
    locale: 'zh-CN',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: defaultWebServer,
});

export default defineConfig({
  ...defaultConfig,
  ...(explicitResetRealtime ? { testIgnore: [] } : {}),
  webServer: {
    ...defaultWebServer,
    ...(explicitResetRealtime
      ? { env: { VITE_RESET_WORKBENCH: '1', VITE_PRIVATE_REVIEW: '0' } }
      : {}),
  },
});

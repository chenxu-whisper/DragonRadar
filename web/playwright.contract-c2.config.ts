import { defineConfig } from '@playwright/test';
import { resolve } from 'node:path';

const evidence = process.env.C2_EVIDENCE_DIR || '/evidence';
export default defineConfig({
  testDir: './tests/e2e',
  testMatch: 'reset-contract-c2*.spec.ts',
  workers: 1,
  retries: 0,
  timeout: 45000,
  expect: { timeout: 7000 },
  outputDir: resolve(evidence, 'browser-results'),
  reporter: [['list'], ['json', { outputFile: resolve(evidence, 'playwright.json') }]],
  webServer: {
    command: 'node scripts/contract-c2-server.mjs',
    url: 'http://127.0.0.1:4171',
    reuseExistingServer: false,
  },
});

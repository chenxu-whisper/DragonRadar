import { defineConfig } from '@playwright/test';
import base from './playwright.ai.config';

const readonly = process.env.DRAGON_IG08_READONLY === '1';

export default defineConfig({
  ...base,
  testMatch: readonly ? 'reset-ig08.spec.ts' : ['reset-ig08.spec.ts', 'reset-ig10-ai.spec.ts'],
  ...(readonly ? {
    grep: /read-only build has no review controls or private requests/,
    use: { ...base.use, baseURL: 'http://127.0.0.1:5191' },
    webServer: {
      command: 'npm run dev -- --port 5191',
      env: {
        VITE_RESET_WORKBENCH: '1', VITE_PUBLIC_SHARING: '1',
        VITE_PRIVATE_REVIEW: '0', VITE_PRIVATE_AI: '0',
      },
      url: 'http://127.0.0.1:5191', reuseExistingServer: false, timeout: 30000,
    },
  } : {}),
  workers: 1,
  outputDir: readonly ? '../../ig08-readonly-e2e/results' : '../../ig08-ig10-e2e/results',
});

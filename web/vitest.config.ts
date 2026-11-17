import { defineConfig } from 'vitest/config';

export default defineConfig({
  cacheDir: '.temp/vitest',
  test: {
    include: ['tests/unit/**/*.test.ts'],
    environment: 'node',
    testTimeout: 5000,
    restoreMocks: true,
    clearMocks: true,
  },
});

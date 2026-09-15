import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

describe('前端发布门禁', () => {
  it('CI 顺序执行默认构建、私人构建和两套 Playwright', () => {
    const workflow = readFileSync(resolve(process.cwd(), '../.github/workflows/ci.yml'), 'utf8');
    const defaultBuild = workflow.indexOf('npm run build');
    const privateBuild = workflow.indexOf('VITE_PRIVATE_REVIEW=1 npm run build');
    const install = workflow.indexOf('playwright install --with-deps chromium');
    const readonlyE2e = workflow.indexOf('npm run test:e2e');
    const privateE2e = workflow.indexOf('playwright.private.config.ts');

    expect(defaultBuild).toBeGreaterThan(-1);
    expect(privateBuild).toBeGreaterThan(defaultBuild);
    expect(install).toBeGreaterThan(privateBuild);
    expect(readonlyE2e).toBeGreaterThan(install);
    expect(privateE2e).toBeGreaterThan(readonlyE2e);
  });
});

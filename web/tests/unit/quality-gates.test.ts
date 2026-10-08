import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

describe('前端发布门禁', () => {
  it('为每种 E2E 环境提供独立且明确的脚本', () => {
    const packageJson = JSON.parse(
      readFileSync(resolve(process.cwd(), 'package.json'), 'utf8'),
    ) as { scripts: Record<string, string> };

    expect(packageJson.scripts).toMatchObject({
      'test:e2e': 'playwright test --config playwright.config.ts',
      'test:e2e:private': 'playwright test --config playwright.private.config.ts',
      'test:e2e:ai:public': 'playwright test --config playwright.ai-public.config.ts',
      'test:e2e:ai:private': 'playwright test --config playwright.ai.config.ts',
      'test:e2e:production-isolation': 'node scripts/test-production-isolation.mjs',
    });
  });

  it('宽泛的默认和 Reset 配置不收集专用 AI 用例', () => {
    for (const file of ['playwright.config.ts', 'playwright.reset.config.ts']) {
      const config = readFileSync(resolve(process.cwd(), file), 'utf8');
      expect(config).toContain("'**/reset-ai.spec.ts'");
      expect(config).toContain("'**/reset-ai-public.spec.ts'");
    }
  });

  it('CI 顺序执行构建、五套命名 E2E 门禁且不重复错配环境', () => {
    const workflow = readFileSync(resolve(process.cwd(), '../.github/workflows/ci.yml'), 'utf8');
    const defaultBuild = workflow.indexOf('npm run build');
    const privateBuild = workflow.indexOf('VITE_PRIVATE_REVIEW=1 npm run build');
    const install = workflow.indexOf('playwright install --with-deps chromium');
    const commands = [
      'npm run test:e2e',
      'npm run test:e2e:private',
      'npm run test:e2e:ai:public',
      'npm run test:e2e:ai:private',
      'npm run test:e2e:production-isolation',
    ];
    const positions = commands.map(command => workflow.indexOf(`- run: ${command}`));

    expect(defaultBuild).toBeGreaterThan(-1);
    expect(privateBuild).toBeGreaterThan(defaultBuild);
    expect(install).toBeGreaterThan(privateBuild);
    expect(positions[0]).toBeGreaterThan(install);
    positions.slice(1).forEach((position, index) => {
      expect(position).toBeGreaterThan(positions[index]);
    });
    commands.forEach(command => {
      expect(workflow.split(/\r?\n/)
        .filter(line => line.trim() === `- run: ${command}`)).toHaveLength(1);
    });
    expect(workflow).not.toContain('npx playwright test --config');
  });
});

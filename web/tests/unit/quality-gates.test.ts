import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const read = (file: string) => readFileSync(resolve(process.cwd(), file), 'utf8');

function workflowJob(workflow: string, name: string) {
  const lines = workflow.split(/\r?\n/);
  const start = lines.indexOf(`  ${name}:`);
  expect(start, `missing CI job ${name}`).toBeGreaterThan(-1);
  const next = lines.findIndex((line, index) => index > start && /^  [\w-]+:$/.test(line));
  return lines.slice(start + 1, next === -1 ? undefined : next).join('\n');
}

describe('前端发布门禁', () => {
  it('实时 Decimal 解码不引入运行时 JSON 解析依赖', () => {
    const packageJson = JSON.parse(read('package.json')) as {
      dependencies: Record<string, string>;
    };

    expect(packageJson.dependencies).not.toHaveProperty('lossless-json');
  });

  it('实时响应解码仅由 lazy feature client 静态引入', () => {
    const sharedApi = read('src/api.ts');
    const realtimeClient = read('src/features/realtime/client.ts');
    const realtimeHook = 'src/features/realtime/useRealtimeResource.ts';
    const sharedHook = read('src/hooks/useResource.tsx');

    expect(sharedApi).not.toContain('features/realtime');
    expect(sharedApi).not.toContain('parseRealtimeJson');
    expect(sharedApi).not.toContain('function validateSecurityResponse');
    expect(read('src/stock-validation.ts')).toContain('validateLegacySecurityResponse');
    expect(realtimeClient).toContain("from './json'");
    expect(realtimeClient).toContain('parseRealtimeJson');
    expect(existsSync(resolve(process.cwd(), realtimeHook))).toBe(true);
    expect(sharedHook).not.toContain('requester');
    expect(sharedHook).not.toContain('localErrorCodes');
    if (existsSync(resolve(process.cwd(), realtimeHook))) {
      expect(read(realtimeHook)).toContain('requestRealtime');
      expect(read(realtimeHook)).toContain('FORBIDDEN');
    }
  });

  it('为每种 E2E 环境提供独立且明确的脚本', () => {
    const packageJson = JSON.parse(read('package.json')) as { scripts: Record<string, string> };

    expect(packageJson.scripts).toMatchObject({
      'test:e2e': 'playwright test --config playwright.config.ts',
      'test:e2e:reset': 'playwright test --config playwright.reset.config.ts',
      'test:e2e:private': 'playwright test --config playwright.private.config.ts',
      'test:e2e:ai:public': 'playwright test --config playwright.ai-public.config.ts',
      'test:e2e:ai:private': 'playwright test --config playwright.ai.config.ts',
      'test:e2e:production-isolation': 'node scripts/test-production-isolation.mjs',
    });
  });

  it('每个 spec 家族仅由匹配其环境的命令收集一次', () => {
    expect(read('playwright.config.ts')).toContain("testIgnore: '**/reset-*.spec.ts'");
    expect(read('playwright.reset.config.ts')).toContain("testMatch: 'reset-*.spec.ts'");
    expect(read('playwright.reset.config.ts')).toContain('explicitWorkbench');
    expect(read('playwright.reset.config.ts')).toContain("'**/reset-ai.spec.ts'");
    expect(read('playwright.reset.config.ts')).toContain("'**/reset-ai-public.spec.ts'");
    expect(read('playwright.private.config.ts')).toContain("testDir: './tests/private'");
    expect(read('playwright.ai-public.config.ts')).toContain("testMatch: 'reset-ai-public.spec.ts'");
    expect(read('playwright.ai.config.ts')).toContain("testMatch: 'reset-ai.spec.ts'");

    const publicSpecs = readdirSync(resolve(process.cwd(), 'tests/e2e'))
      .filter(file => file.endsWith('.spec.ts'));
    for (const spec of publicSpecs) {
      const owners = [
        !spec.startsWith('reset-') && 'test:e2e',
        spec.startsWith('reset-') && !spec.startsWith('reset-ai') && 'test:e2e:reset',
        spec === 'reset-ai-public.spec.ts' && 'test:e2e:ai:public',
        spec === 'reset-ai.spec.ts' && 'test:e2e:ai:private',
      ].filter(Boolean);
      expect(owners, `${spec} must have one environment owner`).toHaveLength(1);
    }
    for (const spec of readdirSync(resolve(process.cwd(), 'tests/private'))
      .filter(file => file.endsWith('.spec.ts'))) {
      expect(['test:e2e:private'], `${spec} must have one environment owner`).toHaveLength(1);
    }
  });

  it('CI 以互不依赖的必需 job 执行全部 Web 门禁', () => {
    const workflow = readFileSync(resolve(process.cwd(), '../.github/workflows/ci.yml'), 'utf8');
    const jobs = {
      web_quality: ['npm test', 'npm run build', 'npm run check:bundle', 'VITE_PRIVATE_REVIEW=1 npm run build'],
      web_e2e_legacy: ['npm run test:e2e'],
      web_e2e_reset: ['npm run test:e2e:reset'],
      web_e2e_private: ['npm run test:e2e:private'],
      web_e2e_ai_public: ['npm run test:e2e:ai:public'],
      web_e2e_ai_private: ['npm run test:e2e:ai:private'],
      web_production_isolation: ['npm run test:e2e:production-isolation'],
      reset_performance: [
        'uv run pytest -q -rs tests/performance/test_reset_queries.py',
      ],
      task20_acceptance: [
        'uv run python scripts/verify_reset.py --mode full --output .temp/acceptance/reset-base.json',
      ],
    };

    for (const [name, commands] of Object.entries(jobs)) {
      const job = workflowJob(workflow, name);
      expect(job).not.toMatch(/^\s+needs:/m);
      expect(job).not.toContain('continue-on-error');
      commands.forEach(command => expect(job).toContain(`- run: ${command}`));
    }
    Object.values(jobs).flat().forEach(command => {
      expect(workflow.split(/\r?\n/)
        .filter(line => line.trim() === `- run: ${command}`)).toHaveLength(1);
    });
    expect(workflow).not.toContain('npx playwright test --config');
    expect(workflow).not.toContain('--performance-evidence');
    const acceptance = workflowJob(workflow, 'task20_acceptance');
    expect(acceptance).toContain('npm ci --ignore-scripts');
    expect(acceptance).toContain('npx playwright install --with-deps chromium');
    expect(acceptance).toContain('name: reset-task20-evidence');
  });
});

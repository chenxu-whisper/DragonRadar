import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it, vi } from 'vitest';

const read = (file: string) => readFileSync(resolve(process.cwd(), file), 'utf8');

function workflowJob(workflow: string, name: string) {
  const lines = workflow.split(/\r?\n/);
  const start = lines.indexOf(`  ${name}:`);
  expect(start, `missing CI job ${name}`).toBeGreaterThan(-1);
  const next = lines.findIndex((line, index) => index > start && /^  [\w-]+:$/.test(line));
  return lines.slice(start + 1, next === -1 ? undefined : next).join('\n');
}

// 仅断言本测试关心的两个配置面，避免把 Playwright 完整配置类型引入断言。
interface NarrowConfig {
  testIgnore?: string | string[];
  webServer?: { env?: Record<string, string> };
}

describe('CI web_e2e_reset 去重契约', () => {
  const workflow = read('../.github/workflows/ci.yml');
  const job = workflowJob(workflow, 'web_e2e_reset');

  it('reset 浏览器用例由 npm run test:e2e:reset 家族命令收集', () => {
    expect(job).toContain('- run: npm run test:e2e:reset');
  });

  it('保留 Task 21R 的 reset-realtime 聚焦行', () => {
    expect(job).toContain('- run: npx playwright test tests/e2e/reset-realtime.spec.ts');
  });

  it('不再包含独立的 reset-lhb 聚焦行（家族命令已唯一收集，禁止双跑）', () => {
    expect(job).not.toMatch(
      /^\s*- run: npx playwright test tests[\\/]e2e[\\/]reset-lhb\.spec\.ts\s*$/m,
    );
  });
});

describe('默认 playwright.config.ts 的 reset spec argv 检测', () => {
  interface ArgvCase {
    name: string;
    argv: string[];
    triggered: boolean;
  }

  const cases: ArgvCase[] = [
    {
      name: 'argv 含 tests/e2e/reset-lhb.spec.ts 时放开忽略并注入 workbench 环境变量',
      argv: ['node', 'playwright', 'test', 'tests/e2e/reset-lhb.spec.ts'],
      triggered: true,
    },
    {
      name: 'argv 含 tests/e2e/reset-realtime.spec.ts 时同样触发',
      argv: ['node', 'playwright', 'test', 'tests/e2e/reset-realtime.spec.ts'],
      triggered: true,
    },
    {
      name: 'argv 不含 reset spec 时保持默认忽略且不注入 env',
      argv: ['node', 'playwright', 'test'],
      triggered: false,
    },
    {
      name: 'reset-lhb-foo.spec.ts 后缀粘连不触发',
      argv: ['node', 'playwright', 'test', 'tests/e2e/reset-lhb-foo.spec.ts'],
      triggered: false,
    },
    {
      name: 'uploads/xreset-lhb.spec.ts 前缀粘连不触发',
      argv: ['node', 'playwright', 'test', 'uploads/xreset-lhb.spec.ts'],
      triggered: false,
    },
  ];

  async function loadConfigWithArgv(argv: string[]): Promise<NarrowConfig> {
    const originalArgv = process.argv;
    process.argv = [...argv];
    try {
      vi.resetModules();
      const loaded = (await import('../../playwright.config')).default as unknown;
      return loaded as NarrowConfig;
    } finally {
      process.argv = originalArgv;
    }
  }

  it.each(cases)('$name', async ({ argv, triggered }) => {
    const config = await loadConfigWithArgv(argv);

    if (triggered) {
      expect(config.testIgnore).toEqual([]);
      expect(config.webServer).toBeDefined();
      expect(config.webServer?.env).toMatchObject({
        VITE_RESET_WORKBENCH: '1',
        VITE_PRIVATE_REVIEW: '0',
      });
    } else {
      expect(config.testIgnore).toBe('**/reset-*.spec.ts');
      expect(config.webServer).toBeDefined();
      expect(config.webServer?.env).toBeUndefined();
    }
  });
});

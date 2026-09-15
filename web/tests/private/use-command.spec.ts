import { expect, test } from '@playwright/test';

async function mountAndChange(page: import('@playwright/test').Page, contextChange: 'slot' | 'epoch') {
  await page.goto('/');
  await page.evaluate(async change => {
    const load = (path: string) => import(/* @vite-ignore */ path);
    const { mountUseCommandHarness } = await load('/tests/private/useCommandHarness.tsx');
    await mountUseCommandHarness(change);
  }, contextChange);
  await page.locator('#start').click();
  await page.locator('#change').click();
  await expect.poll(() => page.evaluate(() => window.commandHarness.deferred.length)).toBe(2);
}

async function settledState(page: import('@playwright/test').Page) {
  return page.evaluate(() => {
    const latest = window.commandHarness.renders.at(-1);
    return {
      hasOriginalCommand: window.commandHarness.store.commands.has('slot-a'),
      accepted: window.commandHarness.accepted,
      error: latest?.error,
      conflict: latest?.conflict,
    };
  });
}

for (const contextChange of ['slot', 'owner', 'epoch'] as const) {
  test(`${contextChange} 变化的首次 render 立即为空闲且旧结果不能落地`, async ({ page }) => {
    await page.goto('/');
    await page.evaluate(async contextChange => {
      const load = (path: string) => import(/* @vite-ignore */ path);
      const { mountUseCommandHarness } = await load('/tests/private/useCommandHarness.tsx');
      await mountUseCommandHarness(contextChange);
    }, contextChange);

    await page.locator('#start').click();
    await expect(page.locator('#command-state')).toContainText('busy');
    await page.locator('#change').click();

    const changed = await page.evaluate(contextChange => {
      const harness = (window as typeof window & {
        commandHarness: {
          store: { commands: Map<string, unknown> };
          deferred: Array<{ resolve(value: { source: string }): void }>;
          renders: Array<{ generation: number; busy: boolean; error: string; conflict: boolean }>;
        };
      }).commandHarness;
      return {
        firstRender: harness.renders.find(render => render.generation === 1),
        sends: harness.deferred.length,
        hasUnknown: contextChange === 'owner' || harness.store.commands.has('slot-a'),
      };
    }, contextChange);
    expect(changed.firstRender).toEqual({ generation: 1, busy: false, error: '', conflict: false });
    expect(changed.sends).toBe(2);
    expect(changed.hasUnknown).toBe(true);

    await page.evaluate(() => {
      const harness = (window as typeof window & {
        commandHarness: {
          deferred: Array<{ resolve(value: { source: string }): void }>;
        };
      }).commandHarness;
      harness.deferred[0].resolve({ source: 'old' });
    });
    await expect.poll(() => page.evaluate(() => {
      const harness = (window as typeof window & {
        commandHarness: { accepted: string[] };
      }).commandHarness;
      return harness.accepted;
    })).toEqual([]);

    await page.evaluate(() => {
      const harness = (window as typeof window & {
        commandHarness: {
          deferred: Array<{ resolve(value: { source: string }): void }>;
        };
      }).commandHarness;
      harness.deferred[1].resolve({ source: 'new' });
    });
    await expect.poll(() => page.evaluate(() => {
      const harness = (window as typeof window & {
        commandHarness: { accepted: string[] };
      }).commandHarness;
      return harness.accepted;
    })).toEqual(['new']);
  });
}

for (const contextChange of ['slot', 'epoch'] as const) {
  test(`${contextChange} 切换后旧请求成功会结算原 slot 且不污染新上下文`, async ({ page }) => {
    await mountAndChange(page, contextChange);

    await page.evaluate(() => {
      window.commandHarness.deferred[0].resolve({ source: 'old' });
    });

    await expect.poll(() => settledState(page)).toEqual({
      hasOriginalCommand: false,
      accepted: [],
      error: '',
      conflict: false,
    });
  });

  test(`${contextChange} 切换后旧请求确定失败会结算原 slot 且不显示错误或冲突`, async ({ page }) => {
    await mountAndChange(page, contextChange);

    await page.evaluate(async () => {
      const load = (path: string) => import(/* @vite-ignore */ path);
      const { PrivateError } = await load('/src/features/review/api.ts');
      window.commandHarness.deferred[0].reject(
        new PrivateError(409, 'version_conflict', { version: 2 }),
      );
    });

    await expect.poll(() => settledState(page)).toEqual({
      hasOriginalCommand: false,
      accepted: [],
      error: '',
      conflict: false,
    });
  });

  test(`${contextChange} 切换后旧请求不确定失败仍保留原 slot 命令`, async ({ page }) => {
    await mountAndChange(page, contextChange);

    await page.evaluate(async () => {
      const load = (path: string) => import(/* @vite-ignore */ path);
      const { PrivateError } = await load('/src/features/review/api.ts');
      window.commandHarness.deferred[0].reject(new PrivateError(500, 'storage_unavailable'));
    });

    await expect.poll(() => settledState(page)).toEqual({
      hasOriginalCommand: true,
      accepted: [],
      error: '',
      conflict: false,
    });
  });
}

test('冲突错误不会泄漏到新 slot 的首次 render', async ({ page }) => {
  await page.goto('/');
  await page.evaluate(async () => {
    const load = (path: string) => import(/* @vite-ignore */ path);
    const { mountUseCommandHarness } = await load('/tests/private/useCommandHarness.tsx');
    await mountUseCommandHarness('slot');
  });
  await page.locator('#start').click();
  await page.evaluate(async () => {
    const load = (path: string) => import(/* @vite-ignore */ path);
    const { PrivateError } = await load('/src/features/review/api.ts');
    window.commandHarness.deferred[0].reject(new PrivateError(409, 'version_conflict', { version: 2 }));
  });
  await expect.poll(() => page.evaluate(() => {
    const latest = window.commandHarness.renders.at(-1);
    return { error: latest?.error, conflict: latest?.conflict };
  })).toEqual({ error: '版本冲突，请对照后手工处理。', conflict: true });

  await page.locator('#change').click();

  const firstRender = await page.evaluate(() =>
    window.commandHarness.renders.find(render => render.generation === 1));
  expect(firstRender).toEqual({ generation: 1, busy: false, error: '', conflict: false });
});

test('可清理当前上下文的错误与冲突反馈', async ({ page }) => {
  await page.goto('/');
  await page.evaluate(async () => {
    const load = (path: string) => import(/* @vite-ignore */ path);
    const { mountUseCommandHarness } = await load('/tests/private/useCommandHarness.tsx');
    await mountUseCommandHarness('slot');
  });
  await page.locator('#start').click();
  await page.evaluate(async () => {
    const load = (path: string) => import(/* @vite-ignore */ path);
    const { PrivateError } = await load('/src/features/review/api.ts');
    window.commandHarness.deferred[0].reject(
      new PrivateError(409, 'version_conflict', { version: 2 }),
    );
  });
  await expect.poll(() => page.evaluate(() => window.commandHarness.renders.at(-1))).toMatchObject({
    error: '版本冲突，请对照后手工处理。',
    conflict: true,
  });

  await page.locator('#clear-feedback').click();

  await expect.poll(() => page.evaluate(() => window.commandHarness.renders.at(-1))).toMatchObject({
    error: '',
    conflict: false,
  });
});

test('清理反馈不会删除结果未知的 pending 命令', async ({ page }) => {
  await page.goto('/');
  await page.evaluate(async () => {
    const load = (path: string) => import(/* @vite-ignore */ path);
    const { mountUseCommandHarness } = await load('/tests/private/useCommandHarness.tsx');
    await mountUseCommandHarness('slot');
  });
  await page.locator('#start').click();
  await page.evaluate(async () => {
    const load = (path: string) => import(/* @vite-ignore */ path);
    const { PrivateError } = await load('/src/features/review/api.ts');
    window.commandHarness.deferred[0].reject(new PrivateError(500, 'storage_unavailable'));
  });
  await expect.poll(() => page.evaluate(() => window.commandHarness.renders.at(-1)?.error))
    .toBe('服务暂不可用，结果未知，请重试原命令。');

  await page.locator('#clear-feedback').click();

  await expect.poll(() => page.evaluate(() => ({
    error: window.commandHarness.renders.at(-1)?.error,
    pending: window.commandHarness.store.commands.has('slot-a'),
  }))).toEqual({ error: '', pending: true });
});

for (const contextChange of ['slot', 'epoch'] as const) {
  test(`旧 ${contextChange} 的反馈清理函数不会清理新上下文`, async ({ page }) => {
    await mountAndChange(page, contextChange);
    await page.evaluate(async () => {
      const load = (path: string) => import(/* @vite-ignore */ path);
      const { PrivateError } = await load('/src/features/review/api.ts');
      window.commandHarness.deferred[1].reject(
        new PrivateError(409, 'version_conflict', { version: 3 }),
      );
    });
    await expect.poll(() => page.evaluate(() => window.commandHarness.renders.at(-1))).toMatchObject({
      error: '版本冲突，请对照后手工处理。',
      conflict: true,
    });

    await page.evaluate(() => window.commandHarness.clearFeedbacks[0]?.());

    await expect.poll(() => page.evaluate(() => window.commandHarness.renders.at(-1))).toMatchObject({
      error: '版本冲突，请对照后手工处理。',
      conflict: true,
    });
  });
}

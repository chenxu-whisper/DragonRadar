import { expect, test, type Page } from '@playwright/test';

const nextSnapshot = '00000000-0000-4000-8000-000000000002';

async function mount(page: Page, operation: 'submit' | 'cancel' | 'review' | 'adopt') {
  await page.goto('/');
  await page.evaluate(async value => {
    const load = (path: string) => import(/* @vite-ignore */ path);
    const { mountTaskPanelHarness } = await load('/tests/private/taskPanelHarness.tsx');
    await mountTaskPanelHarness(value);
  }, operation);
}

async function rerender(page: Page) {
  await page.evaluate(snapshotId => window.taskPanelHarness.rerender(snapshotId), nextSnapshot);
  await expect(page.getByRole('button', { name: '生成题材建议' })).toBeEnabled();
  await expect(page.getByText(/任务状态：/)).toHaveCount(0);
}

test('input signature change aborts a pending submit and ignores its completion', async ({ page }) => {
  await mount(page, 'submit');
  await page.getByRole('button', { name: '生成题材建议' }).click();
  await expect.poll(() => page.evaluate(() => window.taskPanelHarness.requests.length)).toBe(1);
  expect(await page.evaluate(() => window.taskPanelHarness.requests[0].signal?.aborted)).toBe(false);

  await rerender(page);

  expect(await page.evaluate(() => window.taskPanelHarness.requests[0].signal?.aborted)).toBe(true);
  await page.evaluate(() => {
    const harness = window.taskPanelHarness;
    harness.requests[0].resolve(harness.response(harness.task('succeeded')));
  });
  await expect(page.getByText(/任务状态：/)).toHaveCount(0);
  await expect(page.getByRole('button', { name: '生成题材建议' })).toBeEnabled();
});

test('unmount aborts a pending submit', async ({ page }) => {
  await mount(page, 'submit');
  await page.getByRole('button', { name: '生成题材建议' }).click();
  await expect.poll(() => page.evaluate(() => window.taskPanelHarness.requests.length)).toBe(1);

  await page.evaluate(() => window.taskPanelHarness.unmount());

  expect(await page.evaluate(() => window.taskPanelHarness.requests[0].signal?.aborted)).toBe(true);
  await expect(page.getByRole('region', { name: 'AI 题材建议' })).toHaveCount(0);
});

test('input change aborts stale cancel completion without restoring the old task', async ({ page }) => {
  await mount(page, 'cancel');
  await page.getByRole('button', { name: '生成题材建议' }).click();
  await expect(page.getByRole('button', { name: '取消任务' })).toBeVisible();
  await page.getByRole('button', { name: '取消任务' }).click();
  await expect.poll(() => page.evaluate(() =>
    window.taskPanelHarness.requests.some(item => item.path.endsWith('/cancel')))).toBe(true);

  await rerender(page);

  const aborted = await page.evaluate(() =>
    window.taskPanelHarness.requests.find(item => item.path.endsWith('/cancel'))?.signal?.aborted);
  expect(aborted).toBe(true);
  await page.evaluate(() => {
    const harness = window.taskPanelHarness;
    const request = harness.requests.find(item => item.path.endsWith('/cancel'))!;
    request.resolve(harness.response({
      task_id: '00000000-0000-4000-8000-000000000099',
      status: 'cancelled',
      idempotency_key: 'cancel-key',
      accepted_at: '2026-09-10T08:01:02Z',
    }));
  });
  await expect(page.getByText('取消命令已确认。')).toHaveCount(0);
  await expect(page.getByText(/任务状态：/)).toHaveCount(0);
});

test('input change aborts stale review completion without restoring the old draft', async ({ page }) => {
  await mount(page, 'review');
  await page.getByRole('button', { name: '生成题材建议' }).click();
  await expect(page.getByRole('button', { name: '接受校验草稿' })).toBeVisible();
  await page.getByRole('button', { name: '接受校验草稿' }).click();
  await expect.poll(() => page.evaluate(() =>
    window.taskPanelHarness.requests.some(item => item.path.endsWith('/review')))).toBe(true);

  await rerender(page);

  const aborted = await page.evaluate(() =>
    window.taskPanelHarness.requests.find(item => item.path.endsWith('/review'))?.signal?.aborted);
  expect(aborted).toBe(true);
  await page.evaluate(() => {
    const harness = window.taskPanelHarness;
    const request = harness.requests.find(item => item.path.endsWith('/review'))!;
    request.resolve(harness.response({
      task_id: '00000000-0000-4000-8000-000000000099',
      human_review_status: 'accepted',
      revision: 1,
      idempotency_key: 'review-key',
      verification_status: 'passed',
      note: 'accepted',
    }));
  });
  await expect(page.getByText('人工审核已记录。')).toHaveCount(0);
  await expect(page.getByText(/任务状态：/)).toHaveCount(0);
});

test('input change aborts stale adoption and suppresses its completion message', async ({ page }) => {
  await mount(page, 'adopt');
  await page.getByRole('button', { name: '生成题材建议' }).click();
  await expect(page.getByRole('button', { name: '采用草稿' })).toBeVisible();
  await page.getByRole('button', { name: '采用草稿' }).click();
  await expect.poll(() => page.evaluate(() => Boolean(window.taskPanelHarness.adoption))).toBe(true);

  await rerender(page);

  expect(await page.evaluate(() => window.taskPanelHarness.adoption?.signal.aborted)).toBe(true);
  await page.evaluate(() => window.taskPanelHarness.adoption?.resolve('stale adoption applied'));
  await expect(page.getByText('stale adoption applied')).toHaveCount(0);
  await expect(page.getByRole('button', { name: '生成题材建议' })).toBeEnabled();
});

import { expect, test } from '@playwright/test';
import { mockMarket } from '../fixtures/market';

const backup = {
  format_version: 1,
  exported_at: '2026-09-21T08:30:00+00:00',
  profile_mapping: { owner_id: 'old-owner', local_profile_id: 'old-profile' },
  counts: { notes: 1, revisions: 2, verifications: 1, watchlist: 1 },
  notes: [{
    note_id: 'restored-note', snapshot_id: 'snapshot', security_id: 'TEST001',
    current_revision: 2, created_at: '2026-09-20T08:00:00.000Z', updated_at: '2026-09-20T09:00:00.000Z',
  }],
  revisions: [
    {
      revision_id: 'old-r1', note_id: 'restored-note', revision: 1, state: 'draft',
      content: {
        title: '恢复笔记一', judgment: '第一版', evidence: '依据一',
        invalidation: '条件一', verification_date: '2026-09-11',
      },
      saved_at: '2026-09-20T08:00:00.000Z',
    },
    {
      revision_id: 'old-r2', note_id: 'restored-note', revision: 2, state: 'confirmed',
      content: {
        title: '恢复笔记二', judgment: '第二版', evidence: '依据二',
        invalidation: '条件二', verification_date: '2026-09-12',
      },
      saved_at: '2026-09-20T09:00:00.000Z',
    },
  ],
  verifications: [{
    verification_id: 'verify-1', note_id: 'restored-note', revision_id: 'old-r2',
    snapshot_id: 'verification-snapshot', outcome: 'refuted', evidence: '验证证据',
    supersedes: null, created_at: '2026-09-21T08:00:00.000Z',
  }],
  watchlist: [{
    watch_id: 'watch-1', security_id: 'TEST001', snapshot_id: 'snapshot', active: true,
    revision: 1, added_at: '2026-09-20T08:00:00.000Z', updated_at: '2026-09-20T08:00:00.000Z',
  }],
};

test.beforeEach(async ({}, testInfo) => {
  test.skip(!testInfo.project.use.baseURL?.endsWith(':5174'), 'Requires the explicit reset server.');
});

test('fresh browser restores every version and exports the local database', async ({ page }) => {
  await mockMarket(page, { empty: true });
  await page.goto('/?view=review');
  await page.getByLabel('选择复盘备份文件').setInputFiles({
    name: 'old-review.json',
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(backup)),
  });
  await expect(page.getByRole('status')).toContainText('修订 2');
  await page.getByLabel('导入方式').selectOption('merge');
  await expect(page.getByRole('region', { name: '备份与恢复' })).toContainText('重映射修订 2');
  await page.getByRole('button', { name: '执行恢复' }).click();
  await expect(page.getByRole('status')).toContainText('新增 1 条笔记、2 个修订、1 条验证、1 个自选');
  await page.getByRole('button', { name: '恢复笔记二' }).click();
  await page.getByRole('button', { name: '查看修订历史' }).click();
  await expect(page.getByRole('region', { name: '修订历史' }).getByText(/revision/)).toHaveText([
    /revision 2/, /revision 1/,
  ]);

  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: '导出本机备份' }).click();
  const file = await download;
  const stream = await file.createReadStream();
  const chunks: Buffer[] = [];
  for await (const chunk of stream) chunks.push(Buffer.from(chunk));
  const exported = JSON.parse(Buffer.concat(chunks).toString('utf-8'));
  expect(exported.counts).toEqual({ notes: 1, revisions: 2, verifications: 1, watchlist: 1 });
  expect(exported.revisions.map((row: { content: { title: string } }) => row.content.title))
    .toEqual(['恢复笔记一', '恢复笔记二']);
});

test('damaged backup leaves an existing database unchanged', async ({ page }) => {
  await mockMarket(page, { empty: true });
  await page.goto('/?view=review');
  await page.getByLabel('选择复盘备份文件').setInputFiles({
    name: 'valid.json',
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(backup)),
  });
  await page.getByLabel('导入方式').selectOption('merge');
  await page.getByRole('button', { name: '执行恢复' }).click();
  await expect(page.getByRole('button', { name: '恢复笔记二' })).toBeVisible();

  const damaged = structuredClone(backup);
  damaged.counts.revisions = 99;
  await page.getByLabel('选择复盘备份文件').setInputFiles({
    name: 'damaged.json',
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(damaged)),
  });
  await expect(page.getByRole('alert')).toContainText('备份文件无效');
  await expect(page.getByRole('button', { name: '恢复笔记二' })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('button', { name: '恢复笔记二' })).toBeVisible();
});

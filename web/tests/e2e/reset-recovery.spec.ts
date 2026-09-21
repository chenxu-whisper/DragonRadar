import { createHash } from 'node:crypto';
import type { Page } from '@playwright/test';
import { expect, test } from '@playwright/test';
import type { ReviewBackup } from '../../src/repositories/local/migrate';
import { mockMarket } from '../fixtures/market';

const backup: ReviewBackup = {
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

async function restore(page: Page, value: unknown, name = 'review.json') {
  await page.getByLabel('选择复盘备份文件').setInputFiles({
    name,
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(value)),
  });
  await page.getByLabel('导入方式').selectOption('merge');
  await page.getByRole('button', { name: '执行恢复' }).click();
}

async function downloadBackup(page: Page): Promise<ReviewBackup> {
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: '导出本机备份' }).click();
  const stream = await (await download).createReadStream();
  const chunks: Buffer[] = [];
  for await (const chunk of stream) chunks.push(Buffer.from(chunk));
  return JSON.parse(Buffer.concat(chunks).toString('utf-8')) as ReviewBackup;
}

async function localDatabase(page: Page) {
  return page.evaluate(async () => {
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      const opening = indexedDB.open('dragon-radar-local-v3');
      opening.onsuccess = () => resolve(opening.result);
      opening.onerror = () => reject(opening.error);
    });
    const read = <T>(store: string) => new Promise<T[]>((resolve, reject) => {
      const query = db.transaction(store).objectStore(store).getAll();
      query.onsuccess = () => resolve(query.result);
      query.onerror = () => reject(query.error);
    });
    const [notes, revisions, verifications] = await Promise.all([
      read<Record<string, unknown>>('notes'),
      read<Record<string, unknown>>('note_revisions'),
      read<Record<string, unknown>>('verification_records'),
    ]);
    db.close();
    return { notes, revisions, verifications };
  });
}

test('local export restores complete history and provenance in a fresh browser context', async ({ browser }) => {
  const sourceContext = await browser.newContext();
  const sourcePage = await sourceContext.newPage();
  await mockMarket(sourcePage, { empty: true });
  await sourcePage.goto('/?view=review');
  await sourcePage.getByLabel('选择复盘备份文件').setInputFiles({
    name: 'old-review.json',
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(backup)),
  });
  await expect(sourcePage.getByRole('status')).toContainText('修订 2');
  await sourcePage.getByLabel('导入方式').selectOption('merge');
  await sourcePage.getByRole('button', { name: '执行恢复' }).click();
  await expect(sourcePage.getByRole('status')).toContainText('新增 1 条笔记、2 个修订、1 条验证、1 个自选');
  const exported = await downloadBackup(sourcePage);
  await sourceContext.close();

  const restoreContext = await browser.newContext();
  const restoredPage = await restoreContext.newPage();
  await mockMarket(restoredPage, { empty: true });
  await restoredPage.goto('/?view=review');
  await restore(restoredPage, exported, 'local-export.json');
  await expect(restoredPage.getByRole('status')).toContainText('新增 1 条笔记、2 个修订、1 条验证、1 个自选');
  await restoredPage.getByRole('button', { name: '恢复笔记二' }).click();
  await restoredPage.getByRole('button', { name: '查看修订历史' }).click();
  await expect(restoredPage.getByRole('region', { name: '修订历史' }).getByText(/revision/)).toHaveText([
    /revision 2/, /revision 1/,
  ]);

  const restoredExport = await downloadBackup(restoredPage);
  expect(restoredExport.counts).toEqual(exported.counts);
  expect(restoredExport.notes).toEqual(exported.notes);
  expect(restoredExport.revisions).toEqual(exported.revisions);
  expect(restoredExport.verifications).toEqual(exported.verifications);
  expect(restoredExport.watchlist).toEqual(exported.watchlist);

  const stored = await localDatabase(restoredPage);
  const sourceHash = createHash('sha256').update(JSON.stringify(exported)).digest('hex');
  const sourceId = createHash('sha256').update(JSON.stringify(exported.profile_mapping)).digest('hex');
  expect(stored.revisions
    .sort((a, b) => Number(a.revision) - Number(b.revision))
    .map(row => ({
      note_id: row.note_id,
      revision: row.revision,
      state: row.state,
      content: row.content,
      source_backup_id: row.source_backup_id,
      source_backup_hash: row.source_backup_hash,
      source_note_id: row.source_note_id,
      source_revision: row.source_revision,
      source_revision_id: row.source_revision_id,
    }))).toEqual(exported.revisions.map(row => ({
      note_id: row.note_id,
      revision: row.revision,
      state: row.state,
      content: row.content,
      source_backup_id: sourceId,
      source_backup_hash: sourceHash,
      source_note_id: row.note_id,
      source_revision: row.revision,
      source_revision_id: row.revision_id,
    })));
  expect(stored.notes).toMatchObject([{
    note_id: 'restored-note',
    revision: 2,
    state: 'confirmed',
    source_revision_id: 'old-r2',
  }]);
  expect(stored.verifications).toMatchObject([{
    note_id: 'restored-note',
    revision: 2,
    verification_id: 'verify-1',
    supersedes: null,
  }]);
  await restoreContext.close();
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

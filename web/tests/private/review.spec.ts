import { expect, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { ID, mockMarket } from '../fixtures/market';
import { mockReview, NOTE, reviewServer } from '../fixtures/review';

test('编辑回读、响应丢失原键重试、确认与另存', async ({ page }) => {
  const db = reviewServer();
  await mockMarket(page); await mockReview(page, db);
  await page.goto(`/?view=review&note=${NOTE}&snapshot=${ID}`);
  await page.getByLabel('判断', { exact: true }).fill('新判断');
  db.lostResponse = true;
  await page.getByRole('button', { name: '保存草稿', exact: true }).click();
  await expect(page.getByRole('button', { name: '重试原命令' })).toBeVisible();
  await expect(page.getByLabel('判断', { exact: true })).toBeDisabled();
  await page.getByRole('button', { name: '重试原命令' }).click();
  await expect(page.getByText(/已保存.*版本 2/)).toBeVisible();
  expect(db.calls[0]).toEqual(db.calls[1]);
  await page.getByRole('button', { name: '确认复盘', exact: true }).click();
  await expect(page.getByText('已确认 · 只读')).toBeVisible();
  await page.getByRole('button', { name: '以此新建草稿' }).click();
  await expect(page.getByLabel('判断', { exact: true })).toBeEditable();
  expect(db.notes[0].derived_from_revision_id).not.toBeNull();
});

test('两窗口冲突三栏、Esc焦点与手工合并', async ({ page, context }) => {
  const db = reviewServer(), second = await context.newPage();
  for (const tab of [page, second]) {
    await mockMarket(tab); await mockReview(tab, db);
    await tab.goto(`/?view=review&note=${NOTE}&snapshot=${ID}`);
    await expect(tab.getByLabel('判断', { exact: true })).toHaveValue('原判断');
  }
  await page.getByLabel('判断', { exact: true }).fill('窗口一');
  await second.getByLabel('判断', { exact: true }).fill('窗口二');
  await second.getByRole('button', { name: '保存草稿', exact: true }).click();
  await expect(second.getByText(/已保存.*版本 2/)).toBeVisible();
  await page.getByRole('button', { name: '保存草稿', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '版本冲突' });
  await expect(dialog.getByText('原基线', { exact: true })).toBeVisible();
  await expect(dialog.getByText('窗口二', { exact: true })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('button', { name: '处理版本冲突' })).toBeFocused();
  await page.getByRole('button', { name: '处理版本冲突' }).click();
  await dialog.getByLabel('合并判断').fill('人工合并');
  await dialog.getByRole('button', { name: '采用手工合并' }).click();
  await page.getByRole('button', { name: '保存草稿', exact: true }).click();
  await expect(page.getByText(/已保存.*版本 3/)).toBeVisible();
  expect(db.notes[0].content.judgment).toBe('人工合并');
});

test('401隐藏并恢复同主体草稿，切主体销毁', async ({ page }) => {
  const db = reviewServer();
  await mockMarket(page); await mockReview(page, db);
  await page.goto(`/?view=review&note=${NOTE}&snapshot=${ID}`);
  await page.getByLabel('判断', { exact: true }).fill('内存独有');
  db.authorized = false;
  await page.getByRole('button', { name: '检查会话' }).click();
  await expect(page.getByRole('heading', { name: '私人会话已锁定' })).toBeVisible();
  await expect(page.getByLabel('判断', { exact: true })).not.toBeVisible();
  await page.getByLabel('一次性配对码').fill('synthetic-fixture-code');
  await page.getByRole('button', { name: '配对', exact: true }).click();
  await expect(page.getByLabel('判断', { exact: true })).toHaveValue('内存独有');
  db.authorized = false;
  await page.getByRole('button', { name: '检查会话' }).click();
  db.owner = '10000000-0000-4000-8000-000000000002';
  await page.getByLabel('一次性配对码').fill('synthetic-fixture-code');
  await page.getByRole('button', { name: '配对', exact: true }).click();
  await expect(page.getByText('记录不存在或当前不可见。')).toBeVisible();
  await expect(page.getByLabel('判断', { exact: true })).toHaveCount(0);
});

test('来源403保留原创可保存，离开取消保持URL', async ({ page }) => {
  const db = reviewServer(), market = { denied: false };
  await mockMarket(page, market); await mockReview(page, db);
  await page.goto(`/?view=review&note=${NOTE}&snapshot=${ID}`);
  await page.getByLabel('判断', { exact: true }).fill('保留原创');
  const before = page.url();
  page.once('dialog', dialog => dialog.dismiss());
  await page.getByRole('link', { name: '市场总览', exact: true }).click();
  expect(page.url()).toBe(before);
  await expect(page.getByLabel('判断', { exact: true })).toHaveValue('保留原创');
  market.denied = true;
  await page.getByRole('button', { name: '刷新快照' }).click();
  await expect(page.getByText('来源不可用，原创内容仍可保存。').first()).toBeVisible();
  await page.getByRole('button', { name: '保存草稿', exact: true }).click();
  await expect(page.getByText(/已保存.*版本 2/)).toBeVisible();
});

test('模板每次新建，自选移除和撤销', async ({ page }) => {
  const db = reviewServer();
  await mockMarket(page); await mockReview(page, db);
  await page.goto(`/?snapshot=${ID}`);
  await page.getByRole('button', { name: '加入自选 TEST001', exact: true }).click();
  await page.getByRole('link', { name: '我的复盘' }).click();
  await page.getByRole('button', { name: '自选', exact: true }).click();
  await page.getByRole('button', { name: '移除 TEST001' }).click();
  await page.getByRole('button', { name: '撤销移除 TEST001' }).click();
  expect(db.watches[0].active).toBe(true);
  expect(db.watches[0].version).toBe(3);
  await page.getByRole('button', { name: '生成每日复盘模板' }).click();
  await expect(page.getByLabel('标题', { exact: true })).toHaveValue('2026-09-10 复盘');
  await page.getByRole('button', { name: '生成每日复盘模板' }).click();
  expect(db.notes.filter(note => note.kind === 'daily')).toHaveLength(2);
});

test('自选版本冲突采用服务端状态后可继续操作', async ({ page }) => {
  const db = reviewServer();
  const watch = {
    id: '50000000-0000-4000-8000-000000000001',
    owner_id: db.owner,
    security_id: 'TEST001',
    snapshot_id: ID,
    active: true,
    version: 1,
    added_at: '2026-09-11 12:00:00+00:00',
    updated_at: '2026-09-11 12:00:00+00:00',
    facts: { status: 'unavailable' as const },
  };
  db.watches.push(watch);
  await mockMarket(page); await mockReview(page, db);
  await page.goto(`/?view=review&snapshot=${ID}`);
  await page.getByRole('button', { name: '自选', exact: true }).click();
  await expect(page.getByRole('button', { name: '移除 TEST001' })).toBeVisible();
  watch.active = false;
  watch.version = 2;

  await page.getByRole('button', { name: '移除 TEST001' }).click();
  await expect(page.getByRole('alert')).toHaveText('版本冲突，请对照后手工处理。');
  await expect(page.getByText('服务端版本 2：已移除。')).toBeVisible();
  await page.getByRole('button', { name: '采用最新状态' }).click();

  await expect(page.getByRole('alert')).toHaveCount(0);
  await expect(page.getByText('服务端版本 2：已移除。')).toHaveCount(0);
  await expect(page.getByText('已移除 · v2', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '撤销移除 TEST001' }).click();
  await expect(page.getByText('自选中 · v3', { exact: true })).toBeVisible();
  expect(db.calls.at(-1)?.match).toBe('"2"');
  expect(watch.active).toBe(true);
});

test('人工验证显式快照与更正保留历史链', async ({ page }) => {
  const db = reviewServer();
  db.notes[0].state = 'confirmed'; db.revisions[0].state = 'confirmed';
  await mockMarket(page); await mockReview(page, db);
  await page.goto(`/?view=review&note=${NOTE}&snapshot=${ID}`);
  await page.getByRole('button', { name: '人工验证', exact: true }).click();
  await expect(page.getByText('尚未验证', { exact: true })).toBeVisible();
  await page.getByLabel('验证快照日期').fill('2026-09-10');
  await page.getByLabel('验证快照', { exact: true }).selectOption(ID);
  await page.getByLabel('人工结论').selectOption('supported');
  await page.getByLabel('验证依据').fill('人工核验依据');
  await page.getByRole('button', { name: '记录验证', exact: true }).click();
  await expect(page.getByText('人工核验依据', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '更正此记录' }).click();
  await page.getByLabel('验证快照日期').fill('2026-09-10');
  await page.getByLabel('验证快照', { exact: true }).selectOption(ID);
  await page.getByLabel('人工结论').selectOption('weakened');
  await page.getByLabel('验证依据').fill('补充人工更正依据');
  await page.getByRole('button', { name: '记录验证', exact: true }).click();
  await expect(page.getByText('补充人工更正依据', { exact: true })).toBeVisible();
  expect(db.verifications[0].supersedes).toBe(db.verifications[1].id);
});

for (const width of [375, 768, 1024, 1440]) {
  test(`私人编辑 ${width}px、200%缩放、减少动态与axe`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await mockMarket(page); await mockReview(page, reviewServer());
    await page.goto(`/?view=review&note=${NOTE}&snapshot=${ID}`);
    await expect(page.getByLabel('判断', { exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    await page.screenshot({ path: `.temp/private/editor-${width}.png`, fullPage: true });
    await page.addStyleTag({ content: 'html { zoom: 2; }' });
    const overflow = await page.evaluate(() => ({
      width: document.documentElement.scrollWidth, viewport: innerWidth,
      elements: [...document.querySelectorAll('body *')].filter(element => element.getBoundingClientRect().right > innerWidth)
        .slice(0, 12).map(element => ({ tag: element.tagName, class: element.className, right: element.getBoundingClientRect().right })),
    }));
    expect(overflow.width, JSON.stringify(overflow)).toBeLessThanOrEqual(overflow.viewport);
    await expect(page.getByRole('button', { name: '保存草稿', exact: true })).toBeEnabled();
  });
}

import { expect, test } from '@playwright/test';
import { ID, mockMarket } from '../fixtures/market';

test('local notes survive reload and offline; watchlist needs no owner session', async ({ page }) => {
  await mockMarket(page);
  const privateRequests: string[] = [];
  page.on('request', request => {
    if (/\/api\/owner\/.*(notes|watchlist)/.test(request.url())) privateRequests.push(request.url());
  });
  await page.goto(`/?view=review&snapshot=${ID}&date=2026-09-10`);
  await page.getByLabel('标题', { exact: true }).fill('本地恢复验证');
  await page.getByRole('textbox', { name: '正文', exact: true }).fill('不上传服务器的判断');
  await page.getByRole('button', { name: '保存笔记', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: '回读验证通过' })).toBeVisible();
  await page.reload();
  await page.getByRole('button', { name: '本地恢复验证', exact: true }).click();
  await expect(page.getByRole('textbox', { name: '正文', exact: true })).toHaveValue('不上传服务器的判断');
  await page.getByLabel('证券代码', { exact: true }).fill('SSE.600519');
  await page.getByRole('button', { name: '添加自选', exact: true }).click();
  await expect(page.getByTestId('local-watchlist')).toContainText('SSE.600519');
  await page.context().setOffline(true);
  await page.getByRole('textbox', { name: '正文', exact: true }).fill('断网后仍可保存');
  await page.getByRole('button', { name: '保存笔记', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: '回读验证通过' })).toContainText('revision 2');
  expect(privateRequests).toEqual([]);
});

test('two tabs preserve unsaved input and offer save-as on revision conflict', async ({ page, context }) => {
  await mockMarket(page);
  await page.goto(`/?view=review&snapshot=${ID}&date=2026-09-10`);
  await page.getByLabel('标题', { exact: true }).fill('共同笔记');
  await page.getByRole('button', { name: '保存笔记', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: '回读验证通过' })).toBeVisible();
  const second = await context.newPage();
  await mockMarket(second);
  await second.goto(`/?view=review&snapshot=${ID}&date=2026-09-10`);
  await second.getByRole('button', { name: '共同笔记', exact: true }).click();
  await second.getByRole('textbox', { name: '正文', exact: true }).fill('标签二草稿');
  await page.getByRole('textbox', { name: '正文', exact: true }).fill('标签一已保存');
  await page.getByRole('button', { name: '保存笔记', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: '回读验证通过' })).toContainText('revision 2');
  await second.getByRole('button', { name: '保存笔记', exact: true }).click();
  await expect(second.getByRole('alert').filter({ hasText: '版本冲突' })).toBeVisible();
  await expect(second.getByRole('textbox', { name: '正文', exact: true })).toHaveValue('标签二草稿');
  await second.getByRole('button', { name: '另存为新笔记', exact: true }).click();
  await expect(second.getByRole('status').filter({ hasText: '回读验证通过' })).toContainText('revision 1');
});

test('stock drawer adds a local watch and opens a bound review', async ({ page }) => {
  await mockMarket(page);
  await page.goto(`/?view=overview&snapshot=${ID}&date=2026-09-10&q=TEST001`);
  await page.getByRole('button', { name: '合成测试证券1', exact: true }).click();
  const drawer = page.getByRole('dialog');
  await drawer.getByRole('button', { name: '加入本地自选', exact: true }).click();
  await expect(drawer.getByRole('status').filter({ hasText: '已加入本地自选' })).toBeVisible();
  await drawer.getByRole('button', { name: '记录本地判断', exact: true }).click();
  await expect(page).toHaveURL(/view=review/);
  await expect(page).toHaveURL(/security=TEST001/);
  await expect(page.getByTestId('local-watchlist')).toContainText('TEST001');
  await expect(page.getByLabel('证券代码', { exact: true })).toHaveValue('TEST001');
});

import { expect, test } from '@playwright/test';
import { ID, OLD, mockMarket, snapshot, type MockState } from '../fixtures/market';

test('changing trade date clears comparison rather than applying it to another snapshot', async ({ page }) => {
  await mockMarket(page);
  await page.goto(`/?snapshot=${ID}&compare=TEST001,TEST002`);
  await expect(page.getByLabel('比较 TEST001', { exact: true })).toBeChecked();
  await page.getByLabel('交易日期', { exact: true }).fill('2026-09-09');
  await expect(page).toHaveURL(new RegExp(OLD));
  await expect(page.getByLabel('比较 TEST001', { exact: true })).not.toBeChecked();
  await expect(page).not.toHaveURL(/compare=/);
});

test('same-date version change clears comparison and honors selected version', async ({ page }) => {
  await mockMarket(page);
  const revision = { ...snapshot, snapshot_id: OLD, result: { ...snapshot.result, revision: 2, supersedes: ID } };
  await page.route('**/snapshots?trade_date=2026-09-10&limit=100&offset=0', route => route.fulfill({ json: [snapshot, revision] }));
  await page.route(`**/snapshots/${OLD}`, route => route.fulfill({ json: revision }));
  await page.goto(`/?snapshot=${ID}&compare=TEST001`);
  await expect(page.getByLabel('快照版本', { exact: true }).locator('option')).toHaveCount(2);
  await page.getByLabel('快照版本', { exact: true }).selectOption(OLD);
  await expect(page.getByText(/版本性质.*修订第 2 版/)).toBeVisible();
  await expect(page.getByLabel('比较 TEST001', { exact: true })).not.toBeChecked();
  await expect(page).not.toHaveURL(/compare=/);
});

test('conflicting URL date is visibly reconciled to the direct snapshot, never used to fetch latest', async ({ page }) => {
  const paths: string[] = [];
  page.on('request', request => { if (request.url().includes('/api/')) paths.push(request.url()); });
  await mockMarket(page);
  await page.goto(`/?snapshot=${OLD}&date=2026-09-10`);
  await expect(page.getByRole('alert').filter({ hasText: '日期与快照不一致' })).toBeVisible();
  await expect(page.getByLabel('交易日期', { exact: true })).toHaveValue('2026-09-09');
  await expect(page).toHaveURL(new RegExp(OLD));
  expect(paths.some(url => /snapshots\?limit/.test(url))).toBe(false);
});

test('403 clears comparison and security selections before permission recovery', async ({ page }) => {
  const state: MockState = {};
  await mockMarket(page, state);
  await page.goto(`/?view=stock&snapshot=${ID}&compare=TEST001&security=TEST001`);
  await expect(page.getByRole('heading', { name: '合成测试证券1' })).toBeVisible();
  state.denied = true;
  await page.getByRole('button', { name: '刷新快照' }).click();
  await expect(page.getByRole('heading', { name: '来源访问已被拒绝' })).toBeVisible();
  await expect(page).not.toHaveURL(/compare=|security=/);
  state.denied = false;
  await page.getByRole('button', { name: '重新检查访问权限' }).click();
  await expect(page.getByText('未指定证券，请返回研究列表选择。')).toBeVisible();
  await expect(page.getByRole('heading', { name: '合成测试证券1' })).toHaveCount(0);
});

test('drawer traps focus and closes on browser history changes', async ({ page }) => {
  await mockMarket(page);
  await page.goto('/');
  await page.getByRole('button', { name: '下一页', exact: true }).click();
  const trigger = page.getByRole('button', { name: '合成测试证券51', exact: true });
  await trigger.click();
  await expect(page.getByRole('button', { name: '关闭详情' })).toBeFocused();
  await page.keyboard.press('Shift+Tab');
  expect(await page.evaluate(() => document.activeElement?.closest('dialog') !== null)).toBe(true);
  await page.keyboard.press('Escape');
  await expect(trigger).toBeFocused();
  await trigger.click();
  await page.goBack();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.getByRole('button', { name: '合成测试证券1', exact: true })).toBeVisible();
});

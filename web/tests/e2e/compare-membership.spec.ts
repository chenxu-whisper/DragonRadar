import { expect, test } from '@playwright/test';
import { ID, OLD, detail, envelope, mockMarket } from '../fixtures/market';

test('deep-link membership removes concurrent unknown IDs despite an insufficient interval and keeps its notice', async ({ page }) => {
  await mockMarket(page);
  const checked: string[] = [];
  page.on('request', request => {
    const match = new URL(request.url()).pathname.match(/\/securities\/([^/]+)$/);
    if (match) checked.push(match[1]);
  });
  await page.route('**/securities/MISSING*', route => route.fulfill({ status: 404, json: {} }));
  await page.goto(`/?snapshot=${OLD}&n=250&compare=MISSING1,TEST001,MISSING2,TEST002,TEST003`);
  await expect(page.getByRole('alert').filter({ hasText: '研究区间不可用' })).toBeVisible();
  await expect(page).not.toHaveURL(/MISSING/);
  await expect(page.getByRole('status').filter({ hasText: '已选 3 / 5' })).toBeVisible();
  expect([...new Set(checked)].sort()).toEqual(['MISSING1', 'MISSING2', 'TEST001', 'TEST002', 'TEST003']);
  expect(checked.length).toBeLessThanOrEqual(5);
  const notice = page.getByRole('alert').filter({ hasText: '已移除' });
  await expect(notice).toContainText('MISSING1');
  await expect(notice).toContainText('MISSING2');
  await page.getByLabel('比较 TEST004', { exact: true }).check();
  await page.getByLabel('比较 TEST005', { exact: true }).check();
  await expect(page.getByRole('status').filter({ hasText: '已选 5 / 5' })).toBeVisible();
  await expect(notice).toBeVisible();
  await page.getByRole('button', { name: '关闭比较校验提示' }).click();
  await expect(notice).toHaveCount(0);
});

for (const failure of ['network', '503'] as const) {
  test(`membership ${failure} retains selection and supports retry without assuming 404`, async ({ page }) => {
    await mockMarket(page);
    let unavailable = true;
    await page.route('**/securities/TEST001', route => unavailable
      ? failure === 'network' ? route.abort('failed') : route.fulfill({ status: 503, json: {} })
      : route.fulfill({ json: envelope(detail('TEST001')) }));
    await page.goto(`/?snapshot=${ID}&compare=TEST001`);
    const retry = page.getByRole('button', { name: '重试比较标的 TEST001', exact: true });
    await expect(retry).toBeVisible();
    await expect(page).toHaveURL(/compare=TEST001/);
    await expect(page.getByLabel('比较 TEST001', { exact: true })).toBeChecked();
    unavailable = false;
    const response = page.waitForResponse(response => response.url().endsWith('/securities/TEST001') && response.ok());
    await retry.click();
    await response;
    await expect(retry).toHaveCount(0);
    await expect(page).toHaveURL(/compare=TEST001/);
    await expect(page.getByRole('alert').filter({ hasText: '已移除' })).toHaveCount(0);
  });
}

test('membership-only 403 revokes overview, comparison, versions and an open drawer', async ({ page }) => {
  await mockMarket(page);
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/securities/TEST001', async route => {
    await gate;
    await route.fulfill({ status: 403, json: {} });
  });
  await page.goto(`/?snapshot=${ID}&compare=TEST001`);
  await page.getByRole('button', { name: '合成测试证券2', exact: true }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  release();
  await expect(page.getByRole('heading', { name: '来源访问已被拒绝' })).toBeVisible();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.getByRole('table')).toHaveCount(0);
  await expect(page.locator('option').filter({ hasText: '首版' })).toHaveCount(0);
  await expect(page).not.toHaveURL(/compare=|security=/);
  await page.getByRole('button', { name: '重新检查访问权限' }).click();
  await expect(page.getByRole('table', { name: '证券列表' })).toBeVisible();
  await expect(page.getByLabel('比较 TEST001', { exact: true })).not.toBeChecked();
});

test('late membership 404 from the previous snapshot cannot remove a current selection', async ({ page }) => {
  await mockMarket(page);
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  let settled!: () => void;
  const finished = new Promise<void>(resolve => { settled = resolve; });
  await page.route(`**/snapshots/${OLD}/securities/TEST001`, async route => {
    await gate;
    await route.fulfill({ status: 404, json: {} });
    settled();
  });
  const pending = page.waitForRequest(request => request.url().includes(`${OLD}/securities/TEST001`));
  await page.goto(`/?snapshot=${OLD}&compare=TEST001`);
  await pending;
  const current = page.waitForResponse(response => response.url().includes(`${ID}/securities/TEST001`) && response.ok());
  await page.evaluate(snapshot => {
    history.pushState({}, '', `/?snapshot=${snapshot}&compare=TEST001`);
    window.dispatchEvent(new PopStateEvent('popstate'));
  }, ID);
  await current;
  release();
  await finished;
  await expect(page.getByLabel('交易日期', { exact: true })).toHaveValue('2026-09-10');
  await expect(page.getByLabel('比较 TEST001', { exact: true })).toBeChecked();
  await expect(page).toHaveURL(/compare=TEST001/);
  await expect(page.getByRole('alert').filter({ hasText: '已移除' })).toHaveCount(0);
});

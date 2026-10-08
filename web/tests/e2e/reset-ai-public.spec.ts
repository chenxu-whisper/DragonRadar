import { expect, test } from '@playwright/test';

import { ID, mockMarket } from '../fixtures/market';

test('public Reset denies review deep links without opening IndexedDB', async ({ page }) => {
  await mockMarket(page);
  const privateRequests: string[] = [];
  page.on('request', request => {
    const path = new URL(request.url()).pathname;
    if (path.startsWith('/api/ai/') || path.startsWith('/api/owner/')) {
      privateRequests.push(path);
    }
  });

  await page.goto(`/?view=review&snapshot=${ID}&date=2026-09-10`);

  await expect(page).toHaveURL(url => url.searchParams.get('view') === 'overview');
  await expect(page.getByRole('link', { name: '我的复盘' })).toHaveCount(0);
  await expect(page.getByRole('heading', { name: '我的复盘' })).toHaveCount(0);
  await expect(page.getByRole('region', { name: 'AI 每日复盘' })).toHaveCount(0);
  await expect(page.getByText('AI 每日复盘')).toHaveCount(0);
  expect(await page.evaluate(async () => (await indexedDB.databases()).map(item => item.name))).toEqual([]);
  expect(privateRequests).toEqual([]);
});

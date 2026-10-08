import { expect, test } from '@playwright/test';

import { ID, mockMarket } from '../fixtures/market';

test('public Reset makes zero owner or AI requests and exposes no AI panel', async ({ page }) => {
  await mockMarket(page);
  const privateRequests: string[] = [];
  page.on('request', request => {
    const path = new URL(request.url()).pathname;
    if (path.startsWith('/api/ai/') || path.startsWith('/api/owner/')) {
      privateRequests.push(path);
    }
  });

  await page.goto(`/?view=review&snapshot=${ID}&date=2026-09-10`);

  await expect(page.getByRole('heading', { name: '我的复盘' })).toBeVisible();
  await expect(page.getByRole('region', { name: 'AI 每日复盘' })).toHaveCount(0);
  await expect(page.getByText('AI 每日复盘')).toHaveCount(0);
  expect(privateRequests).toEqual([]);
});

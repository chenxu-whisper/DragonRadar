import { expect, test } from '@playwright/test';
import { mockMarket } from '../fixtures/market';
import { mockReview, NOTE, reviewServer } from '../fixtures/review';

const CANONICAL_SEARCH = `?view=review&sort=security_id&order=asc&page=1&page_size=50&n=20&return_view=overview&note=${NOTE}&role=trend_leader`;

test('私人构建保留私人深链', async ({ page }) => {
  await mockMarket(page, { empty: true });
  await mockReview(page, reviewServer());

  await page.goto(`/?view=review&note=${NOTE}#fragment`);

  await expect(page.getByRole('heading', { name: '我的复盘' })).toBeVisible();
  await expect.poll(() => new URL(page.url()).search).toBe(CANONICAL_SEARCH);
  expect(new URL(page.url()).pathname).toBe('/');
  expect(new URL(page.url()).hash).toBe('#fragment');
});

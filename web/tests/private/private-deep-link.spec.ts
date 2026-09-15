import { expect, test } from '@playwright/test';
import { mockMarket } from '../fixtures/market';
import { mockReview, NOTE, reviewServer } from '../fixtures/review';

test('私人构建保留私人深链', async ({ page }) => {
  await mockMarket(page, { empty: true });
  await mockReview(page, reviewServer());

  await page.goto(`/?view=review&note=${NOTE}#fragment`);

  await expect(page.getByRole('heading', { name: '我的复盘' })).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`view=review.*note=${NOTE}#fragment$`));
});

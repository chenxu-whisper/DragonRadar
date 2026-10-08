import { expect, test, type Download, type Page } from '@playwright/test';

import {
  ID,
  mockLeaders,
  mockMarket,
  resetSummary,
  sessions,
  snapshot,
} from '../fixtures/market';
import { mockNews, NEWS_SNAPSHOT } from '../fixtures/news';
import { mockRegulation } from '../fixtures/regulation';

async function downloadText(download: Download): Promise<string> {
  const stream = await download.createReadStream();
  const chunks: Buffer[] = [];
  for await (const chunk of stream) chunks.push(Buffer.from(chunk));
  return Buffer.concat(chunks).toString('utf-8');
}

async function expectNoPageOverflow(page: Page) {
  expect(await page.evaluate(() => ({
    width: document.documentElement.clientWidth,
    scroll: document.documentElement.scrollWidth,
  }))).toEqual(expect.objectContaining({
    width: expect.any(Number),
    scroll: expect.any(Number),
  }));
  expect(await page.evaluate(
    () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
  )).toBe(true);
}

test('overview at 375px exports the current filter with immutable metadata', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await mockMarket(page);
  await page.goto(`/?view=overview&snapshot=${ID}&date=2026-09-10&market_filter=up&q=TEST001`);

  await expect(page.getByRole('heading', { name: '市场总览' })).toBeVisible();
  await expectNoPageOverflow(page);
  const pending = page.waitForEvent('download');
  await page.getByRole('button', { name: '导出当前筛选 CSV' }).click();
  const download = await pending;
  expect(download.suggestedFilename()).toMatch(/^dragon-radar-overview-current-filter-/);
  const csv = await downloadText(download);
  expect(csv).toContain('snapshot_id,trade_date,as_of,rule_version,filters,unit,currency,price_type,benchmark');
  expect(csv).toContain(ID);
  expect(csv).toContain('2026-09-10');
  expect(csv).toContain('测试规则一');
  expect(csv).toContain('market_filter=up');
  expect(csv).toContain('q=TEST001');
  expect(csv).toContain(',ratio,CNY,raw,');
  expect(csv).not.toContain('不上传服务器');
});

test('news at 1440px remains readable at 200% zoom and exports only the current page', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await mockNews(page);
  await page.goto(`/?view=news&snapshot=${NEWS_SNAPSHOT}&date=2026-09-10&news_type=announcement`);
  await expect(page.getByRole('heading', { name: '消息', exact: true })).toBeVisible();
  await page.evaluate(() => { document.documentElement.style.zoom = '2'; });
  await expectNoPageOverflow(page);
  await expect(page.getByRole('button', { name: '导出当前页 CSV' })).toBeVisible();
  const pending = page.waitForEvent('download');
  await page.getByRole('button', { name: '导出当前页 CSV' }).click();
  const csv = await downloadText(await pending);
  expect(csv).toContain(NEWS_SNAPSHOT);
  expect(csv).toContain('news_type=announcement');
  expect(csv).toContain('机器人公司签署合成测试订单');
  expect(csv).not.toContain('行业跟踪');
});

test('leaders supports keyboard, reduced motion, drawer Escape, focus restoration and export', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await mockLeaders(page);
  await page.goto(`/?view=leaders&snapshot=${ID}&date=2026-09-10&sector=SEC001`);

  await page.keyboard.press('Tab');
  await expect(page.getByRole('link', { name: '跳至研究内容' })).toBeFocused();
  expect(await page.evaluate(() => matchMedia('(prefers-reduced-motion: reduce)').matches)).toBe(true);
  expect(await page.locator('button').first().evaluate(
    element => getComputedStyle(element).transitionDuration,
  )).toBe('0s');
  const trigger = page.getByRole('button', { name: '合成测试证券1', exact: true }).first();
  await trigger.focus();
  await trigger.press('Enter');
  await expect(page.getByRole('dialog')).toBeVisible();
  await expect(page.getByRole('button', { name: '关闭详情' })).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(trigger).toBeFocused();
  await expectNoPageOverflow(page);

  const pending = page.waitForEvent('download');
  await page.getByRole('button', { name: '导出当前筛选 CSV' }).click();
  const csv = await downloadText(await pending);
  expect(csv).toContain('role=trend_leader');
  expect(csv).toContain('sector=SEC001');
  expect(csv).toContain('role-v1');
});

test('analysis exports the rendered current page and declared benchmark/basis', async ({ page }) => {
  await mockMarket(page);
  await page.route('**/api/rank/range?**', route => route.fulfill({
    json: {
      ...resetSummary(snapshot),
      coverage: { expected: 1, valid: 1, excluded: 0, missing: 0 },
      rule_version: 'analysis-v1',
      basis: { unit: 'ratio', currency: 'CNY', price_type: 'raw', benchmark: 'B000300' },
      data: {
        window_days: 20,
        adjusted_start: sessions[0],
        adjusted_end: sessions.at(-1),
        benchmark_return: 0.02,
        ranking: [{
          rank: 1,
          security_id: 'TEST001',
          name: '合成测试证券1',
          topics: [{ topic_id: 'SEC001', name: '机器人' }],
          topic_status: 'available',
          topic_reasons: [],
          reasons: [],
          return_ratio: 0.1,
          excess_return_ratio: 0.08,
          max_drawdown_ratio: 0.02,
          amount_change_ratio: 0.03,
        }],
        exclusions: [],
      },
    },
  }));
  await page.goto(`/?view=analysis&snapshot=${ID}&date=2026-09-10&q=TEST001&page=1&page_size=20`);
  await expect(page.getByRole('table', { name: '涨幅排行' })).toBeVisible();
  const pending = page.waitForEvent('download');
  await page.getByRole('button', { name: '导出当前页 CSV' }).click();
  const csv = await downloadText(await pending);
  expect(csv).toContain('analysis-v1');
  expect(csv).toContain('page=1;page_size=20');
  expect(csv).toContain(',ratio,CNY,raw,B000300,');
  expect(csv).toContain('TEST001');
});

test('stock detail exports its current raw date window without page overflow', async ({ page }) => {
  await mockMarket(page);
  await page.route('**/api/stock/TEST001/kline?**', route => route.fulfill({
    json: {
      ...resetSummary(snapshot),
      basis: { unit: 'currency/share', currency: 'CNY', price_type: 'raw', benchmark: null },
      data: {
        security_id: 'TEST001',
        adjustment: 'raw',
        adjusted_start: sessions[0],
        adjusted_end: sessions.at(-1),
        chart_type: 'candlestick',
        points: sessions.map((trade_date, index) => ({
          trade_date,
          open: 10 + index,
          high: 12 + index,
          low: 9 + index,
          close: 11 + index,
          volume: 100,
          amount: 1000,
        })),
      },
    },
  }));
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto(`/stock/TEST001?snapshot=${ID}&date=2026-09-10`);
  await expect(page.getByRole('heading', { name: '证券详情' })).toBeVisible();
  await expectNoPageOverflow(page);
  const pending = page.waitForEvent('download');
  await page.getByRole('button', { name: '导出当前筛选 CSV' }).click();
  const csv = await downloadText(await pending);
  expect(csv).toContain('security_id=TEST001');
  expect(csv).toContain('adjustment=raw');
  expect(csv).toContain(',currency/share,CNY,raw,');
});

test('regulation exports rendered calculations and events with row rule versions', async ({ page }) => {
  await mockRegulation(page);
  await page.goto(`/?view=regulation&snapshot=${ID}&date=2026-09-10`);
  await expect(page.getByRole('heading', { name: '监管' })).toBeVisible();
  const pending = page.waitForEvent('download');
  await page.getByRole('button', { name: '导出当前筛选 CSV' }).click();
  const csv = await downloadText(await pending);
  expect(csv).toContain(ID);
  expect(csv).toContain('synthetic-v1');
  expect(csv).toContain('calculation');
  expect(csv).toContain('event');
  expect(csv).toContain(',ratio,CNY,raw,');
});

test('local review preserves conflicting draft and exposes explicit JSON backup only', async ({ page, context }) => {
  await mockMarket(page);
  await page.goto(`/?view=review&snapshot=${ID}&date=2026-09-10`);
  await page.getByLabel('标题', { exact: true }).fill('验收笔记');
  await page.getByRole('button', { name: '保存笔记', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: '回读验证通过' })).toBeVisible();
  await expect(page.getByRole('button', { name: /CSV/ })).toHaveCount(0);

  const second = await context.newPage();
  await mockMarket(second);
  await second.goto(`/?view=review&snapshot=${ID}&date=2026-09-10`);
  await second.getByRole('button', { name: '验收笔记', exact: true }).click();
  await second.getByRole('textbox', { name: '正文', exact: true }).fill('冲突时必须保留');
  await page.getByRole('textbox', { name: '正文', exact: true }).fill('另一标签已保存');
  await page.getByRole('button', { name: '保存笔记', exact: true }).click();
  await second.getByRole('button', { name: '保存笔记', exact: true }).click();
  await expect(second.getByRole('alert').filter({ hasText: '版本冲突' })).toBeVisible();
  await expect(second.getByRole('textbox', { name: '正文', exact: true })).toHaveValue('冲突时必须保留');

  const pending = second.waitForEvent('download');
  await second.getByRole('button', { name: '导出本机备份', exact: true }).click();
  const download = await pending;
  expect(download.suggestedFilename()).toMatch(/\.json$/);
  const backup = JSON.parse(await downloadText(download));
  expect(backup.notes).toHaveLength(1);
  expect(JSON.stringify(backup)).toContain('验收笔记');
});

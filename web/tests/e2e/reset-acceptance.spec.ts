import { expect, test, type Download, type Locator, type Page } from '@playwright/test';

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
  expect(await page.evaluate(() => {
    const root = document.documentElement;
    const rootBounds = root.getBoundingClientRect();
    const containedByScrollWrapper = (element: HTMLElement) => {
      const wrapper = element.closest<HTMLElement>('.table-scroll, .index-strip');
      if (!wrapper || wrapper === element || getComputedStyle(element).position === 'fixed') {
        return false;
      }
      const bounds = wrapper.getBoundingClientRect();
      const overflow = getComputedStyle(wrapper).overflowX;
      return ['auto', 'scroll'].includes(overflow)
        && wrapper.scrollWidth > wrapper.clientWidth + 1
        && bounds.left >= rootBounds.left - 1
        && bounds.right <= rootBounds.right + 1;
    };
    const visuallyClipped = (element: HTMLElement) => {
      let ancestor = element.parentElement;
      while (ancestor && ancestor !== document.body) {
        const style = getComputedStyle(ancestor);
        if (
          style.overflow === 'hidden'
          && (style.clip !== 'auto' || style.clipPath !== 'none')
        ) {
          return true;
        }
        ancestor = ancestor.parentElement;
      }
      return false;
    };
    const elements = [...document.querySelectorAll<HTMLElement>('body *')];
    return {
      width: root.clientWidth,
      scroll: root.scrollWidth,
      offenders: elements
        .filter(element => (
          (
            element.getBoundingClientRect().left < rootBounds.left - 1
            || element.getBoundingClientRect().right > rootBounds.right + 1
          )
          && !containedByScrollWrapper(element)
          && !visuallyClipped(element)
        ))
        .sort((left, right) => right.getBoundingClientRect().right - left.getBoundingClientRect().right)
        .slice(0, 8)
        .map(element => ({
          className: element.className,
          parentClassName: element.parentElement?.className,
          tag: element.tagName,
          text: element.textContent?.slice(0, 40),
          left: element.getBoundingClientRect().left,
          right: element.getBoundingClientRect().right,
          scrollWidth: element.scrollWidth,
        })),
    };
  })).toEqual({
    width: expect.any(Number),
    scroll: expect.any(Number),
    offenders: [],
  });
  expect(await page.evaluate(() => document.documentElement.scrollWidth))
    .toBe(await page.evaluate(() => document.documentElement.clientWidth));
}

async function expectUnobscured(locator: Locator) {
  await expect(locator).toBeVisible();
  await locator.scrollIntoViewIfNeeded();
  expect(await locator.evaluate(element => {
    const bounds = element.getBoundingClientRect();
    const x = bounds.left + bounds.width / 2;
    const y = bounds.top + Math.min(bounds.height / 2, innerHeight - bounds.top - 1);
    const hit = document.elementFromPoint(x, y);
    return {
      bounds: { left: bounds.left, top: bounds.top, width: bounds.width, height: bounds.height },
      hit: hit?.tagName ?? null,
      hitText: hit?.textContent?.slice(0, 40) ?? null,
      unobscured: Boolean(hit && (hit === element || element.contains(hit) || hit.contains(element))),
      viewport: { width: innerWidth, height: innerHeight },
    };
  })).toEqual(expect.objectContaining({ unobscured: true }));
}

async function activate(locator: Locator, key = 'Enter') {
  await locator.focus();
  await expect(locator).toBeFocused();
  await locator.press(key);
}

type AcceptanceView =
  | 'overview'
  | 'news'
  | 'leaders'
  | 'analysis'
  | 'stock'
  | 'regulation'
  | 'review';

const responsiveViews: {
  view: AcceptanceView;
  heading: string;
  url: string;
  meaningful: (page: Page) => ReturnType<Page['locator']>;
  interact: (page: Page) => Promise<Locator>;
}[] = [
  {
    view: 'overview',
    heading: '市场总览',
    url: `/?view=overview&snapshot=${ID}&date=2026-09-10`,
    meaningful: page => page.getByRole('region', { name: '证券筛选结果' }),
    interact: async page => {
      const button = page.getByRole('button', { name: /^上涨 / });
      await activate(button);
      await expect(button).toHaveAttribute('aria-pressed', 'true');
      return page.getByRole('region', { name: '证券筛选结果' });
    },
  },
  {
    view: 'news',
    heading: '消息',
    url: `/?view=news&snapshot=${NEWS_SNAPSHOT}&date=2026-09-10`,
    meaningful: page => page.getByRole('button', { name: '导出当前页 CSV' }),
    interact: async page => {
      const article = page.getByRole('region', { name: '消息列表' })
        .getByRole('button', { name: /机器人公司签署/ });
      await activate(article);
      return page.getByRole('region', { name: '消息详情' })
        .getByRole('heading', { name: '机器人公司签署合成测试订单' });
    },
  },
  {
    view: 'leaders',
    heading: '梯队',
    url: `/?view=leaders&snapshot=${ID}&date=2026-09-10`,
    meaningful: page => page.getByRole('combobox', { name: '角色筛选' }),
    interact: async page => {
      const security = page.getByRole('table', { name: '角色候选' })
        .getByRole('button', { name: '合成测试证券1', exact: true });
      await activate(security);
      return page.getByRole('dialog').getByRole('heading', { name: '合成测试证券1' });
    },
  },
  {
    view: 'analysis',
    heading: '涨幅分析',
    url: `/?view=analysis&snapshot=${ID}&date=2026-09-10`,
    meaningful: page => page.getByRole('table', { name: '涨幅排行' }),
    interact: async page => {
      const checkbox = page.getByRole('checkbox', { name: '比较 TEST001' });
      await activate(checkbox, 'Space');
      await expect(checkbox).toBeChecked();
      return page.getByRole('status').filter({ hasText: '已选 1 / 5 个标的' });
    },
  },
  {
    view: 'stock',
    heading: '证券详情',
    url: `/stock/TEST001?snapshot=${ID}&date=2026-09-10`,
    meaningful: page => page.getByText('TEST001', { exact: true }).first(),
    interact: async page => {
      const pending = page.waitForEvent('download');
      const button = page.getByRole('button', { name: '导出当前筛选 CSV' });
      await activate(button);
      expect(await downloadText(await pending)).toContain('security_id=TEST001');
      return button;
    },
  },
  {
    view: 'regulation',
    heading: '监管',
    url: `/?view=regulation&snapshot=${ID}&date=2026-09-10`,
    meaningful: page => page.getByRole('region', { name: '数据完整性' }),
    interact: async page => {
      const pending = page.waitForEvent('download');
      const button = page.getByRole('button', { name: '导出当前筛选 CSV' });
      await activate(button);
      expect(await downloadText(await pending)).toContain('synthetic-v1');
      return button;
    },
  },
  {
    view: 'review',
    heading: '我的复盘',
    url: `/?view=review&snapshot=${ID}&date=2026-09-10`,
    meaningful: page => page.getByRole('region', { name: '备份与恢复' }),
    interact: async page => {
      const title = page.getByLabel('标题', { exact: true });
      await title.focus();
      await expect(title).toBeFocused();
      await page.keyboard.type('缩放验收');
      const save = page.getByRole('button', { name: '保存笔记', exact: true });
      await activate(save);
      return page.getByRole('status').filter({ hasText: '回读验证通过' });
    },
  },
];

async function mockResponsiveView(page: Page, view: AcceptanceView) {
  if (view === 'news') {
    await mockNews(page);
    return;
  }
  if (view === 'leaders') {
    await mockLeaders(page);
    return;
  }
  if (view === 'regulation') {
    await mockRegulation(page);
    return;
  }
  await mockMarket(page);
  if (view === 'analysis') {
    await page.route('**/api/rank/range?**', route => route.fulfill({
      json: {
        ...resetSummary(snapshot),
        coverage: { expected: 1, valid: 1, excluded: 0, missing: 0 },
        rule_version: 'analysis-v1',
        basis: {
          unit: 'ratio',
          currency: 'CNY',
          price_type: 'raw',
          benchmark: 'B000300',
        },
        data: {
          window_days: 20,
          adjusted_start: sessions[0],
          adjusted_end: sessions.at(-1),
          benchmark_return: 0.02,
          ranking: [{
            rank: 1,
            security_id: 'TEST001',
            name: '合成测试证券1',
            topics: [],
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
  }
}

for (const item of responsiveViews) {
  for (const width of [375, 1440]) {
    test(`${item.view} retains its responsive layout at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: width === 375 ? 812 : 1000 });
      await mockResponsiveView(page, item.view);
      await page.goto(item.url);

      const heading = page.getByRole('heading', { name: item.heading, exact: true });
      await expect(heading).toBeVisible();
      await expect(item.meaningful(page)).toBeVisible();
      await expectNoPageOverflow(page);
    });
  }
}

for (const item of responsiveViews) {
  for (const physicalWidth of [375, 1440]) {
    test(`${item.view} works at layout-equivalent 200% and ${physicalWidth}px`, async ({ page }) => {
      const physicalHeight = physicalWidth === 375 ? 812 : 1000;
      const cssWidth = Math.ceil(physicalWidth / 2);
      const cssHeight = Math.ceil(physicalHeight / 2);
      const session = await page.context().newCDPSession(page);
      await session.send('Emulation.setDeviceMetricsOverride', {
        width: cssWidth,
        height: cssHeight,
        deviceScaleFactor: 2,
        mobile: false,
      });
      await mockResponsiveView(page, item.view);
      await page.goto(item.url);

      expect(await page.evaluate(() => ({
        width: window.innerWidth,
        height: window.innerHeight,
        dpr: window.devicePixelRatio,
      }))).toEqual({ width: cssWidth, height: cssHeight, dpr: 2 });
      const heading = page.getByRole('heading', { name: item.heading, exact: true });
      await expectUnobscured(heading);
      const result = await item.interact(page);
      await expectUnobscured(result);
      await expectNoPageOverflow(page);
      await session.detach();
    });
  }
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
  await expect(page.getByText(/^未复权价格 · 实际区间/)).toBeVisible();
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

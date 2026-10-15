import { expect, test, type Page, type Route } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

import { ID, mockMarket, resetSummary, snapshot, sessions } from '../fixtures/market';

const DATE = '2026-09-10';
const SECURITY = 'TEST001';
const AS_OF = '2026-09-10T08:01:00Z';
const EARLY_AS_OF = '2026-09-10T07:00:00Z';
const SOURCE = 'demo-feed';
const SEAT_NAME = '同名席位演示';
const REASON = '日价格涨幅偏离值达到 7%';

type LhbRequest = { method: string; pathname: string; asOf: string | null };

function lhbEnvelope(data: unknown, changes: Record<string, unknown> = {}) {
  return {
    date: DATE,
    as_of: AS_OF,
    source: SOURCE,
    source_version: 'v1',
    quality_flags: [],
    basis: { unit: '元', currency: 'CNY' },
    coverage: {
      total_disclosures: 1,
      visible_disclosures: 1,
      late_disclosures: 0,
      selected_disclosures: 1,
      seat_count: 3,
      complete_seats: 1,
      unknown_seats: 2,
    },
    data,
    ...changes,
  };
}

function disclosureBody(asOf: string | null) {
  return lhbEnvelope({
    items: [{
      disclosure_id: 'disc-1',
      security_id: SECURITY,
      disclosure_date: DATE,
      reason: REASON,
      revision: 1,
      source_id: SOURCE,
      source_version: 'v1',
      source_event_at: '2026-09-10T07:10:00Z',
      received_at: '2026-09-10T07:20:00Z',
      available_at: '2026-09-10T07:45:00Z',
      seats: [
        {
          source_row_id: 'row-A',
          seat_id: 'SEAT-A',
          source_seat_name: SEAT_NAME,
          mapping_version: 'map-v1',
          side: 'buy',
          rank: 1,
          buy_amount: 100,
          sell_amount: 40,
          currency: 'CNY',
          unit: '元',
          completeness: 'complete',
          net_amount: 60,
        },
        {
          source_row_id: 'row-B',
          seat_id: 'SEAT-B',
          source_seat_name: SEAT_NAME,
          mapping_version: 'map-v1',
          side: null,
          rank: null,
          buy_amount: null,
          sell_amount: 0,
          currency: 'CNY',
          unit: '元',
          completeness: 'buy_unknown',
          net_amount: null,
        },
        {
          source_row_id: 'row-C',
          seat_id: 'SEAT-C',
          source_seat_name: SEAT_NAME,
          mapping_version: 'map-v1',
          side: 'sell',
          rank: 2,
          buy_amount: 0,
          sell_amount: null,
          currency: 'CNY',
          unit: '元',
          completeness: 'sell_unknown',
          net_amount: null,
        },
      ],
    }],
    total: 1,
    page: 1,
    size: 20,
  }, { as_of: asOf ?? AS_OF });
}

function emptyWindow(asOf: string | null) {
  return lhbEnvelope({
    items: [],
    total: 0,
    page: 1,
    size: 20,
  }, {
    as_of: asOf ?? EARLY_AS_OF,
    source: null,
    source_version: null,
    quality_flags: ['no_data'],
    basis: { unit: null, currency: null },
    coverage: {
      total_disclosures: 0,
      visible_disclosures: 0,
      late_disclosures: 0,
      selected_disclosures: 0,
      seat_count: 0,
      complete_seats: 0,
      unknown_seats: 0,
    },
  });
}

function knownHistory(asOf: string | null) {
  return lhbEnvelope({
    seat: {
      source_id: SOURCE,
      seat_id: 'SEAT-A',
      source_seat_name: SEAT_NAME,
      mapping_version: 'map-v1',
    },
    identity_state: 'known',
    items: [{
      seat_id: 'SEAT-A',
      source_id: SOURCE,
      source_version: 'v1',
      mapping_version: 'map-v1',
      source_seat_name: SEAT_NAME,
      currency: 'CNY',
      unit: '元',
      buy_amount: 100,
      sell_amount: 80,
      net_amount: 20,
      completeness: 'complete',
      contributions: [
        {
          disclosure_id: 'disc-2',
          security_id: SECURITY,
          revision: 1,
          disclosure_date: '2026-09-09',
          reason: REASON,
          source_row_id: 'row-A0',
          source_seat_name: SEAT_NAME,
          mapping_version: 'map-v1',
          permission_state: 'authorized',
        },
        {
          disclosure_id: 'disc-1',
          security_id: SECURITY,
          revision: 2,
          disclosure_date: DATE,
          reason: REASON,
          source_row_id: 'row-A1',
          source_seat_name: SEAT_NAME,
          mapping_version: 'map-v1',
          permission_state: 'revoked',
        },
      ],
    }],
    total: 1,
  }, {
    as_of: asOf ?? AS_OF,
    coverage: {
      total_disclosures: 2,
      visible_disclosures: 2,
      late_disclosures: 0,
      selected_disclosures: 2,
      seat_count: 1,
      complete_seats: 1,
      unknown_seats: 0,
    },
  });
}

function unknownHistory(asOf: string | null) {
  return lhbEnvelope({
    seat: {
      source_id: 'SEAT-B',
      seat_id: 'SEAT-B',
      source_seat_name: 'SEAT-B',
      mapping_version: 'unknown',
    },
    identity_state: 'unknown',
    items: [],
    total: 0,
  }, {
    as_of: asOf ?? AS_OF,
    source: null,
    source_version: null,
    quality_flags: ['no_data'],
    basis: { unit: null, currency: null },
    coverage: {
      total_disclosures: 0,
      visible_disclosures: 0,
      late_disclosures: 0,
      selected_disclosures: 0,
      seat_count: 0,
      complete_seats: 0,
      unknown_seats: 0,
    },
  });
}

async function mockLhb(page: Page, options: { earlyAsOf?: boolean } = {}): Promise<LhbRequest[]> {
  const requests: LhbRequest[] = [];
  await mockMarket(page);

  // Registered after mockMarket so they win (Playwright matches the last
  // registered route first): mockMarket treats the kline path's final segment
  // as a security id (404) and has no /api/news route, both of which the
  // security detail page requests unconditionally.
  await page.route(/\/api\/stock\/[^/?#]+\/kline(?:[?#]|$)/, route => route.fulfill({
    json: {
      ...resetSummary(snapshot),
      data: {
        security_id: SECURITY,
        adjustment: 'raw',
        adjusted_start: sessions[0],
        adjusted_end: sessions[sessions.length - 1],
        chart_type: 'line',
        points: [],
      },
    },
  }));
  await page.route(/\/api\/news(?:[?#]|$)/, route => route.fulfill({
    json: {
      ...resetSummary(snapshot),
      data: { articles: [], page: 1, size: 20, total: 0, sources: [], event_count: 0 },
    },
  }));

  if (options.earlyAsOf) {
    // Registered after mockMarket so it wins; the snapshot publication time
    // becomes the LHB as_of cursor, so an early snapshot must hide disclosures
    // whose available_at is later.
    const base = resetSummary(snapshot);
    const early = { ...base, data: { ...base.data, published_at: EARLY_AS_OF } };
    await page.route('**/api/snapshots/**', async route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/api/snapshots/list') {
        return route.fulfill({ json: { ...early, data: { snapshots: [early] } } });
      }
      if (url.pathname === `/api/snapshots/${ID}`) {
        return route.fulfill({ json: early });
      }
      return route.fulfill({ status: 404, json: {} });
    });
  }

  const record = (route: Route): LhbRequest => {
    const request = route.request();
    const url = new URL(request.url());
    const entry = {
      method: request.method(),
      pathname: url.pathname,
      asOf: url.searchParams.get('as_of'),
    };
    requests.push(entry);
    return entry;
  };

  await page.route('**/api/lhb**', async route => {
    const entry = record(route);
    if (entry.method !== 'GET') {
      return route.fulfill({ status: 405, json: { code: 'METHOD_NOT_ALLOWED' } });
    }
    if (options.earlyAsOf || (entry.asOf && entry.asOf <= EARLY_AS_OF)) {
      return route.fulfill({ json: emptyWindow(entry.asOf) });
    }
    return route.fulfill({ json: disclosureBody(entry.asOf) });
  });

  // Registered after the list route so the more specific seats pattern wins.
  await page.route('**/api/lhb/seats/**', async route => {
    const entry = record(route);
    if (entry.method !== 'GET') {
      return route.fulfill({ status: 405, json: { code: 'METHOD_NOT_ALLOWED' } });
    }
    const url = new URL(route.request().url());
    const seatId = decodeURIComponent(url.pathname.split('/').pop() ?? '');
    if (seatId === 'SEAT-A') return route.fulfill({ json: knownHistory(entry.asOf) });
    if (seatId === 'SEAT-B') return route.fulfill({ json: unknownHistory(entry.asOf) });
    return route.fulfill({ status: 404, json: { code: 'NOT_FOUND' } });
  });

  return requests;
}

async function openStock(page: Page) {
  await page.goto(
    `/?view=stock&snapshot=${ID}&date=${DATE}&security=${SECURITY}`,
  );
  return page.getByRole('region', { name: '龙虎榜席位证据' });
}

test.beforeEach(async ({ page }) => {
  await mockMarket(page);
});

test('renders same-name seats distinctly with null vs zero and a synthetic badge', async ({ page }) => {
  const requests = await mockLhb(page);
  const panel = await openStock(page);
  await expect(panel).toBeVisible();

  await expect(panel.getByText('合成演示数据 · 未获真实行情授权')).toBeVisible();
  await expect(panel.getByText('部分未知')).toBeVisible();
  await expect(panel.getByText('席位 3（完整 1 · 未知 2）')).toBeVisible();
  await expect(panel.locator('.pager')).toContainText('共 1 条 · 第 1 / 1 页');

  const table = panel.getByRole('table', { name: '披露 disc-1 席位记录' });
  const names = table.locator('strong', { hasText: SEAT_NAME });
  await expect(names).toHaveCount(3);
  for (const seatId of ['SEAT-A', 'SEAT-B', 'SEAT-C']) {
    await expect(panel.getByRole('button', {
      name: `查看席位 ${seatId} 近 30 日历史`,
    })).toBeVisible();
  }
  await expect(table.getByRole('cell', { name: '未知', exact: true })).toHaveCount(6);
  await expect(table.getByRole('cell', { name: '0', exact: true })).toHaveCount(2);
  await expect(table.getByRole('cell', { name: '100', exact: true })).toHaveCount(1);
  await expect(table.getByRole('cell', { name: '60', exact: true })).toHaveCount(1);

  const body = page.locator('body');
  await expect(body).not.toContainText('已授权');
  await expect(body).not.toContainText('持牌');
  expect(requests.filter(item => item.pathname.startsWith('/api/lhb')).length).toBeGreaterThan(0);
  expect(requests.every(item => item.method === 'GET')).toBe(true);
});

test('opens seat history with the keyboard and restores focus on escape', async ({ page }) => {
  await mockLhb(page);
  const panel = await openStock(page);

  const trigger = panel.getByRole('button', { name: '查看席位 SEAT-A 近 30 日历史' });
  await trigger.focus();
  await expect(trigger).toBeFocused();
  await page.keyboard.press('Enter');

  const dialog = page.getByRole('dialog', { name: '席位 SEAT-A 近 30 日上榜历史' });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText('demo-feed').first()).toBeVisible();
  await expect(dialog.getByText('authorized')).toBeVisible();
  await expect(dialog.getByText('revoked')).toBeVisible();
  await expect(dialog.getByText('净额 20 CNY / 元')).toBeVisible();
  await expect(dialog.getByText('2026-09-09')).toBeVisible();
  await expect(dialog.getByText('合成演示数据 · 未获真实行情授权')).toBeVisible();

  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  await expect(trigger).toBeFocused();
});

test('shows the unknown-identity placeholder and a 404 as an empty history, not an error', async ({ page }) => {
  await mockLhb(page);
  const panel = await openStock(page);

  const triggerB = panel.getByRole('button', { name: '查看席位 SEAT-B 近 30 日历史' });
  await triggerB.click();
  const dialogB = page.getByRole('dialog', { name: '席位 SEAT-B 近 30 日上榜历史' });
  await expect(dialogB).toBeVisible();
  await expect(dialogB.getByText('未映射席位（占位身份，非真实来源）。')).toBeVisible();
  await expect(dialogB.getByRole('alert')).toHaveCount(0);
  await dialogB.getByRole('button', { name: '关闭席位历史' }).click();
  await expect(dialogB).toHaveCount(0);
  await expect(triggerB).toBeFocused();

  const triggerC = panel.getByRole('button', { name: '查看席位 SEAT-C 近 30 日历史' });
  await triggerC.click();
  const dialogC = page.getByRole('dialog', { name: '席位 SEAT-C 近 30 日上榜历史' });
  await expect(dialogC.getByText('该席位在近 30 日内没有可显示的上榜历史。')).toBeVisible();
  await expect(dialogC.getByRole('alert')).toHaveCount(0);
});

test('hides the late disclosure when as_of precedes its available time', async ({ page }) => {
  const requests = await mockLhb(page, { earlyAsOf: true });
  const panel = await openStock(page);

  await expect(panel).toBeVisible();
  await expect(panel.getByText(
    '当前研究时点没有可显示的龙虎榜披露；不会用其他日期或来源的数据补齐。',
  )).toBeVisible();
  await expect(panel.getByText(REASON)).toHaveCount(0);
  const listRequests = requests.filter(item => item.pathname === '/api/lhb');
  expect(listRequests.length).toBeGreaterThan(0);
  expect(listRequests.every(item => item.asOf === EARLY_AS_OF)).toBe(true);
});

for (const target of [
  { name: '375px', width: 375, height: 900 },
  { name: '1440px', width: 1440, height: 1000 },
  { name: '200% equivalent', width: 720, height: 900 },
]) {
  test(`has no page or panel overflow at ${target.name}`, async ({ page }) => {
    const consoleErrors: string[] = [];
    page.on('console', message => {
      if (message.type() === 'error') consoleErrors.push(message.text());
    });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.setViewportSize({ width: target.width, height: target.height });
    await mockLhb(page);
    const panel = await openStock(page);
    await expect(panel).toBeVisible();
    await expect(panel.getByText('席位 3（完整 1 · 未知 2）')).toBeVisible();

    const metrics = await page.evaluate(() => {
      const node = document.querySelector('[aria-label="龙虎榜席位证据"]');
      const panelNode = node as HTMLElement | null;
      const tables = [...document.querySelectorAll('[aria-label="龙虎榜席位证据"] table')];
      const rects = tables.map(table => table.getBoundingClientRect());
      return {
        bodyOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
        panelOverflow: panelNode ? panelNode.scrollWidth > panelNode.clientWidth : true,
        tableOverlap: rects.some((rect, index) =>
          rects.slice(index + 1).some(other =>
            rect.top < other.bottom && rect.bottom > other.top
            && rect.left < other.right && rect.right > other.left)),
      };
    });
    expect(metrics).toEqual({
      bodyOverflow: false,
      panelOverflow: false,
      tableOverlap: false,
    });
    expect(await new AxeBuilder({ page }).include('[aria-label="龙虎榜席位证据"]').analyze())
      .toMatchObject({ violations: [] });
    expect(consoleErrors).toEqual([]);
  });
}

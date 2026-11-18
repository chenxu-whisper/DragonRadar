import { expect, test, type Page } from '@playwright/test';
import { ID, mockLeaders, mockMarket, resetSummary } from '../fixtures/market';
import { mockRegulation } from '../fixtures/regulation';

const DATE = '2026-09-10';
const stockUrl = `/?view=stock&snapshot=${ID}&date=${DATE}&security=TEST001`;

async function detailExtras(page: Page) {
  await page.route('**/api/news?**', route => route.fulfill({ json: {
    ...resetSummary(),
    data: { articles: [], page: 1, size: 20, total: 0, sources: [], event_count: 0 },
  } }));
  await page.route('**/api/lhb?**', route => route.fulfill({ json: {
    ...resetSummary(), data: null, quality_flags: ['materialization_unavailable'],
  } }));
}

async function stockRoles(page: Page, sectors: string[]) {
  await page.route(/\/api\/stock\/TEST001\?/, route => route.fulfill({ json: {
    ...resetSummary(),
    data: {
      security_id: 'TEST001', name: '合成测试证券1',
      quote: { last: 10, change_ratio: 0.02, amount: 12000 },
      roles: sectors.map(sector_id => ({ sector_id, role_type: 'trend_leader', evidence_status: 'candidate' })),
      seal: {
        is_limit_up: null, touched_limit: null, first_seal_time: null, last_seal_time: null,
        break_count: null, seal_amount: null, seal_amount_unit: null, continuous_boards: null,
        reason: 'intraday_seal_fields_unavailable',
      },
      regulation: { formal_event_count: 0, event_source_completeness: 'complete', reason: null },
    },
  } }));
}

test('a stock opened without sector chooses an actual topic for all six evidence groups', async ({ page }) => {
  await mockLeaders(page);
  await detailExtras(page);
  const reads: URL[] = [];
  page.on('request', request => {
    if (new URL(request.url()).pathname === '/api/role/evidence') reads.push(new URL(request.url()));
  });
  await page.goto(stockUrl);
  const selector = page.getByRole('combobox', { name: '角色证据题材', exact: true });
  await expect(selector).toBeVisible();
  await selector.selectOption('SEC001');
  await expect(page.getByRole('heading', { name: '六类角色证据', exact: true })).toBeVisible();
  await expect(page.getByRole('rowheader', { name: 'FAC-R01 20日相对强度', exact: true })).toBeVisible();
  expect(reads).toHaveLength(1);
  expect(reads[0].searchParams.get('snapshot_id')).toBe(ID);
  expect(reads[0].searchParams.get('date')).toBe(DATE);
  expect(reads[0].searchParams.get('security_id')).toBe('TEST001');
  expect(reads[0].searchParams.get('sector')).toBe('SEC001');
});

test('an unrelated entry sector is disclosed and never becomes evidence context', async ({ page }) => {
  await mockLeaders(page);
  await detailExtras(page);
  const sectors: (string | null)[] = [];
  page.on('request', request => {
    const url = new URL(request.url());
    if (url.pathname === '/api/role/evidence') sectors.push(url.searchParams.get('sector'));
  });
  await page.goto(`${stockUrl}&sector=UNKNOWN-SECTOR`);
  await expect(page.getByText('当前入口题材不属于该证券的角色证据上下文。', { exact: true })).toBeVisible();
  expect(sectors).toEqual([]);
  await page.getByRole('combobox', { name: '角色证据题材', exact: true }).selectOption('SEC001');
  await expect(page.getByRole('heading', { name: '六类角色证据', exact: true })).toBeVisible();
  expect(sectors).toEqual(['SEC001']);
});

test('a stock with no archived role topic keeps evidence unavailable', async ({ page }) => {
  await mockLeaders(page);
  await detailExtras(page);
  await stockRoles(page, []);
  const evidenceReads: string[] = [];
  page.on('request', request => {
    if (new URL(request.url()).pathname === '/api/role/evidence') evidenceReads.push(request.url());
  });
  await page.goto(stockUrl);
  await expect(page.getByText('当前发布未归档该证券的角色题材，角色证据不可用。', { exact: true })).toBeVisible();
  expect(evidenceReads).toEqual([]);
});

test('formal regulation drill-down reads actual same-publication events', async ({ page }) => {
  await mockRegulation(page);
  await detailExtras(page);
  const reads: URL[] = [];
  page.on('request', request => {
    const url = new URL(request.url());
    if (url.pathname === '/api/regulation/events') reads.push(url);
  });
  await page.goto(stockUrl);
  await page.getByRole('button', { name: '查看正式监管事件', exact: true }).click();
  const events = page.getByRole('region', { name: '该股正式监管事件', exact: true });
  await expect(events).toBeVisible();
  await expect(events.getByRole('link', { name: '公告原文', exact: true })).toHaveCount(4);
  await expect(events).toContainText('TEST001');
  expect(reads).toHaveLength(1);
  expect(reads[0].searchParams.get('snapshot_id')).toBe(ID);
  expect(reads[0].searchParams.get('date')).toBe(DATE);
  await expect(page.locator('dialog[open]')).toHaveCount(0);
});

test('unknown formal source keeps listed events and does not claim zero or completeness', async ({ page }) => {
  await mockRegulation(page, { unknownEvents: true });
  await detailExtras(page);
  await page.goto(stockUrl);
  await page.getByRole('button', { name: '查看正式监管事件', exact: true }).click();
  const events = page.getByRole('region', { name: '该股正式监管事件', exact: true });
  await expect(events).toContainText('事件来源完整性未知');
  await expect(events.getByRole('link', { name: '公告原文', exact: true })).toHaveCount(4);
  await expect(events).not.toContainText('0 条正式记录');
});

test('repeat stock trigger closes the only drawer and restores origin focus', async ({ page }) => {
  await mockMarket(page);
  await detailExtras(page);
  await page.goto(`/?view=overview&snapshot=${ID}&date=${DATE}`);
  const trigger = page.getByRole('button', { name: '合成测试证券1', exact: true });
  await trigger.click();
  await expect(page.locator('dialog[open]')).toHaveCount(1);
  // The underlying list is inert while the native modal is open. Dispatching the
  // same trigger verifies the PRD handler toggle; Escape below exercises real UI.
  await trigger.dispatchEvent('click');
  await expect(page.locator('dialog[open]')).toHaveCount(0);
  await expect(trigger).toBeFocused();
  await trigger.click();
  await expect(page.locator('dialog[open]')).toHaveCount(1);
  await page.keyboard.press('Escape');
  await expect(trigger).toBeFocused();
  await expect(page).toHaveURL(new RegExp(`snapshot=${ID}`));
});

test('expanded raw history uses bounded paging and bidirectional date sorting', async ({ page }) => {
  await mockLeaders(page);
  await detailExtras(page);
  const days: string[] = [];
  const day = new Date(`${DATE}T00:00:00Z`);
  while (days.length < 63) {
    if (![0, 6].includes(day.getUTCDay())) days.unshift(day.toISOString().slice(0, 10));
    day.setUTCDate(day.getUTCDate() - 1);
  }
  const points = days.map(trade_date => ({
    trade_date,
    open: 10, high: 12, low: 9, close: 11, volume: 100, amount: 1100,
  }));
  await page.route('**/api/stock/TEST001/kline?**', route => route.fulfill({ json: {
    ...resetSummary(), data: {
      security_id: 'TEST001', adjustment: 'raw', adjusted_start: points[0].trade_date,
      adjusted_end: points.at(-1)!.trade_date, chart_type: 'candlestick', points,
    },
  } }));
  await page.goto(`${stockUrl}&start=${points[0].trade_date}&end=${DATE}`);
  await page.getByText('查看完整行情数据（63 个交易日）', { exact: true }).click();
  const table = page.getByRole('table', { name: '完整行情数据', exact: true });
  await expect(table.locator('tbody tr')).toHaveCount(50);
  await page.getByRole('combobox', { name: /^行情每页条数/ }).selectOption('20');
  await expect(table.locator('tbody tr')).toHaveCount(20);
  await page.getByRole('button', { name: '行情下一页', exact: true }).click();
  await expect(table.locator('tbody tr').first()).toContainText(points[20].trade_date);
  await page.getByRole('button', { name: '交易日期排序', exact: true }).click();
  await expect(table.locator('tbody tr').first()).toContainText(points.at(-1)!.trade_date);
  await page.getByRole('combobox', { name: /^行情每页条数/ }).selectOption('100');
  await expect(table.locator('tbody tr')).toHaveCount(63);
});

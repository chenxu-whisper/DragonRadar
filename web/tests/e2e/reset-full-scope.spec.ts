import { expect, test, type Page } from '@playwright/test';

import { ID, mockMarket, resetSummary, snapshot } from '../fixtures/market';
import { mockRotation } from '../fixtures/rotation';
import { mockNews, NEWS_SNAPSHOT } from '../fixtures/news';

// Task 29 full-scope cross-domain acceptance. The per-domain specs already cover
// each slice deeply; this spec proves the slices stay *consistent together*: one
// pinned historical publication survives cross-view navigation, the realtime
// batch remains an independent stream, LHB/word-cloud/rotation/NL-query bind to
// the serving publication, local notes survive offline + reload, and the Task 27
// research surface is provably absent. Nothing here uses test.skip(): the Task 27
// scope is a passing *boundary assertion* (no /api/research request, no UI), so
// the reset verify gate's zero-skip contract still holds.

const DATE = '2026-09-10';
const SECURITY = 'TEST001';
const SECTOR = 'SEC001';
const BATCH = 'batch-1';

const QUERY_ID = '11111111-1111-4111-8111-111111111111';
const HASH = 'a'.repeat(64);

function liveEnvelope(data: unknown, changes: Record<string, unknown> = {}) {
  return {
    trade_date: DATE,
    as_of: '2026-09-10T02:00:00Z',
    // Realtime batches are deliberately NOT pinned to a daily publication.
    snapshot_id: null,
    source: 'licensed-feed',
    source_version: 'v1',
    coverage: { expected: 1, valid: 1, excluded: 0, missing: 0 },
    quality_flags: [],
    rule_version: null,
    basis: { unit: null, currency: null, price_type: 'raw', benchmark: null },
    data,
    ...changes,
  };
}

// Compose every read surface the reset workbench can reach while jumping across
// overview / 梯队 / 涨幅. Built on mockRotation (which pulls mockMarket +
// mockLeaders), then layering more-specific routes afterwards so Playwright's
// last-registered-first-match rule wins over the market catch-all.
async function mockCrossDomain(
  page: Page,
  realtimeOptions: { revokeFirstQuote?: boolean } = {},
): Promise<{ realtimeUrls: URL[]; flowUrls: URL[]; researchUrls: URL[]; aiUrls: URL[]; researchCalls: string[] }> {
  const realtimeUrls: URL[] = [];
  const flowUrls: URL[] = [];
  const researchUrls: URL[] = [];
  const aiUrls: URL[] = [];
  const researchCalls: string[] = [];

  await mockRotation(page);

  // ResetAnalysis rank projection: data:null degrades to "interval unavailable".
  await page.route(/\/api\/rank\//, route => route.fulfill({
    json: {
      ...resetSummary(snapshot),
      source: 'analysis_materialization',
      data: null,
    },
  }));

  // Overview-only Task 28 research section: null-data stub, never a 404.
  await page.route(/\/api\/market\/sentiment\/research(?:[?#]|$)/, route =>
    route.fulfill({ json: liveEnvelope(null, {
      snapshot_id: ID,
      quality_flags: ['materialization_unavailable'],
    }) }));

  // Intraday sector flow for the realtime ledger.
  await page.route(/\/api\/market\/sector-flow\/intraday(?:[?#]|$)/, route => {
    flowUrls.push(new URL(route.request().url()));
    return route.fulfill({
      json: liveEnvelope({
        status: 'unavailable',
        points: [{
          batch_id: BATCH, source_id: 'licensed-feed', source_version: 'v1',
          sector: SECTOR, event_at: '2026-09-10T01:30:00Z', available_at: '2026-09-10T01:30:00Z',
          net_inflow: null, unit: 'CNY', unavailable_reason: 'field_not_authorized',
        }],
      }, { quality_flags: ['field_not_authorized'] }),
    });
  });

  let quoteAttempts = 0;
  await page.route('**/api/realtime/**', route => {
    const url = new URL(route.request().url());
    realtimeUrls.push(url);
    if (route.request().method() !== 'GET') {
      return route.fulfill({ status: 405, json: { code: 'METHOD_NOT_ALLOWED' } });
    }
    if (url.pathname === '/api/realtime/quotes') {
      quoteAttempts += 1;
      if (realtimeOptions.revokeFirstQuote && quoteAttempts === 1) {
        return route.fulfill({ status: 403, json: { code: 'FORBIDDEN' } });
      }
      return route.fulfill({ json: liveEnvelope({
        quote_batch_id: BATCH,
        source_event_at: '2026-09-10T01:30:00Z',
        received_at: '2026-09-10T01:30:05Z',
        delay_ms: 5000,
        status: 'delayed',
        quotes: [{
          security_id: SECURITY, last: 10.25, bid: null, ask: null,
          bid_size: null, ask_size: null, volume: 0, amount: 0,
          order_book_status: 'unavailable',
          order_book_unavailable_reason: 'field_not_authorized',
        }],
      }, { trade_date: null, quality_flags: ['delayed'] }) });
    }
    if (url.pathname === '/api/realtime/minutes') {
      return route.fulfill({ json: liveEnvelope({
        status: 'incomplete',
        bars: [
          {
            batch_id: BATCH, source_id: 'licensed-feed', source_version: 'v1',
            security_id: SECURITY, event_at: '2026-09-10T01:30:00Z',
            received_at: '2026-09-10T01:30:00Z', available_at: '2026-09-10T01:30:00Z',
            open: 10, high: 10, low: 10, close: 10, volume: 0, amount: 0,
            revision: 1, final: true,
          },
          {
            batch_id: BATCH, source_id: 'licensed-feed', source_version: 'v1',
            security_id: SECURITY, event_at: '2026-09-10T01:32:00Z',
            received_at: '2026-09-10T01:32:00Z', available_at: '2026-09-10T01:32:00Z',
            open: 10.25, high: 10.25, low: 10.25, close: 10.25,
            volume: 0, amount: 0, revision: 1, final: true,
          },
        ],
      }, { coverage: { expected: 3, valid: 2, excluded: 0, missing: 1 }, quality_flags: ['stream_gap'] }) });
    }
    if (url.pathname === '/api/realtime/anomalies') {
      return route.fulfill({ json: liveEnvelope({
        status: 'complete',
        events: [{
          event_id: 'event-1', batch_id: BATCH, security_id: SECURITY,
          rule_version: 'return-v1', event_at: '2026-09-10T01:32:00Z',
          available_at: '2026-09-10T01:32:02Z', metric: 'window_return',
          window: 2, value: 0.025, threshold: 0.02, quality: 'incomplete',
        }],
        next_cursor: null,
      }, { source_version: null }) });
    }
    return route.fulfill({ status: 404, json: { code: 'NOT_FOUND' } });
  });

  // Task 27 boundary + AI-off probes: these namespaces must never be touched by
  // the reset workbench. Routes are installed so a stray call is recorded and
  // returns a loud 500 instead of silently passing through the dev proxy.
  await page.route('**/api/research/**', route => {
    researchCalls.push(new URL(route.request().url()).pathname);
    return route.fulfill({ status: 500, json: { code: 'TASK27_MUST_BE_ABSENT' } });
  });
  page.on('request', request => {
    const url = new URL(request.url());
    if (/\/api\/research\//.test(url.pathname)) researchUrls.push(url);
    if (/\/api\/ai\//.test(url.pathname)) aiUrls.push(url);
  });

  return { realtimeUrls, flowUrls, researchUrls, aiUrls, researchCalls };
}

function nav(page: Page, name: string) {
  return page.getByRole('navigation', { name: '主导航' }).getByRole('link', { name, exact: true });
}

test('pinned daily publication survives 总览→梯队→涨幅 navigation while realtime stays an independent batch', async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on('console', message => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });
  const probes = await mockCrossDomain(page);

  await page.goto(`/?view=overview&snapshot=${ID}&date=${DATE}&security=${SECURITY}&sector=${SECTOR}`);
  await expect(page.getByRole('heading', { name: '市场总览', exact: true })).toBeVisible();
  await expect(page.getByLabel('快照版本')).toHaveValue(ID);
  const realtime = page.getByRole('region', { name: '实时证据账本' });
  await expect(realtime).toBeVisible();
  await expect(realtime.getByText('延迟 5,000 ms')).toBeVisible();

  // The live stream is keyed by security/sector/date, never by the daily
  // publication, so it cannot be read back as a historical snapshot.
  await expect.poll(() => probes.realtimeUrls.some(u => u.pathname === '/api/realtime/quotes')).toBe(true);
  expect(probes.realtimeUrls.every(u => u.searchParams.get('snapshot_id') === null
    && u.searchParams.get('snapshot') === null)).toBe(true);

  await nav(page, '梯队').click();
  await expect(page).toHaveURL(/view=leaders/);
  await expect(page).toHaveURL(new RegExp(`snapshot=${ID}`));
  await expect(page.getByRole('table', { name: '日期与板数矩阵' })).toBeVisible();
  await expect(page.getByRole('heading', { name: '题材轮动时间轴' })).toBeVisible();

  await nav(page, '涨幅').click();
  await expect(page).toHaveURL(/view=analysis/);
  await expect(page).toHaveURL(new RegExp(`snapshot=${ID}`));
  await expect(page.getByRole('region', { name: '白名单查询面板' })).toBeVisible();
  await expect(page.getByLabel('快照版本')).toHaveValue(ID);

  await nav(page, '总览').click();
  await expect(page.getByRole('heading', { name: '市场总览', exact: true })).toBeVisible();
  // Primary navigation keeps the global publication/date but intentionally clears
  // the per-view security selection (useLocation destination contract); sector is
  // remembered with the overview view, so its live flow ledger re-mounts.
  await expect(page).toHaveURL(new RegExp(`snapshot=${ID}`));
  await expect(page).toHaveURL(/sector=SEC001/);
  await expect(page).not.toHaveURL(/security=/);
  await expect(page.getByLabel('快照版本')).toHaveValue(ID);
  await expect(realtime.getByRole('heading', { name: '题材分时资金流 · SEC001' })).toBeVisible();
  await expect.poll(() => probes.flowUrls.length).toBeGreaterThan(0);

  // Every live batch request (quotes/minutes/anomalies/intraday flow) stays keyed
  // by its own batch inputs and is never bound to the pinned daily publication.
  expect([...probes.realtimeUrls, ...probes.flowUrls].every(u =>
    u.searchParams.get('snapshot_id') === null
    && u.searchParams.get('snapshot') === null)).toBe(true);

  expect(probes.researchUrls).toEqual([]);
  expect(probes.researchCalls).toEqual([]);
  expect(probes.aiUrls).toEqual([]);
  expect(consoleErrors).toEqual([]);
});

test('LHB stock evidence is read-only and carries the synthetic authorization badge', async ({ page }) => {
  const lhbRequests: { method: string; path: string }[] = [];
  await mockMarket(page);
  await page.route(/\/api\/stock\/[^/?#]+\/kline(?:[?#]|$)/, route => route.fulfill({
    json: {
      ...resetSummary(snapshot),
      basis: { unit: 'currency/share', currency: 'CNY', price_type: 'raw', benchmark: null },
      data: {
        security_id: SECURITY, adjustment: 'raw',
        adjusted_start: '2026-08-13', adjusted_end: DATE,
        chart_type: 'line', points: [],
      },
    },
  }));
  await page.route(/\/api\/news(?:[?#]|$)/, route => route.fulfill({
    json: {
      ...resetSummary(snapshot),
      data: { articles: [], page: 1, size: 20, total: 0, sources: [], event_count: 0 },
    },
  }));
  const lhbEnvelope = (data: unknown) => ({
    date: DATE, as_of: '2026-09-10T08:01:00Z', source: 'demo-feed', source_version: 'v1',
    quality_flags: [], basis: { unit: '元', currency: 'CNY' },
    coverage: {
      total_disclosures: 1, visible_disclosures: 1, late_disclosures: 0,
      selected_disclosures: 1, seat_count: 1, complete_seats: 1, unknown_seats: 0,
    },
    data,
  });
  await page.route('**/api/lhb/seats/**', route => {
    const req = route.request();
    lhbRequests.push({ method: req.method(), path: new URL(req.url()).pathname });
    return route.fulfill({ json: lhbEnvelope({
      seat: { source_id: 'demo-feed', seat_id: 'SEAT-A', source_seat_name: '同名席位演示', mapping_version: 'map-v1' },
      identity_state: 'known', items: [],
    }) });
  });
  await page.route('**/api/lhb**', route => {
    const req = route.request();
    lhbRequests.push({ method: req.method(), path: new URL(req.url()).pathname });
    return route.fulfill({ json: lhbEnvelope({
      items: [{
        disclosure_id: 'disc-1', security_id: SECURITY, disclosure_date: DATE,
        reason: '日价格涨幅偏离值达到 7%', revision: 1,
        source_id: 'demo-feed', source_version: 'v1',
        source_event_at: '2026-09-10T07:10:00Z', received_at: '2026-09-10T07:20:00Z',
        available_at: '2026-09-10T07:45:00Z',
        seats: [{
          source_row_id: 'row-A', seat_id: 'SEAT-A', source_seat_name: '同名席位演示',
          mapping_version: 'map-v1', side: 'buy', rank: 1, buy_amount: 100,
          sell_amount: 40, currency: 'CNY', unit: '元',
          completeness: 'complete', net_amount: 60,
        }],
      }],
      total: 1, page: 1, size: 20,
    }) });
  });

  await page.goto(`/?view=stock&snapshot=${ID}&date=${DATE}&security=${SECURITY}`);
  const panel = page.getByRole('region', { name: '龙虎榜席位证据' });
  await expect(panel).toBeVisible();
  await expect(panel.getByText('合成演示数据 · 未获真实行情授权')).toBeVisible();
  await expect(page.getByRole('heading', { name: '证券详情' })).toBeVisible();

  expect(lhbRequests.length).toBeGreaterThan(0);
  expect(lhbRequests.every(item => item.method === 'GET')).toBe(true);
  expect(lhbRequests.some(item => item.path === '/api/lhb')).toBe(true);
});

test('word cloud on 消息 view binds licensed terms to its own publication id', async ({ page }) => {
  const cloudRequests: URL[] = [];
  page.on('request', request => {
    const url = new URL(request.url());
    if (url.pathname === '/api/news/wordcloud') cloudRequests.push(url);
  });
  await mockNews(page);
  await page.goto(`/?view=news&snapshot=${NEWS_SNAPSHOT}&date=2026-09-10`);

  const panel = page.getByRole('region', { name: '新闻词云' });
  await expect(panel).toBeVisible();
  await expect(panel.getByRole('button', { name: '机器人，2 篇' })).toBeVisible();
  await expect(page.getByRole('region', { name: '消息列表' })).toBeVisible();

  await expect.poll(() => cloudRequests.length).toBeGreaterThan(0);
  const first = cloudRequests[0];
  expect(first.searchParams.get('snapshot_id')).toBe(NEWS_SNAPSHOT);
  expect(first.searchParams.get('date')).toBe('2026-09-10');
});

test('rotation timeline pins the anchor day to the current publication and is served under leaders', async ({ page }) => {
  const rotationRequests: URL[] = [];
  page.on('request', request => {
    const url = new URL(request.url());
    if (url.pathname === '/api/topics/rotation') rotationRequests.push(url);
  });
  await mockRotation(page);
  await page.goto(`/?view=leaders&snapshot=${ID}&date=${DATE}&role=trend_leader`);

  await expect(page.getByRole('heading', { name: '题材轮动时间轴' })).toBeVisible();
  await expect(page.locator('section.rotation-section')).toBeVisible();

  await expect.poll(() => rotationRequests.length).toBeGreaterThan(0);
  const request = rotationRequests[0];
  expect(request.searchParams.get('snapshot_id')).toBe(ID);
  expect(request.searchParams.get('date')).toBe(DATE);
});

test('NL query parse→preview→explicit confirm→execute binds the result to the pinned publication', async ({ page }) => {
  const counts = { parse: 0, execute: 0 };
  const executeBodies: unknown[] = [];
  await mockMarket(page);
  await page.route(/\/api\/rank\//, route => route.fulfill({
    json: { ...resetSummary(snapshot), source: 'analysis_materialization', data: null },
  }));
  await page.route('**/api/query/parse', route => {
    counts.parse += 1;
    return route.fulfill({ json: {
      query_id: QUERY_ID,
      typed_ast: {
        version: 1, ast_hash: HASH,
        predicates: [{ field: 'return_ratio', op: 'gt', value: 0.05 }],
        sort: { field: 'return_ratio', direction: 'desc' },
        limit: 50,
      },
      explanation: '涨幅大于5%；按涨幅降序排列；最多返回 50 条。',
      unsupported: [],
    } });
  });
  await page.route('**/api/query/execute', route => {
    counts.execute += 1;
    executeBodies.push(route.request().postDataJSON());
    return route.fulfill({ json: {
      trade_date: DATE, as_of: '2026-09-10T08:01:00Z', snapshot_id: ID,
      source: 'analysis_materialization', source_version: 'analysis-v1',
      coverage: { expected: 65, valid: 1, excluded: 60, missing: 4 },
      quality_flags: [], rule_version: 'role-v1',
      basis: { unit: 'ratio', currency: 'CNY', price_type: 'raw_close', benchmark: 'B000300' },
      data: {
        fields: [
          { key: 'security_id', label: '代码' },
          { key: 'name', label: '名称' },
          { key: 'sector_ids', label: '题材' },
          { key: 'roles', label: '角色' },
          { key: 'boards', label: '板数' },
          { key: 'return_ratio', label: '涨幅' },
        ],
        rows: [{
          security_id: 'TEST001', name: '合成测试证券1', sector_ids: ['SEC001'],
          roles: ['trend_leader'], boards: 2, return_ratio: 0.0523,
        }],
        total: 1, unavailable: [],
      },
    } });
  });

  await page.goto(`/?view=analysis&snapshot=${ID}&date=${DATE}`);
  const panel = page.getByRole('region', { name: '白名单查询面板' });
  await expect(panel).toBeVisible();
  await panel.getByRole('textbox', { name: '查询语句' }).fill('涨幅大于5%');
  await panel.getByRole('button', { name: '解析' }).click();
  await expect(panel.getByRole('group', { name: '查询预览' })).toBeVisible();
  expect(counts.execute).toBe(0);

  await panel.getByRole('button', { name: '执行查询' }).click();
  await expect(panel.getByRole('table', { name: '查询结果' })).toBeVisible();
  expect(counts.execute).toBe(1);
  const body = executeBodies[0] as Record<string, unknown>;
  expect(body).toMatchObject({ query_id: QUERY_ID, confirmed_ast_hash: HASH, snapshot_id: ID });
  expect(body).not.toHaveProperty('date');
  await expect(panel).toContainText('结果快照 2026-09-10 · v1');
});

test('local note saves while offline and recovers after reload without any owner request', async ({ page }) => {
  const ownerRequests: string[] = [];
  page.on('request', request => {
    if (/\/api\/owner\//.test(new URL(request.url()).pathname)) ownerRequests.push(request.url());
  });
  await mockMarket(page);
  await page.goto(`/?view=review&snapshot=${ID}&date=${DATE}`);
  await expect(page.getByRole('heading', { name: '我的复盘', exact: true })).toBeVisible();

  await page.getByLabel('标题', { exact: true }).fill('全范围终验笔记');
  await page.getByRole('textbox', { name: '正文', exact: true }).fill('固定快照下的本地判断');
  await page.getByRole('button', { name: '保存笔记', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: '回读验证通过' })).toBeVisible();

  await page.reload();
  await page.getByRole('button', { name: '全范围终验笔记', exact: true }).click();
  await expect(page.getByRole('textbox', { name: '正文', exact: true })).toHaveValue('固定快照下的本地判断');

  await page.context().setOffline(true);
  await page.getByRole('textbox', { name: '正文', exact: true }).fill('断网后仍可保存');
  await page.getByRole('button', { name: '保存笔记', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: '回读验证通过' })).toContainText('revision 2');
  expect(ownerRequests).toEqual([]);
});

test('realtime authorization revocation hides the live batch and never falls back to the daily snapshot', async ({ page }) => {
  const probes = await mockCrossDomain(page, { revokeFirstQuote: true });
  await page.goto(`/?view=overview&snapshot=${ID}&date=${DATE}&security=${SECURITY}&sector=${SECTOR}`);

  const realtime = page.getByRole('region', { name: '实时证据账本' });
  await expect(realtime.getByText('实时来源授权已撤销')).toBeVisible();
  // The daily, licensed publication is unaffected.
  await expect(page.getByRole('heading', { name: '市场总览', exact: true })).toBeVisible();
  await expect(page.getByLabel('快照版本')).toHaveValue(ID);
  await expect(page.getByRole('button', { name: '重新检查实时来源' })).toBeVisible();
  expect(probes.researchCalls).toEqual([]);
});

test('dark theme and compact density persist across navigation, and Ctrl+K keeps the pinned snapshot', async ({ page }) => {
  await mockCrossDomain(page);
  await page.goto(`/?view=leaders&snapshot=${ID}&date=${DATE}`);
  await expect(page.getByRole('table', { name: '日期与板数矩阵' })).toBeVisible();

  await page.getByRole('button', { name: '切换到深色主题' }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await page.getByRole('button', { name: '切换为紧凑密度' }).click();
  await expect(page.locator('html')).toHaveAttribute('data-density', 'compact');

  await page.keyboard.press('Control+k');
  const dialog = page.getByRole('dialog', { name: '命令面板' });
  await expect(dialog).toBeVisible();
  await expect(page.getByRole('group', { name: '页面' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);

  await nav(page, '总览').click();
  await expect(page.getByRole('heading', { name: '市场总览', exact: true })).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`snapshot=${ID}`));
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await expect(page.locator('html')).toHaveAttribute('data-density', 'compact');
});

// Task 27 (B01/B02, EX14/EX15) is intentionally unimplemented. This is a
// *passing* boundary check, not a skipped test: the workbench must issue no
// research calls and render no backtest / simulation-ledger entry point.
test('Task 27 blocked boundary: no /api/research traffic and no backtest or simulation-ledger entry point', async ({ page }) => {
  const probes = await mockCrossDomain(page);
  await page.goto(`/?view=overview&snapshot=${ID}&date=${DATE}&security=${SECURITY}&sector=${SECTOR}`);
  await expect(page.getByRole('heading', { name: '市场总览', exact: true })).toBeVisible();
  await nav(page, '梯队').click();
  await expect(page.getByRole('heading', { name: '题材轮动时间轴' })).toBeVisible();
  await nav(page, '涨幅').click();
  await expect(page.getByRole('region', { name: '白名单查询面板' })).toBeVisible();

  expect(probes.researchUrls).toEqual([]);
  expect(probes.researchCalls).toEqual([]);
  await expect(page.getByRole('button', { name: /回测|模拟账本|因子体检/ })).toHaveCount(0);
  await expect(page.getByRole('link', { name: /回测|模拟账本|因子体检/ })).toHaveCount(0);
  await expect(page.locator('body')).not.toContainText(/walk-forward|滚动调权|模拟账本/);
});

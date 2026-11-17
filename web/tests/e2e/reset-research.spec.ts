import { expect, test, type Page } from '@playwright/test';
import { ID, mockMarket, resetSummary, snapshot } from '../fixtures/market';
import type { BacktestRunStatus } from '../../src/features/research/backtest-client';

/**
 * Task 17 owner research surface E2E (AC-14/AC-15, TR-17.2).
 *
 * The reset workbench is the only build that mounts the owner backtest panel
 * and the local simulation ledger. These probes drive the real interactive
 * flows against page.route mocks of the closed /api/research namespace and the
 * profile-partitioned IndexedDB ledger:
 *
 *   - manifest fail-closed gate, submit -> 1s poll -> terminal result/cancel,
 *   - strictly local simulation accounting with reload recovery,
 *   - FR-16 research disclosures, zero console errors, 375px and 1440px/200%.
 *
 * No test.skip(): every probe is a passing behavioural assertion.
 */
const DATE = '2026-09-10';
const RUN_ID = '00000000-0000-4000-8000-0000000000aa';
const TOPIC_ID = '00000000-0000-4000-8000-0000000000bb';
const ROLE_ID = '00000000-0000-4000-8000-0000000000cc';
const UUID = /^[\da-f]{8}-[\da-f]{4}-[1-5][\da-f]{3}-[89ab][\da-f]{3}-[\da-f]{12}$/i;

const manifestFixture = {
  snapshot_id: ID,
  domains: [
    { domain: 'market', snapshot_id: ID },
    { domain: 'topic', snapshot_id: TOPIC_ID },
    { domain: 'role', snapshot_id: ROLE_ID },
  ],
  versions: {
    adjustment: 'adjustment-v1',
    calendar: 'calendar-cn-v1',
    pool: 'topic-pool-v1',
  },
} as const;

const succeededResult = () => ({
  metrics: {
    initial_equity: '100000.00',
    final_equity: '102345.67',
    total_return: '0.0235',
    max_drawdown: '0.0120',
  },
  equity: [
    { trade_date: '2025-01-06', equity: '100000.00' },
    { trade_date: '2025-06-27', equity: '102345.67' },
  ],
  trades: [{
    security_id: 'TEST001',
    side: 'buy',
    quantity: 100,
    signal_date: '2025-01-06',
    trade_date: '2025-01-07',
    raw_close: '10.00',
    fill_price: '10.01',
    gross_amount: '1001.00',
    commission: '5.00',
    stamp_tax: '0.00',
    net_cash_delta: '-1006.00',
  }],
  rejections: [],
  quality: [],
  folds: [],
  baseline: { cost_sensitivity: [] },
  ablation: null,
});

function runRecord(status: BacktestRunStatus, extra: Record<string, unknown> = {}) {
  return {
    run_id: RUN_ID,
    status,
    config_hash: 'a'.repeat(64),
    research_only: true,
    result: null,
    failure: null,
    ...extra,
  };
}

type ResearchCall = { method: string; path: string };

type ResearchMockOptions = {
  // GET /api/research/backtests/{id} answers follow this sequence, then stick
  // at the last (terminal) status.
  runSequence?: BacktestRunStatus[];
  // 'malformed' serves a 200 body that fails transport validation; a gate
  // promise holds every manifest response until the caller releases it.
  manifest?: 'valid' | 'malformed';
  gate?: Promise<void>;
};

type ResearchMocks = {
  calls: ResearchCall[];
  submitBodies: unknown[];
  cancelBodies: unknown[];
  runGets: number;
};

async function mockAnalysisRoutes(page: Page): Promise<void> {
  // Safety net first: every un-stubbed /api call gets JSON instead of leaking
  // to the Vite proxy, so a missing mock can never hide as a network console error.
  await page.route('**/api/**', route =>
    route.fulfill({ status: 404, json: { code: 'NOT_FOUND' } }));
  // Registered afterwards, these handlers win over the catch-all.
  await mockMarket(page);
  await page.route(/\/api\/rank\//, route =>
    route.fulfill({
      json: { ...resetSummary(snapshot), source: 'analysis_materialization', data: null },
    }));
  await page.route(/\/api\/market\/sentiment\/research(?:[?#]|$)/, route =>
    route.fulfill({
      json: {
        ...resetSummary(snapshot),
        data: null,
        quality_flags: ['materialization_unavailable'],
      },
    }));
}

async function mockResearchRoutes(
  page: Page,
  options: ResearchMockOptions = {},
): Promise<ResearchMocks> {
  const mocks: ResearchMocks = { calls: [], submitBodies: [], cancelBodies: [], runGets: 0 };
  const sequence = options.runSequence ?? ['queued', 'running', 'succeeded'];
  await page.route('**/api/research/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    mocks.calls.push({ method: request.method(), path });

    if (request.method() === 'GET' && path === `/api/research/manifests/${ID}`) {
      if (options.gate) await options.gate;
      if (options.manifest === 'malformed') {
        return route.fulfill({ status: 200, json: { snapshot_id: ID } });
      }
      return route.fulfill({ json: manifestFixture });
    }
    if (request.method() === 'POST' && path === '/api/research/backtests') {
      mocks.submitBodies.push(request.postDataJSON());
      return route.fulfill({ json: { run_id: RUN_ID, status: 'queued' } });
    }
    if (request.method() === 'GET' && path === `/api/research/backtests/${RUN_ID}`) {
      mocks.runGets += 1;
      const status = sequence[Math.min(mocks.runGets - 1, sequence.length - 1)];
      const extra = status === 'succeeded' ? { result: succeededResult() } : {};
      return route.fulfill({ json: runRecord(status, extra) });
    }
    if (request.method() === 'POST' && path === `/api/research/backtests/${RUN_ID}/cancel`) {
      mocks.cancelBodies.push(request.postDataJSON());
      return route.fulfill({ json: runRecord('cancelled') });
    }
    return route.fulfill({ status: 500, json: { code: 'UNEXPECTED_RESEARCH_CALL' } });
  });
  return mocks;
}

function collectConsoleErrors(page: Page): string[] {
  const consoleErrors: string[] = [];
  page.on('console', message => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });
  return consoleErrors;
}

async function gotoAnalysis(page: Page): Promise<void> {
  await page.goto(`/?view=analysis&snapshot=${ID}&date=${DATE}`);
}

test.describe('Task 17 owner research backtest workbench', () => {
  test('manifest gate holds submit, then submit→poll→succeeded renders the materialised result at 1440px/200%', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 1000 });
    const consoleErrors = collectConsoleErrors(page);
    let releaseManifest!: () => void;
    const gate = new Promise<void>(resolve => { releaseManifest = resolve; });
    await mockAnalysisRoutes(page);
    const research = await mockResearchRoutes(page, { gate });

    await gotoAnalysis(page);
    const panel = page.getByRole('region', { name: '研究回测' });
    await expect(panel).toBeVisible();

    // FR-16 disclosure is present in every panel state.
    await expect(panel.getByTestId('backtest-disclosure')).toContainText(
      '仅用于研究复盘，不代表未来表现，不构成投资建议',
    );

    // Fail-closed gate: until the validated manifest arrives, submit is paused.
    const submit = panel.getByRole('button', { name: '提交回测' });
    await expect(submit).toBeDisabled();
    await expect(panel.getByText('研究清单未就绪，提交回测已暂停。')).toBeVisible();
    expect(research.calls.filter(call => call.method === 'POST')).toEqual([]);

    releaseManifest();
    await expect(submit).toBeEnabled();
    await expect(panel.getByText('研究清单未就绪，提交回测已暂停。')).toHaveCount(0);

    // StrictMode replays the mount effect in dev, so the manifest read may
    // fire twice; every read must still bind to the selected publication.
    const manifestCalls = research.calls.filter(
      call => call.method === 'GET' && call.path === `/api/research/manifests/${ID}`,
    );
    expect(manifestCalls.length).toBeGreaterThanOrEqual(1);
    expect(research.calls.every(call => /^\/api\/research\//.test(call.path))).toBe(true);

    await submit.click();

    // The EX14 body carries the frozen schema, the manifest context and a key.
    expect(research.submitBodies).toHaveLength(1);
    const body = research.submitBodies[0] as Record<string, unknown>;
    expect(body).toMatchObject({
      schema_version: 'dragon-radar.research-backtest/v1',
      snapshot_id: ID,
      strategy_id: 'factor-cross-sectional-topn-v1',
    });
    expect(body.idempotency_key).toMatch(UUID);
    expect(body.input_manifests).toMatchObject({
      domains: manifestFixture.domains,
      versions: manifestFixture.versions,
    });
    const experiment = body.experiment as { legs: string[] };
    expect(experiment.legs).toContain('baseline');

    const status = panel.getByRole('status');
    await expect(status).toHaveText('运行状态：排队中');
    await expect(status).toHaveText('运行状态：运行中');
    await expect(status).toHaveText('运行状态：已完成');

    const result = panel.getByRole('region', { name: '回测结果' });
    await expect(result).toBeVisible();
    await expect(result.getByRole('table', { name: '净值曲线' })).toContainText('2025-06-27');
    await expect(result.getByRole('table', { name: '成交流水' })).toContainText('TEST001');
    await expect(result).toContainText('期末权益');
    await expect(result).toContainText('102345.67');

    // 200% zoom keeps the whole research surface inside the layout viewport.
    // Under CSS zoom the root's own layout metrics stay in its local coordinate
    // system (window.innerWidth halves instead), so the overflow probe compares
    // documentElement scroll/client width like the shared reset acceptance helper.
    await page.evaluate(() => { document.documentElement.style.zoom = '2'; });
    expect(await page.evaluate(() => document.documentElement.scrollWidth))
      .toBeLessThanOrEqual(await page.evaluate(() => document.documentElement.clientWidth));
    await expect(panel).toBeVisible();

    await capturePage(page, 'research-result-200pct.png');
    expect(consoleErrors).toEqual([]);
  });

  test('cooperative cancel reaches the cancel route and freezes the polling loop at 已取消', async ({ page }) => {
    const consoleErrors = collectConsoleErrors(page);
    await mockAnalysisRoutes(page);
    const research = await mockResearchRoutes(page, {
      runSequence: ['queued', 'queued', 'running'],
    });

    await gotoAnalysis(page);
    const panel = page.getByRole('region', { name: '研究回测' });
    await expect(panel).toBeVisible();
    await expect(panel.getByRole('button', { name: '提交回测' })).toBeEnabled();

    await panel.getByRole('button', { name: '提交回测' }).click();
    await expect(panel.getByRole('status')).toHaveText('运行状态：排队中');
    const getsAtCancel = research.runGets;
    expect(getsAtCancel).toBeGreaterThanOrEqual(1);

    await panel.getByRole('button', { name: '取消运行' }).click();
    await expect(panel.getByRole('status')).toHaveText('运行状态：已取消');

    expect(research.cancelBodies).toHaveLength(1);
    const cancelBody = research.cancelBodies[0] as Record<string, unknown>;
    expect(cancelBody.idempotency_key).toMatch(UUID);
    expect(research.calls).toContainEqual({
      method: 'POST',
      path: `/api/research/backtests/${RUN_ID}/cancel`,
    });

    // A terminal record must not schedule another 1s poll.
    await page.waitForTimeout(1_300);
    expect(research.runGets).toBe(getsAtCancel);
    await capturePage(page, 'research-cancelled.png');
    expect(consoleErrors).toEqual([]);
  });

  test('a malformed manifest fails closed at 375px without any backtest write', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    const consoleErrors = collectConsoleErrors(page);
    await mockAnalysisRoutes(page);
    const research = await mockResearchRoutes(page, { manifest: 'malformed' });

    await gotoAnalysis(page);
    const panel = page.getByRole('region', { name: '研究回测' });
    await expect(panel).toBeVisible();
    await expect(panel.getByText('研究清单未就绪，提交回测已暂停。')).toBeVisible();
    await expect(panel.getByRole('button', { name: '提交回测' })).toBeDisabled();
    await expect(panel.getByTestId('backtest-disclosure')).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth))
      .toBeLessThanOrEqual(await page.evaluate(() => document.documentElement.clientWidth));

    // Only the owner manifest read was attempted; no write or run poll can happen.
    expect(research.submitBodies).toEqual([]);
    expect(research.cancelBodies).toEqual([]);
    expect(research.runGets).toBe(0);
    expect(research.calls.every(call =>
      call.method === 'GET' && call.path === `/api/research/manifests/${ID}`)).toBe(true);
    await capturePage(page, 'research-manifest-375px.png');
    expect(consoleErrors).toEqual([]);
  });
});

test.describe('Task 17 local simulation ledger', () => {
  test('opens a ledger, fills an order, survives reload from IndexedDB and never touches the network at 375px', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    const consoleErrors = collectConsoleErrors(page);
    const allRequests: string[] = [];
    page.on('request', request => allRequests.push(new URL(request.url()).pathname));
    await mockAnalysisRoutes(page);

    await page.goto(`/?view=review&snapshot=${ID}&date=${DATE}`);
    await expect(page.getByRole('heading', { name: '我的复盘', exact: true })).toBeVisible();
    await page.getByRole('group', { name: '本地资料分区' })
      .getByRole('button', { name: '模拟账本' }).click();

    const panel = page.getByRole('region', { name: '本地模拟账本' });
    await expect(panel).toBeVisible();
    await expect(panel.getByTestId('simulation-disclosure')).toHaveText(
      '仅研究模拟 · 不构成投资建议 · 不接券商/不发真实订单',
    );

    // Open the account with the golden default cash.
    await panel.getByRole('button', { name: '开立账本' }).click();
    await expect(panel.getByRole('status').filter({ hasText: '模拟账本已开立' })).toContainText(
      '回读验证通过',
    );

    const requestsAfterOpen = allRequests.length;

    // Fill a buy order: signal day before the fill day, positive close, no limit.
    await panel.getByLabel('证券代码').fill('TEST001');
    await panel.getByLabel('数量').fill('100');
    await panel.getByLabel('信号日期').fill('2026-09-09');
    await panel.getByLabel('成交日期').fill('2026-09-10');
    await panel.getByLabel('收盘价').fill('10.5');
    await panel.getByRole('button', { name: '提交模拟下单' }).click();
    await expect(panel.getByRole('status').filter({ hasText: '模拟成交已入账' })).toContainText(
      '回读验证通过',
    );

    // The ledger is strictly local: accounting actions issue zero HTTP requests.
    expect(allRequests.length - requestsAfterOpen).toBe(0);
    expect(allRequests.some(path => path.startsWith('/api/research/'))).toBe(false);

    await expect(panel.getByRole('table', { name: '模拟委托流水' })).toContainText('TEST001');
    await expect(panel.getByRole('table', { name: '模拟委托流水' })).toContainText('已成交');
    await expect(panel.getByRole('table', { name: '模拟成交流水' })).toContainText('10.5');
    await expect(panel.getByRole('table', { name: '模拟持仓' })).toContainText('TEST001');

    const stored = await readSimulationStores(page);
    expect(stored.accounts).toHaveLength(1);
    // Repository contract (TR-14.1): open mints revision 0, the first fill bumps to 1.
    expect((stored.accounts[0] as Record<string, unknown>).revision).toBe(1);
    expect(stored.orders).toHaveLength(1);
    expect((stored.orders[0] as Record<string, unknown>).status).toBe('filled');
    expect(stored.fills).toHaveLength(1);
    expect(stored.commands.length).toBeGreaterThanOrEqual(2);
    expect(await page.evaluate(() => document.documentElement.scrollWidth))
      .toBeLessThanOrEqual(await page.evaluate(() => document.documentElement.clientWidth));

    // Reload restores the whole ledger from the profile-partitioned IDB.
    await page.reload();
    await expect(page.getByRole('heading', { name: '我的复盘', exact: true })).toBeVisible();
    await page.getByRole('group', { name: '本地资料分区' })
      .getByRole('button', { name: '模拟账本' }).click();
    const restored = page.getByRole('region', { name: '本地模拟账本' });
    await expect(restored).toBeVisible();
    await expect(restored.getByRole('table', { name: '模拟委托流水' })).toContainText('TEST001');
    await expect(restored.getByRole('table', { name: '模拟持仓' })).toContainText('TEST001');
    await expect(restored).toContainText('账本 revision 1');
    expect(allRequests.some(path => path.startsWith('/api/research/'))).toBe(false);
    await capturePage(page, 'simulation-restored-375px.png');
    expect(consoleErrors).toEqual([]);
  });
});

async function capturePage(page: Page, name: string): Promise<void> {
  // Capture from the page top so sticky navigation does not cover the middle
  // of a tall element screenshot. Keep the rendered page and styles intact.
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: test.info().outputPath(name), fullPage: true });
}

async function readSimulationStores(page: Page): Promise<{
  accounts: unknown[];
  orders: unknown[];
  fills: unknown[];
  commands: unknown[];
}> {
  return page.evaluate(async () => {
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      const opening = indexedDB.open('dragon-radar-local-v3');
      opening.onsuccess = () => resolve(opening.result);
      opening.onerror = () => reject(opening.error);
    });
    const getAll = (store: string) => new Promise<unknown[]>((resolve, reject) => {
      const request = db.transaction(store).objectStore(store).getAll();
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    const [accounts, orders, fills, commands] = await Promise.all([
      getAll('simulation_accounts'),
      getAll('simulation_orders'),
      getAll('simulation_fills'),
      getAll('simulation_commands'),
    ]);
    db.close();
    return { accounts, orders, fills, commands };
  });
}

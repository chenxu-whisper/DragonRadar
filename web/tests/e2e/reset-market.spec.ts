import { expect, test, type Page, type Route } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const CURRENT = '00000000-0000-4000-8000-000000000091';
const PRIOR = '00000000-0000-4000-8000-000000000090';

const coverage = { expected: 5, valid: 5, excluded: 0, missing: 0 };
const basis = { unit: 'ratio', currency: 'CNY', price_type: 'raw', benchmark: null };

function envelope(snapshotId: string, tradeDate: string, data: unknown) {
  return {
    trade_date: tradeDate,
    as_of: `${tradeDate}T10:00:00Z`,
    snapshot_id: snapshotId,
    source: 'synthetic-market',
    source_version: 'market-v1',
    coverage,
    quality_flags: [],
    rule_version: 'market-v1',
    basis,
    data,
  };
}

function summary(snapshotId: string, tradeDate: string) {
  return envelope(snapshotId, tradeDate, {
    revision: 1,
    supersedes: null,
    published_at: `${tradeDate}T10:05:00Z`,
    security_count: 5,
    calendar_sessions: [tradeDate],
    earliest_date: tradeDate,
  });
}

async function mockResetMarket(page: Page, listIncludesSelected = true) {
  await page.route('**/api/**', async (route: Route) => {
    const url = new URL(route.request().url());
    const respond = (body: unknown) => route.fulfill({ json: body });
    if (url.pathname === '/api/snapshots/list') {
      const day = url.searchParams.get('date');
      const items = day === '2026-09-09'
        ? [summary(PRIOR, '2026-09-09')]
        : listIncludesSelected ? [summary(CURRENT, '2026-09-10')] : [];
      if (!items.length) return respond(envelope(CURRENT, '2026-09-10', { snapshots: [] }));
      return respond(envelope(items[0].snapshot_id, items[0].trade_date, { snapshots: items }));
    }
    if (url.pathname === `/api/snapshots/${CURRENT}`) {
      return respond(summary(CURRENT, '2026-09-10'));
    }
    if (url.pathname === `/api/snapshots/${PRIOR}`) {
      return respond(summary(PRIOR, '2026-09-09'));
    }
    const selected = url.searchParams.get('snapshot_id') === PRIOR
      ? [PRIOR, '2026-09-09'] : [CURRENT, '2026-09-10'];
    if (url.pathname === '/api/index/IDX/history') {
      return respond({
        ...envelope(selected[0], selected[1], null),
        coverage: null,
        basis: { ...basis, unit: 'point', currency: null },
        reason: 'materialization_unavailable',
        quality_flags: ['materialization_unavailable'],
      });
    }
    if (url.pathname === '/api/market/overview') {
      return respond(envelope(selected[0], selected[1], {
        indexes: [{ security_id: 'IDX', code: '000300', name: '合成指数', last: 1234, change_ratio: 0.01, amount: null, reason: 'source_field_unavailable' }],
        breadth: {
          up: 3, down: 1, flat: 1, valid_total: 5,
          coverage,
          members: {
            up: [
              { security_id: 'E', code: '000005', name: '合成戊' },
              { security_id: 'A', code: '000001', name: '合成甲' },
              { security_id: 'C', code: '000003', name: '合成丙' },
              { security_id: 'A', code: '000001', name: '合成甲' },
            ],
            down: [{ security_id: 'B', code: '000002', name: '合成乙' }],
            flat: [{ security_id: 'D', code: '000004', name: '合成丁' }],
          },
          seal_rate: {
            numerator: 2, denominator: 3, value: null,
            reason: 'incomplete_touched_limit_coverage',
            coverage: { expected: 5, valid: 4, excluded: 0, missing: 1 },
          },
        },
        sentiment: {
          limit_up_count: 2, limit_down_count: 0, max_boards: 2,
          promote_rate: { yesterday_ge2: 3, numerator: 1, denominator: 2, value: 0.5, excluded: 0, missing: 1, reason: null },
          yesterday_limit_up_median_return: null,
          yesterday_limit_up_return_coverage: { expected: 2, valid: 1, excluded: 0, missing: 1 },
          yesterday_limit_up_return_reason: 'partial_yesterday_limit_up_return_coverage',
        },
        hot_sectors: [{
          sector_id: 'SEC001', name: '合成题材甲', member_count: 3,
          valid_returns: 3, median_return: 0.024, change_ratio: 0.024,
          limit_up_count: 1, quality_flags: ['sample_insufficient'],
          up_ratio: { numerator: 2, denominator: 3, value: 0.6667, reason: null },
          limit_up_ratio: { numerator: 1, denominator: 3, value: 0.3333, reason: null },
          net_inflow: null, net_inflow_reason: 'source_field_unavailable',
          coverage: { expected: 3, valid: 3, excluded: 0, missing: 0 },
        }],
        factors: {
          'FAC-M01': {
            advancing: { numerator: 3, denominator: 5, value: 0.6, reason: null, coverage },
            above_ma20: {
              numerator: 2, denominator: 4, value: 0.5, reason: null,
              coverage: { expected: 5, valid: 4, excluded: 0, missing: 1 },
            },
            method: 'adjusted_close_ma20_including_current',
          },
          'FAC-M02': {
            value: null, today_total: null, baseline_mean: null,
            baseline_days: 20, baseline_excludes_current: true,
            coverage: { expected: 21, valid: 19, excluded: 0, missing: 2 },
            reason: 'requires_complete_current_and_prior_20_sessions',
            method: 'same_pool_amount_vs_prior_20_session_mean',
          },
          'FAC-M03': {
            indexes: [{
              security_id: 'IDX', code: '000300', name: '合成指数', close: 1234,
              close_over_ma20: 0.031, close_over_ma60: null, volatility_20: 0.018,
              volatility_method: 'sample_stddev_non_annualized',
              coverage: {
                ma20: { expected: 20, valid: 20, excluded: 0, missing: 0 },
                ma60: { expected: 60, valid: 59, excluded: 0, missing: 1 },
                return20: { expected: 20, valid: 20, excluded: 0, missing: 0 },
              },
              close_over_ma20_reason: null,
              close_over_ma60_reason: 'requires_60_consecutive_index_closes',
              volatility_20_reason: null,
              reason: null,
            }],
            method: 'close_over_ma20_ma60_and_20_return_sample_stddev_non_annualized',
          },
        },
        historical_topic_analysis: { enabled: false, reason: 'historical_topic_coverage_unavailable' },
      }));
    }
    if (url.pathname === '/api/market/heatmap') {
      return respond(envelope(selected[0], selected[1], {
        columns: [1, 2],
        cells: [
          { trade_date: '2026-09-09', boards: 2, count: 1 },
          { trade_date: '2026-09-10', boards: 1, count: 2 },
        ],
      }));
    }
    if (url.pathname === '/api/market/sector-flow') return respond(envelope(selected[0], selected[1], { sectors: [] }));
    if (url.pathname === '/api/market/sentiment') return respond(envelope(selected[0], selected[1], { series: [] }));
    return route.fulfill({ status: 404, json: {} });
  });
}

test('index detail uses archived factors and restores overview focus without stock requests', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await mockResetMarket(page);
  const stockRequests: string[] = [];
  page.on('request', request => {
    if (new URL(request.url()).pathname.startsWith('/api/stock/')) stockRequests.push(request.url());
  });
  await page.goto(`/?view=overview&snapshot=${CURRENT}&date=2026-09-10&market_filter=up&q=合成`);
  const opener = page.getByRole('button', { name: /查看指数详情.*合成指数/ });
  await opener.click();
  const drawer = page.getByRole('dialog', { name: '指数详情 · 只读' });
  await expect(drawer).toContainText('1234');
  await expect(drawer).toContainText('3.1%');
  await expect(drawer).toContainText('requires_60_consecutive_index_closes');
  await expect(drawer).toContainText('当前发布未归档指数历史行情');
  expect((await new AxeBuilder({ page }).include('dialog[open]').analyze()).violations).toEqual([]);
  expect(await drawer.evaluate(element => element.getBoundingClientRect().width)).toBeLessThanOrEqual(375);
  await page.screenshot({ path: testInfo.outputPath('index-detail-375.png') });
  const close = drawer.getByRole('button', { name: '关闭详情' });
  await close.focus();
  await page.keyboard.press('Tab');
  await expect(drawer.getByRole('combobox', { name: '指数历史窗口' })).toBeFocused();
  await page.keyboard.press('Shift+Tab');
  await expect(close).toBeFocused();
  await page.keyboard.press('Shift+Tab');
  await expect(drawer.getByRole('combobox', { name: '指数历史窗口' })).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(close).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(drawer).not.toBeVisible();
  await expect(opener).toBeFocused();
  await expect(page).toHaveURL(/market_filter=up/);
  await expect(page).toHaveURL(/q=/);
  expect(stockRequests).toEqual([]);
});

for (const failure of ['missing', 'wrong-date', 'corrupt'] as const) {
  test(`index detail fails closed for ${failure} archived response`, async ({ page }) => {
    await mockResetMarket(page);
    let reads = 0;
    await page.route('**/api/market/overview?**', route => {
      reads += 1;
      if (reads === 1) return route.fallback();
      return route.fulfill({ json: {
        ...envelope(CURRENT, failure === 'wrong-date' ? '2026-09-09' : '2026-09-10',
          failure === 'corrupt' ? { indexes: [{ security_id: 'IDX', name: '错误指数', last: 9999 }] } : null),
        quality_flags: ['materialization_unavailable'],
      } });
    });
    await page.goto(`/?view=overview&snapshot=${CURRENT}&date=2026-09-10`);
    const opener = page.getByRole('button', { name: /查看指数详情.*合成指数/ });
    await opener.click();
    const drawer = page.getByRole('dialog', { name: '指数详情 · 只读' });
    await expect(drawer).toBeVisible();
    await expect(drawer).toContainText(failure === 'missing' ? '指数物化不可用' : failure === 'wrong-date' ? '响应日期' : '响应格式无效');
    await expect(drawer).not.toContainText('9999');
    await page.keyboard.press('Escape');
    await expect(opener).toBeFocused();
  });
}

test('topic detail binds full members to publication and returns from stock without changing filters', async ({ page }, testInfo) => {
  await mockResetMarket(page);
  await page.route('**/api/stock/A?**', route => route.fulfill({ json: envelope(CURRENT, '2026-09-10', {
    security_id: 'A', name: '合成甲', quote: { last: 10, change_ratio: 0.024, amount: 100 },
    roles: [], seal: { is_limit_up: null, touched_limit: null, first_seal_time: null, last_seal_time: null,
      break_count: null, seal_amount: null, seal_amount_unit: null, continuous_boards: null, reason: 'intraday_seal_fields_unavailable' },
    regulation: { formal_event_count: 0, event_source_completeness: 'complete', reason: null },
  }) }));
  await page.route('**/api/topics/SEC001?**', route => {
    const query = new URL(route.request().url()).searchParams;
    expect(query.get('snapshot_id')).toBe(CURRENT);
    expect(query.get('date')).toBe('2026-09-10');
    return route.fulfill({ json: envelope(CURRENT, '2026-09-10', {
      sector: { sector_id: 'SEC001', name: '合成题材甲', member_count: 1, valid_returns: 1, median_return: 0.024, change_ratio: 0.024, limit_up_count: 1,
        up_ratio: { numerator: 1, denominator: 1, value: 1, reason: null }, limit_up_ratio: { numerator: 1, denominator: 1, value: 1, reason: null },
        net_inflow: null, net_inflow_reason: 'source_field_unavailable', quality_flags: [], coverage: { expected: 1, valid: 1, excluded: 0, missing: 0 } },
      topic_basis: { source: 'synthetic-topic', source_version: 'topic-v1', as_of: '2026-09-10T09:00:00Z', source_time: null, cutoff: '2026-09-10T09:00:00Z',
        market_snapshot_id: CURRENT, topic_snapshot_id: PRIOR, taxonomy_version: 'taxonomy-v1', membership_version: 'membership-v1' },
      definition: { effective_from: '2026-09-10', effective_to: null, available_at: '2026-09-10T09:00:00Z', source_reference: 'evidence-topic' },
      membership_coverage: { expected: 1, received: 1, known: 1, status: 'complete', reason: null, complete_as_of: '2026-09-10T09:00:00Z' },
      members: [{ security_id: 'A', code: '000001', name: '合成甲', effective_from: '2026-09-10', effective_to: null, available_at: '2026-09-10T09:00:00Z', evidence_refs: ['evidence-member'] }],
      total: 1, page: 1, size: 50,
    }) });
  });
  await page.goto(`/?view=overview&snapshot=${CURRENT}&date=2026-09-10&market_filter=up&q=合成`);
  const opener = page.getByRole('link', { name: /合成题材甲/ });
  await opener.click();
  const topic = page.getByRole('dialog', { name: '题材详情 · 只读' });
  await expect(topic).toContainText('membership-v1');
  await expect(topic).toContainText('evidence-member');
  expect((await new AxeBuilder({ page }).include('dialog[open]').analyze()).violations).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath('topic-detail-desktop.png') });
  await topic.getByRole('button', { name: '合成甲', exact: true }).click();
  const stock = page.getByRole('dialog', { name: '证券详情 · 只读' });
  await expect(stock).toBeVisible();
  await expect(page.locator('dialog[open]')).toHaveCount(1);
  await stock.getByRole('button', { name: '返回题材详情', exact: true }).click();
  await expect(topic).toBeVisible();
  await expect(topic.getByRole('button', { name: '合成甲', exact: true })).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(opener).toBeFocused();
  await expect(page).toHaveURL(/market_filter=up/);
  await opener.click();
  await topic.getByRole('button', { name: '合成甲', exact: true }).click();
  await stock.getByRole('button', { name: '加入本地自选', exact: true }).click();
  await expect(stock.getByRole('status').filter({ hasText: '已加入本地自选' })).toBeVisible();
  await stock.getByRole('button', { name: '记录本地判断', exact: true }).click();
  await expect(page).toHaveURL(/security=A/);
  await expect(page.getByTestId('local-watchlist')).toContainText('A');
  await page.getByRole('textbox', { name: '标题', exact: true }).fill('题材成员判断');
  await page.getByRole('textbox', { name: '正文', exact: true }).fill('保留题材下钻的发布依据');
  await page.getByRole('button', { name: '保存笔记', exact: true }).click();
  await expect(page.locator('.local-note-editor').getByRole('status')).toContainText('回读验证通过');
  await page.getByRole('link', { name: '返回市场总览', exact: true }).click();
  await expect(page).toHaveURL(/market_filter=up/);
  await expect(page).toHaveURL(/q=/);
  await expect(page).toHaveURL(new RegExp(`snapshot=${CURRENT}`));
  await expect(opener).toBeVisible();
});

test('topic detail discloses legacy missing materialization and permits retry without changing context', async ({ page }) => {
  await mockResetMarket(page);
  let missing = false;
  await page.route('**/api/topics/SEC001?**', route => missing
    ? route.fulfill({ json: { ...envelope(CURRENT, '2026-09-10', null), quality_flags: ['materialization_unavailable'] } })
    : route.fulfill({ status: 503, json: { code: 'DATA_UNAVAILABLE' } }));
  await page.goto(`/?view=overview&snapshot=${CURRENT}&date=2026-09-10&market_filter=up`);
  await page.getByRole('link', { name: /合成题材甲/ }).click();
  const drawer = page.getByRole('dialog', { name: '题材详情 · 只读' });
  await expect(drawer.getByRole('alert')).toBeVisible();
  missing = true;
  await drawer.getByRole('button', { name: '重试题材详情' }).click();
  await expect(drawer).toContainText('materialization_unavailable');
  await expect(drawer.getByRole('button', { name: '合成甲', exact: true })).toHaveCount(0);
  await page.keyboard.press('Escape');
  await expect(page).toHaveURL(/market_filter=up/);
});

test('sector flow opens the same topic drawer and missing membership cannot imply a candidate pool', async ({ page }) => {
  await mockResetMarket(page);
  await page.route('**/api/market/sector-flow?**', route => route.fulfill({ json: envelope(CURRENT, '2026-09-10', { sectors: [{
    sector_id: 'SEC001', name: '资金流题材', member_count: 3, valid_returns: 3, median_return: 0.024, change_ratio: 0.024, limit_up_count: 1,
    up_ratio: { numerator: 2, denominator: 3, value: 2 / 3, reason: null },
    limit_up_ratio: { numerator: 1, denominator: 3, value: 1 / 3, reason: null },
    net_inflow: null, net_inflow_reason: 'source_field_unavailable', quality_flags: ['sample_insufficient'],
    coverage: { expected: 3, valid: 3, excluded: 0, missing: 0 },
  }] }) }));
  await page.route('**/api/topics/SEC001?**', route => route.fulfill({ json: {
    ...envelope(CURRENT, '2026-09-10', null), quality_flags: ['materialization_unavailable'],
  } }));
  await page.goto(`/?view=overview&snapshot=${CURRENT}&date=2026-09-10`);
  const opener = page.getByRole('region', { name: '板块资金流', exact: true }).getByRole('button', { name: /资金流题材/ });
  await opener.click();
  const drawer = page.getByRole('dialog', { name: '题材详情 · 只读' });
  await expect(drawer).toContainText('materialization_unavailable');
  await expect(page).toHaveURL(/view=overview/);
  await page.keyboard.press('Escape');
  await expect(opener).toBeFocused();
});

test('topic detail rejects a date mismatch and clears content on source revocation', async ({ page }) => {
  await mockResetMarket(page);
  let revoked = false;
  await page.route('**/api/topics/SEC001?**', route => revoked
    ? route.fulfill({ status: 403, json: { code: 'source_forbidden' } })
    : route.fulfill({ json: { ...envelope(CURRENT, '2026-09-09', null), quality_flags: ['materialization_unavailable'] } }));
  await page.goto(`/?view=overview&snapshot=${CURRENT}&date=2026-09-10`);
  await page.getByRole('link', { name: /合成题材甲/ }).click();
  const drawer = page.getByRole('dialog', { name: '题材详情 · 只读' });
  await expect(drawer).toContainText('题材响应与当前选择不匹配');
  revoked = true;
  await drawer.getByRole('button', { name: '重试题材详情' }).click();
  await expect(page.getByRole('heading', { name: '来源访问已被拒绝' })).toBeVisible();
  await expect(page.locator('dialog[open]')).toHaveCount(0);
});

test('explicit snapshot deep link does not depend on the first list page', async ({ page }) => {
  const requests: string[] = [];
  page.on('request', request => requests.push(new URL(request.url()).pathname));
  await mockResetMarket(page, false);

  await page.goto(`/?view=overview&snapshot=${CURRENT}&date=2026-09-10`);

  await expect(page.getByRole('heading', { name: '市场总览' })).toBeVisible();
  expect(requests).toContain(`/api/snapshots/${CURRENT}`);
});

test('reset overview uses versioned APIs and persists drill-down context in URL', async ({ page }) => {
  const requests: string[] = [];
  page.on('request', request => {
    if (request.url().includes('/api/')) requests.push(new URL(request.url()).pathname);
  });
  await mockResetMarket(page);
  await page.goto(`/?view=overview&snapshot=${CURRENT}&date=2026-09-10`);

  await expect(page.getByRole('heading', { name: '市场总览' })).toBeVisible();
  await page.getByLabel('来源、覆盖率与版本详情').click();
  await expect(page.getByText('来源 synthetic-market / market-v1', { exact: true })).toBeVisible();
  await page.getByLabel('来源、覆盖率与版本详情').click();
  const members = page.getByRole('region', { name: '证券筛选结果' });
  await expect(members.getByText('全部证券 · 共 5 家', { exact: true })).toBeVisible();
  await expect(members.getByRole('list').getByRole('button')).toHaveText(['合成甲', '合成乙', '合成丙', '合成丁', '合成戊']);
  await members.getByRole('button', { name: '合成甲', exact: true }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.getByRole('button', { name: '关闭详情' }).click();

  await page.getByPlaceholder('输入证券代码或名称').fill('000002');
  await page.getByRole('button', { name: '搜索', exact: true }).click();
  await expect(page).toHaveURL(/q=000002/);
  await expect(members.getByText('全部证券 · 搜索“000002” · 共 1 家', { exact: true })).toBeVisible();
  await expect(members.getByRole('list').getByRole('button')).toHaveText(['合成乙']);
  await members.getByRole('button', { name: '合成乙', exact: true }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.getByRole('button', { name: '关闭详情' }).click();

  await page.getByRole('button', { name: '清除搜索', exact: true }).click();
  await page.getByRole('button', { name: '上涨 3' }).click();
  await expect(page).toHaveURL(/market_filter=up/);
  const upMembers = page.getByRole('region', { name: '证券筛选结果' });
  await expect(upMembers.getByText('共 3 家')).toBeVisible();
  await expect(upMembers.getByRole('list').getByRole('button')).toHaveText(['合成甲', '合成丙', '合成戊']);
  await expect(upMembers.getByText('000003')).toBeVisible();
  await upMembers.getByRole('button', { name: '合成丙', exact: true }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.getByRole('button', { name: '关闭详情' }).click();

  await page.getByRole('button', { name: '下跌 1' }).click();
  await expect(page).toHaveURL(/market_filter=down/);
  const downMembers = page.getByRole('region', { name: '证券筛选结果' });
  await expect(downMembers.getByText('共 1 家')).toBeVisible();
  await expect(downMembers.getByRole('button', { name: '合成乙', exact: true })).toBeVisible();
  await expect(downMembers.getByRole('button', { name: '合成甲', exact: true })).toHaveCount(0);

  await expect(page.getByText(/FAC-M01/)).toBeVisible();
  await expect(page.getByText('requires_complete_current_and_prior_20_sessions')).toBeVisible();
  await expect(page.getByText('requires_60_consecutive_index_closes')).toBeVisible();
  await expect(page.getByText('incomplete_touched_limit_coverage')).toBeVisible();
  await expect(page.getByText('partial_yesterday_limit_up_return_coverage')).toBeVisible();
  await expect(page.getByRole('region', { name: '题材热度 TOP' }).getByText('source_field_unavailable')).toBeVisible();
  await expect(page.getByText('涨停占比 1/3 · 33.33%')).toBeVisible();

  await page.getByRole('link', { name: /合成题材甲/ }).click();
  await page.getByRole('button', { name: '查看题材梯队', exact: true }).click();
  await expect(page).toHaveURL(/view=leaders/);
  await expect(page).toHaveURL(/sector=SEC001/);
  await expect(page.getByLabel('题材筛选')).toHaveValue('SEC001');
  expect(requests.some(path => path.startsWith('/api/v1/') && (path.endsWith('/overview') || path.endsWith('/securities')))).toBe(false);
});

test('heatmap date clears stale snapshot and historical topic control is disabled', async ({ page }) => {
  await mockResetMarket(page);
  await page.goto(`/?view=overview&snapshot=${CURRENT}&date=2026-09-10`);

  await expect(page.getByRole('button', { name: '历史题材分析' })).toBeDisabled();
  await expect(page.getByText('缺少历史题材成员')).toBeVisible();
  await page.getByRole('button', { name: '2026-09-09 2板 1家' }).click();
  await expect(page).toHaveURL(/date=2026-09-09/);
  await expect(page).toHaveURL(new RegExp(`snapshot=${PRIOR}`));
  await expect(page).not.toHaveURL(new RegExp(`snapshot=${CURRENT}`));
});

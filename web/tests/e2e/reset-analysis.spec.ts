import { expect, test, type Page, type Route } from '@playwright/test';

const ID = '00000000-0000-4000-8000-000000000013';
const sessions = [
  '2026-08-13', '2026-08-14', '2026-08-17', '2026-08-18', '2026-08-19',
  '2026-08-20', '2026-08-21', '2026-08-24', '2026-08-25', '2026-08-26',
  '2026-08-27', '2026-08-28', '2026-08-31', '2026-09-01', '2026-09-02',
  '2026-09-03', '2026-09-04', '2026-09-07', '2026-09-08', '2026-09-09',
  '2026-09-10',
];
const coverage = { expected: 3, valid: 2, excluded: 0, missing: 1 };

type MockState = {
  malformedRank?: boolean;
  wrongSnapshot?: boolean;
  qfqUnavailable?: boolean;
  closeOnly?: boolean;
};

function envelope(data: unknown, extra: Record<string, unknown> = {}) {
  return {
    trade_date: '2026-09-10',
    as_of: '2026-09-10T10:00:00Z',
    snapshot_id: ID,
    source: 'synthetic-analysis',
    source_version: 'analysis-source-v1',
    coverage,
    quality_flags: [],
    rule_version: 'analysis-v1',
    basis: { unit: 'ratio', currency: 'CNY', price_type: 'raw', benchmark: 'B000300' },
    data,
    ...extra,
  };
}

function summary() {
  return envelope({
    revision: 1,
    supersedes: null,
    published_at: '2026-09-10T10:05:00Z',
    security_count: 3,
    calendar_sessions: sessions,
    earliest_date: sessions[0],
  });
}

function rankData(start = sessions[0], end = sessions.at(-1)!) {
  return {
    window_days: 20,
    adjusted_start: start,
    adjusted_end: end,
    benchmark_return: 0.02,
    ranking: [
      {
        rank: 1, security_id: 'TEST001', name: '合成甲',
        topics: [{ topic_id: 'SEC001', name: '机器人' }],
        topic_status: 'available', topic_reasons: [], reasons: [],
        return_ratio: 0.1, excess_return_ratio: 0.08,
        max_drawdown_ratio: 0.25, amount_change_ratio: 0.2,
      },
      {
        rank: 2, security_id: 'TEST006', name: '合成己',
        topics: [], topic_status: 'available', topic_reasons: [], reasons: [],
        return_ratio: 0.01, excess_return_ratio: -0.01,
        max_drawdown_ratio: 0.02, amount_change_ratio: 0.03,
      },
    ],
    exclusions: [{ security_id: 'TEST002', reasons: ['missing_price'] }],
  };
}

function curves() {
  return {
    adjusted_start: sessions[0],
    adjusted_end: sessions.at(-1)!,
    benchmark: 'B000300',
    curves: ['TEST001', 'TEST002', 'TEST003', 'TEST004', 'TEST005'].map((securityId, index) => ({
      security_id: securityId,
      reasons: index === 0 ? ['missing_price'] : [],
      points: sessions.map((tradeDate, pointIndex) => ({
        trade_date: tradeDate,
        normalized: index === 0 && pointIndex === 8 ? null : 100 + pointIndex,
        drawdown: index === 0 && pointIndex === 8 ? null : pointIndex === 20 ? 0.25 : 0,
      })),
    })),
    exclusions: [],
  };
}

function stockData(securityId: string) {
  return {
    security_id: securityId,
    name: securityId === 'TEST002' ? '合成乙' : '合成甲',
    quote: { last: 11, change_ratio: 0.1, amount: 120000 },
    roles: [{ role_type: 'trend_leader', evidence_status: 'candidate', sector_id: 'SEC001' }],
    role_reason: 'role_partial',
    seal: {
      is_limit_up: true,
      touched_limit: true,
      first_seal_time: '09:35:00',
      last_seal_time: '14:55:00',
      break_count: 0,
      seal_amount: 210000000,
      seal_amount_unit: 'currency',
      continuous_boards: 2,
      reason: null,
    },
    regulation: {
      formal_event_count: null,
      event_source_completeness: 'unknown',
      reason: 'regulation_missing',
    },
  };
}

function klineData(securityId: string, closeOnly: boolean) {
  return {
    security_id: securityId,
    adjustment: 'raw',
    adjusted_start: sessions[0],
    adjusted_end: sessions.at(-1)!,
    chart_type: closeOnly ? 'line' : 'candlestick',
    points: sessions.map((tradeDate, index) => ({
      trade_date: tradeDate,
      open: closeOnly ? null : 10 + index,
      high: closeOnly ? null : 12 + index,
      low: closeOnly ? null : 9 + index,
      close: closeOnly && index === 8 ? null : 11 + index,
      volume: 10000,
      amount: 120000,
    })),
  };
}

async function mockAnalysis(page: Page, state: MockState = {}) {
  await page.route('**/api/**', async (route: Route) => {
    const url = new URL(route.request().url());
    const respond = (body: unknown, status = 200) => route.fulfill({ status, json: body });
    if (url.pathname === '/api/snapshots/list') {
      return respond(envelope({ snapshots: [summary()] }));
    }
    if (url.pathname === `/api/snapshots/${ID}`) return respond(summary());
    if (url.pathname === '/api/rank/range') {
      if (state.qfqUnavailable || url.searchParams.get('adj') !== 'raw') {
        return respond(envelope(null, {
          reason: 'qfq_semantics_unproven_from_generic_adjustment_factor',
          quality_flags: ['adjustment_unavailable'],
          basis: { unit: 'ratio', currency: 'CNY', price_type: url.searchParams.get('adj'), benchmark: url.searchParams.get('benchmark') || 'B000300' },
        }));
      }
      if (state.malformedRank) return respond(envelope({ ...rankData(), ranking: 'dirty payload' }));
      const custom = url.searchParams.get('start') === '2026-08-16';
      return respond({
        ...envelope(rankData(custom ? '2026-08-17' : sessions[0])),
        snapshot_id: state.wrongSnapshot ? '00000000-0000-4000-8000-000000000099' : ID,
      });
    }
    if (url.pathname === '/api/compare/curves') return respond(envelope(curves()));
    if (/^\/api\/stock\/[^/]+\/kline$/.test(url.pathname)) {
      const securityId = decodeURIComponent(url.pathname.split('/')[3]);
      return respond(envelope(klineData(securityId, state.closeOnly || securityId === 'TEST002')));
    }
    if (/^\/api\/stock\/[^/]+$/.test(url.pathname)) {
      const securityId = decodeURIComponent(url.pathname.split('/')[3]);
      return respond(envelope(stockData(securityId)));
    }
    return respond({}, 404);
  });
}

const analysisUrl = `/?view=analysis&snapshot=${ID}&date=2026-09-10`;

test('20日排行展示基准、收益、回撤、成交额、题材、覆盖和排除', async ({ page }) => {
  const rankRequests: URL[] = [];
  page.on('request', request => {
    const url = new URL(request.url());
    if (url.pathname === '/api/rank/range') rankRequests.push(url);
  });
  await mockAnalysis(page);
  await page.goto(analysisUrl);

  const table = page.getByRole('table', { name: '涨幅排行' });
  await expect(table.getByRole('row', { name: /1.*TEST001.*合成甲.*机器人.*10%.*8%.*25%.*20%/ })).toBeVisible();
  await expect(table.getByText('TEST002')).toHaveCount(0);
  await expect(page.getByText('基准收益 +2%')).toBeVisible();
  await expect(page.getByText('有效 2 / 应有 3 · 排除 0 · 缺失 1')).toBeVisible();
  await page.getByText('查看排除原因（1）').click();
  const exclusions = page.getByRole('table', { name: '排除原因' });
  await expect(exclusions.getByRole('row', { name: /TEST002.*价格缺失/ })).toBeVisible();
  expect(rankRequests).toHaveLength(1);
  expect(rankRequests[0].searchParams.get('start')).toBe(sessions[0]);
  expect(rankRequests[0].searchParams.get('end')).toBe(sessions.at(-1));
  expect(rankRequests[0].searchParams.get('snapshot_id')).toBe(ID);
  expect(rankRequests[0].searchParams.get('benchmark')).toBe('B000300');
  expect(rankRequests[0].searchParams.get('adj')).toBe('raw');
});

test('基准与复权写入URL，且不可用复权不伪装为raw', async ({ page }) => {
  const state: MockState = {};
  await mockAnalysis(page, state);
  await page.goto(analysisUrl);
  await expect(page.getByRole('table', { name: '涨幅排行' })).toBeVisible();

  await page.getByLabel('基准指数').selectOption('B000905');
  await expect(page).toHaveURL(/benchmark=B000905/);
  state.qfqUnavailable = true;
  await page.getByLabel('复权方式').selectOption('qfq');
  await expect(page).toHaveURL(/adj=qfq/);
  await expect(page.getByText('当前来源无法证明该复权口径', { exact: true })).toBeVisible();
  await expect(page.getByRole('table', { name: '涨幅排行' })).toHaveCount(0);
});

test('最多比较五个标的，第六个不发请求，并切换归一化与回撤缺口', async ({ page }) => {
  const comparisonSymbols: string[] = [];
  page.on('request', request => {
    const url = new URL(request.url());
    if (url.pathname === '/api/compare/curves') comparisonSymbols.push(url.searchParams.get('symbols') || '');
  });
  await mockAnalysis(page);
  await page.goto(`${analysisUrl}&compare=TEST001,TEST002,TEST003,TEST004,TEST005`);
  await expect(page.getByRole('img', { name: /归一化.*缺失处断开/ })).toBeVisible();
  const points = page.getByRole('table', { name: '比较曲线精确值' });
  await expect(points.getByRole('row', { name: /2026-08-25.*—/ })).toBeVisible();

  await page.getByRole('radio', { name: '回撤' }).check();
  await expect(page.getByRole('img', { name: /回撤.*缺失处断开/ })).toBeVisible();
  await expect(points.getByRole('row', { name: /2026-09-10.*25%/ })).toBeVisible();

  await page.getByRole('checkbox', { name: '比较 TEST006' })
    .evaluate((checkbox: HTMLInputElement) => checkbox.click());
  await expect(page.getByRole('alert')).toContainText('最多比较 5 个标的');
  expect(comparisonSymbols.some(value => value.includes('TEST006'))).toBe(false);
});

test('自定义休市区间展示后端调整结果', async ({ page }) => {
  await mockAnalysis(page);
  await page.goto(analysisUrl);
  await page.getByLabel('区间方式').selectOption('custom');
  await page.getByLabel('起始日期').fill('2026-08-16');
  await page.getByLabel('结束日期').fill('2026-09-10');
  await page.getByRole('button', { name: '应用区间' }).click();

  await expect(page).toHaveURL(/start=2026-08-16/);
  await expect(page.getByText('实际区间：2026-08-17 至 2026-09-10')).toBeVisible();
});

test('详情使用同快照raw K线，并展示完整角色、封板和未知监管证据', async ({ page }) => {
  const klineRequests: URL[] = [];
  page.on('request', request => {
    const url = new URL(request.url());
    if (url.pathname.endsWith('/kline')) klineRequests.push(url);
  });
  await mockAnalysis(page);
  await page.goto(`${analysisUrl}&q=TEST001&sort=amount&order=desc`);
  await page.getByRole('button', { name: 'TEST001', exact: true }).click();

  const drawer = page.getByRole('dialog');
  await expect(drawer.getByRole('img', { name: /历史日线图.*缺失处断开/ })).toBeVisible();
  await expect(drawer.getByText('趋势龙头：正式候选')).toBeVisible();
  await expect(drawer.getByText('角色状态：部分可用')).toBeVisible();
  await expect(drawer.getByText('收盘涨停 是 · 盘中触及 是')).toBeVisible();
  await expect(drawer.getByText('首封 09:35:00 · 末封 14:55:00 · 开板 0 · 封单 210,000,000 货币 · 连板 2')).toBeVisible();
  await expect(drawer.getByText('正式监管事件：未知')).toBeVisible();
  await expect(drawer.getByText('事件来源完整性：未知 · 未识别状态')).toBeVisible();
  await expect(drawer).not.toContainText('正式监管事件：0');
  await expect(drawer).not.toContainText(/安全|无风险/);
  expect(klineRequests[0].searchParams.get('snapshot_id')).toBe(ID);
  expect(klineRequests[0].searchParams.get('adj')).toBe('raw');

  await drawer.getByRole('link', { name: '打开完整详情' }).click();
  await expect(page).toHaveURL(/view=stock/);
  await page.getByRole('link', { name: '返回研究列表' }).click();
  await expect(page).toHaveURL(/view=analysis/);
  await expect(page).toHaveURL(/q=TEST001/);
  await expect(page).toHaveURL(/sort=amount/);
  await expect(page).toHaveURL(/order=desc/);
});

test('close-only接口退化为收盘线，375px记录布局无页面横向溢出', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await mockAnalysis(page, { closeOnly: true });
  await page.goto(analysisUrl);
  await expect(page.getByRole('table', { name: '涨幅排行' })).toHaveClass(/responsive-record-table/);
  const firstRow = page.getByRole('table', { name: '涨幅排行' }).locator('tbody tr').first();
  await expect(firstRow.locator('[data-label="题材"]')).toContainText('机器人');
  await page.getByRole('button', { name: 'TEST001', exact: true }).click();
  await expect(page.getByRole('dialog').getByRole('img', { name: /历史收盘价线图.*缺失处断开/ })).toBeVisible();
  const dimensions = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    page: document.documentElement.scrollWidth,
  }));
  expect(dimensions.page).toBeLessThanOrEqual(dimensions.viewport);
});

test('错误快照和畸形排行响应都清除旧数据且不泄漏payload', async ({ page }) => {
  const state: MockState = {};
  await mockAnalysis(page, state);
  await page.goto(analysisUrl);
  await expect(page.getByRole('table', { name: '涨幅排行' })).toBeVisible();

  state.wrongSnapshot = true;
  await page.getByRole('button', { name: '刷新快照' }).click();
  await expect(page.getByRole('alert')).toContainText('响应快照与当前选择不匹配');
  await expect(page.getByRole('table', { name: '涨幅排行' })).toHaveCount(0);

  state.wrongSnapshot = false;
  state.malformedRank = true;
  await page.getByRole('button', { name: /重试涨幅排行/ }).click();
  await expect(page.getByRole('alert')).toContainText('涨幅排行响应格式无效');
  await expect(page.getByText('dirty payload')).toHaveCount(0);
  await expect(page.getByRole('table', { name: '涨幅排行' })).toHaveCount(0);
});

import { expect, test, type Page, type Route, type Locator } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const CURRENT = '00000000-0000-4000-8000-000000000091';
const PRIOR = '00000000-0000-4000-8000-000000000090';
const CURRENT_DATE = '2026-09-10';
const PRIOR_DATE = '2026-09-09';
const METHOD = 'sentiment-research-v1';

const coverage = { expected: 5, valid: 5, excluded: 0, missing: 0 };
const basis = { unit: 'ratio', currency: 'CNY', price_type: 'raw', benchmark: null };
const weight = { numerator: 1, denominator: 3 };
const REASON_NO_LIMIT_EVENTS = 'no_limit_events';

type Coverage = typeof coverage;

function envelope(snapshotId: string, tradeDate: string, data: unknown, qualityFlags: string[] = []) {
  return {
    trade_date: tradeDate,
    as_of: `${tradeDate}T10:00:00Z`,
    snapshot_id: snapshotId,
    source: 'synthetic-market',
    source_version: 'market-v1',
    coverage,
    quality_flags: qualityFlags,
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

function ratioComponent(numerator: number, denominator: number, cover?: Coverage) {
  const component: Record<string, unknown> = {
    value: Number((numerator / denominator).toFixed(4)),
    numerator,
    denominator,
    weight: { ...weight },
    reason: null,
  };
  if (cover) component.coverage = { ...cover };
  return component;
}

function missingComponent(reason: string) {
  return { value: null, numerator: 0, denominator: 0, weight: { ...weight }, reason };
}

function point(
  tradeDate: string,
  score: number | null,
  phase: string | null,
  components: Record<string, unknown>,
  reasons: string[] = [],
) {
  return { trade_date: tradeDate, score, phase, components, reasons };
}

// Frozen anchor: b=3/5 l=2/2 p=1/2 -> 70.00/repair; prior day 83.33/climax.
function currentBlock(): Record<string, unknown> {
  const firstComponents = {
    breadth: ratioComponent(1, 2, { expected: 2, valid: 2, excluded: 0, missing: 0 }),
    limit_balance: ratioComponent(1, 1),
    promotion: ratioComponent(1, 1, { expected: 1, valid: 1, excluded: 0, missing: 0 }),
  };
  const headlineComponents = {
    breadth: ratioComponent(3, 5, { expected: 5, valid: 5, excluded: 0, missing: 0 }),
    limit_balance: ratioComponent(2, 2),
    promotion: ratioComponent(1, 2, { expected: 2, valid: 2, excluded: 0, missing: 0 }),
  };
  return {
    method_version: METHOD,
    research_only: true,
    trade_date: CURRENT_DATE,
    score: 70.0,
    phase: 'repair',
    components: headlineComponents,
    reasons: [],
    series: [
      point(PRIOR_DATE, 83.33, 'climax', firstComponents),
      point(CURRENT_DATE, 70.0, 'repair', headlineComponents),
    ],
  };
}

// Frozen fixture block: b=1/3 l=1/2 p=1/2 -> 44.44/divergent.
function priorBlock(): Record<string, unknown> {
  const components = {
    breadth: ratioComponent(1, 3),
    limit_balance: ratioComponent(1, 2),
    promotion: ratioComponent(1, 2),
  };
  return {
    method_version: METHOD,
    research_only: true,
    trade_date: PRIOR_DATE,
    score: 44.44,
    phase: 'divergent',
    components,
    reasons: [],
    series: [point(PRIOR_DATE, 44.44, 'divergent', components)],
  };
}

function incompleteBlock(): Record<string, unknown> {
  const components = {
    breadth: ratioComponent(3, 5, { expected: 5, valid: 5, excluded: 0, missing: 0 }),
    limit_balance: missingComponent(REASON_NO_LIMIT_EVENTS),
    promotion: ratioComponent(1, 2, { expected: 2, valid: 2, excluded: 0, missing: 0 }),
  };
  return {
    method_version: METHOD,
    research_only: true,
    trade_date: CURRENT_DATE,
    score: null,
    phase: null,
    components,
    reasons: [REASON_NO_LIMIT_EVENTS],
    series: [point(CURRENT_DATE, null, null, components, [REASON_NO_LIMIT_EVENTS])],
  };
}

function malformedBlock(): Record<string, unknown> {
  const block = currentBlock();
  // Phase still buckets to repair; only the recomputed score (70.00) drifts.
  block.score = 70.5;
  (block.series as Record<string, unknown>[])[1].score = 70.5;
  return block;
}

type Variant = 'anchor' | 'incomplete' | 'null' | 'malformed';

async function mockResetResearch(page: Page, variant: Variant = 'anchor') {
  await page.route('**/api/**', async (route: Route) => {
    const url = new URL(route.request().url());
    const respond = (body: unknown) => route.fulfill({ json: body });
    if (url.pathname === '/api/snapshots/list') {
      const day = url.searchParams.get('date');
      const items = day === PRIOR_DATE
        ? [summary(PRIOR, PRIOR_DATE)]
        : [summary(CURRENT, CURRENT_DATE)];
      return respond(envelope(items[0].snapshot_id, items[0].trade_date, { snapshots: items }));
    }
    if (url.pathname === `/api/snapshots/${CURRENT}`) {
      return respond(summary(CURRENT, CURRENT_DATE));
    }
    if (url.pathname === `/api/snapshots/${PRIOR}`) {
      return respond(summary(PRIOR, PRIOR_DATE));
    }
    if (url.pathname === '/api/market/sentiment/research') {
      if (url.searchParams.get('snapshot_id') === PRIOR) {
        return respond(envelope(PRIOR, PRIOR_DATE, priorBlock()));
      }
      if (variant === 'null') {
        return respond(envelope(CURRENT, CURRENT_DATE, null, ['materialization_unavailable']));
      }
      if (variant === 'incomplete') {
        return respond(envelope(CURRENT, CURRENT_DATE, incompleteBlock()));
      }
      if (variant === 'malformed') {
        return respond(envelope(CURRENT, CURRENT_DATE, malformedBlock()));
      }
      return respond(envelope(CURRENT, CURRENT_DATE, currentBlock()));
    }
    const selected = url.searchParams.get('snapshot_id') === PRIOR
      ? [PRIOR, PRIOR_DATE] : [CURRENT, CURRENT_DATE];
    if (url.pathname === '/api/market/overview') {
      return respond(envelope(selected[0], selected[1], {
        indexes: [],
        breadth: {
          up: 3, down: 1, flat: 1, valid_total: 5,
          coverage,
          members: {
            up: [
              { security_id: 'E', code: '000005', name: '合成戊' },
              { security_id: 'A', code: '000001', name: '合成甲' },
              { security_id: 'C', code: '000003', name: '合成丙' },
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
        hot_sectors: [],
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
            indexes: [],
            method: 'close_over_ma20_ma60_and_20_return_sample_stddev_non_annualized',
          },
        },
        historical_topic_analysis: { enabled: false, reason: 'historical_topic_coverage_unavailable' },
      }));
    }
    if (url.pathname === '/api/market/heatmap') {
      return respond(envelope(selected[0], selected[1], { columns: [1, 2], cells: [] }));
    }
    if (url.pathname === '/api/market/sector-flow') {
      return respond(envelope(selected[0], selected[1], { sectors: [] }));
    }
    if (url.pathname === '/api/market/sentiment') {
      return respond(envelope(selected[0], selected[1], { series: [] }));
    }
    return route.fulfill({ status: 404, json: {} });
  });
}

const overviewUrl = `/?view=overview&snapshot=${CURRENT}&date=${CURRENT_DATE}`;

function researchSection(page: Page): Locator {
  return page.locator('section[aria-label="情绪研究分区"]');
}

function trackResearchRequests(page: Page): URL[] {
  const requests: URL[] = [];
  page.on('request', request => {
    const url = new URL(request.url());
    if (url.pathname === '/api/market/sentiment/research') requests.push(url);
  });
  return requests;
}

test('research section stays independent of the raw sentiment observations', async ({ page }) => {
  await mockResetResearch(page);
  await page.goto(overviewUrl);

  await expect(page.getByRole('heading', { name: '市场总览' })).toBeVisible();
  const section = researchSection(page);
  await expect(section.getByRole('heading', { name: '情绪研究分区' })).toBeVisible();
  await expect(section.getByText('产品研究假设 · 非官方指标', { exact: true })).toBeVisible();
  await expect(section.getByText('仅研究', { exact: true })).toBeVisible();

  // Headline score, phase, three components and frozen formula.
  await expect(section.locator('.research-score-value')).toHaveText('70.00');
  await expect(section.locator('.research-phase')).toHaveText('修复');
  await expect(section.getByRole('heading', { name: '市场广度', level: 3 })).toBeVisible();
  await expect(section.getByRole('heading', { name: '涨跌停平衡', level: 3 })).toBeVisible();
  await expect(section.getByRole('heading', { name: '高位晋级', level: 3 })).toBeVisible();
  await expect(section.getByText(/研究分 = 100 ×/)).toBeVisible();
  await expect(section.getByText(new RegExp(METHOD))).toBeVisible();

  // Series opens newest-first with both published days.
  await section.getByText('近 2 个交易日研究序列').click();
  const rows = section.getByRole('table', { name: '情绪研究序列' }).locator('tbody tr');
  await expect(rows).toHaveCount(2);
  await expect(rows.nth(0)).toContainText(CURRENT_DATE);
  await expect(rows.nth(0)).toContainText('70.00');
  await expect(rows.nth(0)).toContainText('修复');
  await expect(rows.nth(1)).toContainText(PRIOR_DATE);
  await expect(rows.nth(1)).toContainText('83.33');
  await expect(rows.nth(1)).toContainText('高潮');

  // The original observation area never carries the research score.
  const observations = page.locator('section[aria-label="涨跌与情绪"]');
  await expect(observations).toBeVisible();
  expect(await observations.textContent()).not.toContain('70.00');
  expect(await observations.textContent()).not.toContain('研究分');
  await expect(page.getByText('不含研究评分与阶段判断', { exact: true })).toBeVisible();
});

test('reports the missing-component day honestly without zero fill', async ({ page }) => {
  await mockResetResearch(page, 'incomplete');
  await page.goto(overviewUrl);

  const section = researchSection(page);
  await expect(section.locator('.research-score-value')).toHaveText('不可评分');
  await expect(section.locator('.research-score')).toHaveAttribute('aria-label', '当日研究分不可用');
  await expect(section.locator('.research-phase')).toHaveText('当日分项不完整');

  const limitCard = section.locator('.research-component', {
    has: page.getByRole('heading', { name: '涨跌停平衡', level: 3 }),
  });
  await expect(limitCard.getByText('不可评分 · 缺少涨跌停事件')).toBeVisible();
  await expect(limitCard.getByText('0.00%')).toHaveCount(0);

  const reason = section.getByRole('status').filter({ hasText: '当日不出分原因' });
  await expect(reason).toBeVisible();
  await expect(reason).toContainText('不补零、不重分配权重');

  await section.getByText('近 1 个交易日研究序列').click();
  const row = section.getByRole('table', { name: '情绪研究序列' }).locator('tbody tr');
  await expect(row).toHaveCount(1);
  await expect(row.nth(0).locator('td.numeric')).toHaveText('—');
  await expect(row.nth(0)).toContainText('缺少涨跌停事件');
});

test('fail-closes a malformed block without rendering half-values', async ({ page }) => {
  await mockResetResearch(page, 'malformed');
  await page.goto(overviewUrl);

  const section = researchSection(page);
  await expect(section.getByRole('heading', { name: '情绪研究分区' })).toBeVisible();
  await expect(
    section.getByRole('alert').filter({ hasText: '情绪研究分区响应格式无效' }),
  ).toBeVisible();
  await expect(section.locator('.research-score')).toHaveCount(0);
  await expect(section.getByRole('heading', { name: '市场广度', level: 3 })).toHaveCount(0);
  await expect(section.getByRole('table', { name: '情绪研究序列' })).toHaveCount(0);
  // The rest of the published overview stays available.
  await expect(page.getByRole('heading', { name: '市场总览' })).toBeVisible();
});

test('refetches the research block bound to snapshot and date switches', async ({ page }) => {
  const requests = trackResearchRequests(page);
  await mockResetResearch(page);
  await page.goto(overviewUrl);

  const section = researchSection(page);
  await expect(section.locator('.research-score-value')).toHaveText('70.00');

  await page.fill('#trade-date-input', PRIOR_DATE);
  await expect(page).toHaveURL(new RegExp(`date=${PRIOR_DATE}`));
  await expect(page).toHaveURL(new RegExp(`snapshot=${PRIOR}`));

  await expect(section.locator('.research-score-value')).toHaveText('44.44');
  await expect(section.locator('.research-phase')).toHaveText('分歧');
  expect(await section.textContent()).not.toContain('70.00');

  expect(requests.some(url =>
    url.searchParams.get('snapshot_id') === CURRENT
    && url.searchParams.get('date') === CURRENT_DATE)).toBeTruthy();
  expect(requests.some(url =>
    url.searchParams.get('snapshot_id') === PRIOR
    && url.searchParams.get('date') === PRIOR_DATE)).toBeTruthy();
  expect(requests.every(url => url.searchParams.get('days') === '20')).toBeTruthy();
});

test('exposes the two-layer unavailable state for legacy publications', async ({ page }) => {
  await mockResetResearch(page, 'null');
  await page.goto(overviewUrl);

  const section = researchSection(page);
  const notice = section.getByRole('status')
    .filter({ hasText: '该快照未物化情绪研究分区' });
  await expect(notice).toBeVisible();
  await expect(notice).toContainText('读取侧不重算');
  await expect(notice).toContainText('materialization_unavailable');
  await expect(section.locator('.research-score')).toHaveCount(0);
  await expect(section.getByRole('table', { name: '情绪研究序列' })).toHaveCount(0);
});

test('toggles the research series by keyboard', async ({ page }) => {
  await mockResetResearch(page);
  await page.goto(overviewUrl);

  const section = researchSection(page);
  const summary = section.locator('summary');
  await expect(summary).toBeVisible();
  const table = section.getByRole('table', { name: '情绪研究序列' });

  await summary.focus();
  await page.keyboard.press('Enter');
  await expect(table).toBeVisible();

  await page.keyboard.press('Enter');
  await expect(table).toBeHidden();
});

test('is axe-clean without horizontal overflow at 375px', async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on('console', message => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });
  await page.setViewportSize({ width: 375, height: 812 });
  await page.emulateMedia({ reducedMotion: 'reduce' });

  await mockResetResearch(page);
  await page.goto(overviewUrl);

  const section = researchSection(page);
  await expect(section.locator('.research-score-value')).toHaveText('70.00');
  await section.locator('summary').click();
  await expect(section.getByRole('table', { name: '情绪研究序列' })).toBeVisible();

  const sectionAxe = await new AxeBuilder({ page }).include('.research-panel').analyze();
  expect(sectionAxe.violations).toEqual([]);
  const pageAxe = await new AxeBuilder({ page }).analyze();
  expect(pageAxe.violations).toEqual([]);

  const metrics = await page.evaluate(() => {
    const panel = document.querySelector('.research-panel') as HTMLElement | null;
    return {
      pageFits: document.documentElement.scrollWidth <= document.documentElement.clientWidth,
      sectionFits: panel ? panel.scrollWidth <= panel.clientWidth : false,
    };
  });
  expect(metrics).toEqual({ pageFits: true, sectionFits: true });
  expect(consoleErrors).toEqual([]);
});

test('uses no promissory or forecast-style copy in the research section', async ({ page }) => {
  await mockResetResearch(page);
  await page.goto(overviewUrl);

  const section = researchSection(page);
  await expect(section.getByText('仅研究', { exact: true })).toBeVisible();
  const text = await section.textContent();
  for (const banned of ['胜率', '未来上涨概率', '必涨', '稳赚', '安全', '预测', '见顶', '见底']) {
    expect(text, `research section must not contain ${banned}`).not.toContain(banned);
  }
});

import { expect, test, type Page } from '@playwright/test';

const PUB = '00000000-0000-4000-8000-000000000071';
const JOB = '00000000-0000-4000-8000-000000000072';
const OWNER = '00000000-0000-4000-8000-000000000073';
const SESSION = '00000000-0000-4000-8000-000000000074';
const OTHER_PUB = '00000000-0000-4000-8000-000000000075';
const sessions = [
  '2026-08-13', '2026-08-14', '2026-08-17', '2026-08-18', '2026-08-19',
  '2026-08-20', '2026-08-21', '2026-08-24', '2026-08-25', '2026-08-26',
  '2026-08-27', '2026-08-28', '2026-08-31', '2026-09-01', '2026-09-02',
  '2026-09-03', '2026-09-04', '2026-09-07', '2026-09-08', '2026-09-09', '2026-09-10',
];
const row = {
  rank: 1, security_id: 'A', name: '合成分析甲', topics: [],
  topic_status: 'unavailable', topic_reasons: ['topic_batch_unavailable'],
  return_ratio: 0.1, excess_return_ratio: null, max_drawdown_ratio: 0,
  amount_change_ratio: null, reasons: ['benchmark_identity_invalid'],
};
function envelope(data: unknown, extra: Record<string, unknown> = {}) {
  return {
    trade_date: '2026-09-10', as_of: '2026-09-10T10:00:00Z', snapshot_id: PUB,
    source: 'synthetic', source_version: 's1',
    coverage: { expected: 1, valid: 1, excluded: 0, missing: 0 },
    quality_flags: [], rule_version: 'analysis-v1',
    basis: { unit: 'ratio', currency: 'CNY', price_type: 'raw', benchmark: 'B000300' },
    data, ...extra,
  };
}
function rank() {
  return envelope({
    window_days: 20, adjusted_start: sessions[0], adjusted_end: sessions.at(-1),
    benchmark_return: null, ranking: [row], exclusions: [],
  });
}
type Scene = {
  cached: boolean; late: boolean; complete: boolean; denied: boolean;
  posts: Record<string, unknown>[]; polls: number; rankReads: number;
  privateReads: string[]; release: () => void;
  uncertain: boolean; principalChanged: boolean; latePoll: boolean;
  releasePoll: () => void; comparisonReads: number;
  sidecarReads: number; lineageReads: number;
  sidecarPaths: string[];
};
async function scene(page: Page, options: Partial<Scene> = {}) {
  const value: Scene = {
    cached: false, late: false, complete: false, denied: false,
    posts: [], polls: 0, rankReads: 0, privateReads: [], release: () => {},
    uncertain: false, principalChanged: false, latePoll: false,
    releasePoll: () => {}, comparisonReads: 0, sidecarReads: 0, lineageReads: 0, sidecarPaths: [], ...options,
  };
  const gate = new Promise<void>(resolve => { value.release = resolve; });
  const pollGate = new Promise<void>(resolve => { value.releasePoll = resolve; });
  const summary = envelope({
    revision: 1, supersedes: null, published_at: '2026-09-10T10:05:00Z',
    security_count: 1, calendar_sessions: sessions, earliest_date: sessions[0],
  });
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    const json = (data: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
    if (path.startsWith('/api/analysis/jobs') || path === '/api/owner/v1/session') value.privateReads.push(path);
    if (path === '/api/snapshots/list') {
      if (value.late) await page.evaluate(() => {
        const native = window.setTimeout.bind(window);
        window.setTimeout = ((handler: TimerHandler, delay?: number, ...args: unknown[]) => {
          if (delay === 5000) {
            (window as unknown as { handoffDeadline: number }).handoffDeadline = Date.now() + delay;
          }
          return native(handler, delay, ...args);
        }) as typeof window.setTimeout;
      });
      return json(envelope({ snapshots: [summary, { ...summary, snapshot_id: OTHER_PUB }] }));
    }
    if (path === `/api/snapshots/${PUB}`) return json(summary);
    if (path === `/api/snapshots/${OTHER_PUB}`) return json({ ...summary, snapshot_id: OTHER_PUB });
    if (path === '/api/snapshots/lineage') {
      value.lineageReads++;
      const at = '2026-09-10T10:05:00Z';
      const cell = { state: 'known', at, first_at: at, last_at: at, reason: null };
      return json({
        schema: 'dragon-radar.publication-clocks/v1', snapshot_id: PUB, trade_date: '2026-09-10',
        domains: Object.fromEntries(['market', 'topic', 'news', 'regulation', 'role'].map(domain => [
          domain, { status: 'ready', input_id: PUB, as_of: at, clocks: {
            source_event_at: cell, provider_published_at: cell, first_received_at: cell,
            computed_at: cell, snapshot_published_at: cell,
            page_updated_at: { state: 'not_applicable', at: null, first_at: null, last_at: null, reason: 'browser_clock' },
          } },
        ])),
      });
    }
    if (path === '/api/market/daily-ai' || path === '/api/market/daily-ai/archives') {
      value.sidecarReads++;
      value.sidecarPaths.push(path);
      const meta = {
        archive_id: JOB, archive_version: 1, archive_published_at: '2026-09-10T10:10:00Z',
        publication_sequence: 2, content_hash: 'a'.repeat(64), attestation_reference: 'synthetic-proof-only',
      };
      if (path.endsWith('/archives')) return json({
        snapshot_id: PUB, trade_date: '2026-09-10', base_published_at: '2026-09-10T10:05:00Z',
        publication_sequence_watermark: 2, items: [meta], total: 1, next_cursor: null,
      });
      return json(envelope({
        ...meta, schema: 'dragon-radar.public-daily-ai/v1',
        payload: {
          facts: [], inferences: [], citations: [],
          missing_evidence: [{ text: '合成日报证据缺失项', reason: 'synthetic_only' }],
        },
      }, {
        base_published_at: '2026-09-10T10:05:00Z',
        archive_published_at: meta.archive_published_at, archive_version: 1,
      }));
    }
    if (path === '/api/owner/v1/session') return json({
      owner_id: value.principalChanged ? JOB : OWNER, session_id: SESSION, expires_at: '2099-01-01T00:00:00Z',
    });
    if (path === '/api/analysis/jobs' && route.request().method() === 'POST') {
      value.posts.push(route.request().postDataJSON());
      if (value.uncertain && value.posts.length === 1) return route.abort('failed');
      return json({ job_id: JOB, status: 'queued' });
    }
    if (path === `/api/analysis/jobs/${JOB}`) {
      value.polls++;
      if (value.latePoll) await pollGate;
      if (value.denied) return json({ code: 'FORBIDDEN' }, 403);
      const command = value.posts[0];
      const input = {
        snapshot_id: PUB, start: sessions[0], end: sessions.at(-1), benchmark: 'B000300',
        adj: 'raw', symbols: command?.symbols || [], algorithm_version: 'analysis-custom-v1-decimal34',
        adjustment_version: 'a1',
      };
      const result = value.complete ? {
        schema: 'dragon-radar.custom-analysis', schema_version: 1,
        algorithm_version: input.algorithm_version, snapshot_id: PUB,
        requested_start: input.start, requested_end: input.end,
        adjusted_start: input.start, adjusted_end: input.end,
        benchmark: input.benchmark, adjustment: input.adj,
        eligible_security_ids: ['A'], available: true, reason: null,
        ranking: [row], exclusions: [], benchmark_return_ratio: null,
        benchmark_reasons: ['benchmark_identity_invalid'],
        curves: (input.symbols as string[]).map(security_id => ({
          security_id, points: sessions.map(trade_date => ({ trade_date, normalized: 100, drawdown: 0 })), reasons: [],
        })),
      } : null;
      if (value.complete) value.cached = true;
      return json({ job_id: JOB, status: value.complete ? 'succeeded' : value.polls === 1 ? 'queued' : 'running',
        result, failure: null, input });
    }
    if (path === '/api/rank/range') {
      value.rankReads++;
      if (value.late && value.rankReads === 1) {
        await gate;
        await json(rank()).catch(() => {}); return;
      }
      return json(value.cached ? rank() : envelope(null, { reason: 'analysis_not_precomputed' }));
    }
    if (path === '/api/compare/curves') {
      value.comparisonReads++;
      return json(value.cached ? envelope({
        adjusted_start: sessions[0], adjusted_end: sessions.at(-1),
        benchmark: 'B000300', exclusions: [],
        curves: [{ security_id: 'A', points: sessions.map(trade_date => ({
          trade_date, normalized: 100, drawdown: 0,
        })), reasons: [] }],
      }) : envelope(null, { reason: 'analysis_not_precomputed' }));
    }
    // Adjacent independent modules stay explicitly unavailable in this bounded scene.
    return json(envelope(null, { reason: 'materialization_unavailable' }));
  });
  return value;
}
const url = `/?view=analysis&snapshot=${PUB}&date=2026-09-10&n=20&adj=raw`;

test('fast authorized archive read queues no job', async ({ page }) => {
  const value = await scene(page, { cached: true });
  await page.goto(url);
  await expect(page.getByRole('table', { name: '涨幅排行' })).toBeVisible();
  expect(value.posts).toEqual([]);
  expect(value.privateReads.filter(path => path.startsWith('/api/analysis/jobs'))).toEqual([]);
});

test('five-second delayed read requires explicit consent and discards late success', async ({ page }, info) => {
  await page.clock.install({ time: new Date('2026-09-10T11:00:00Z') });
  const value = await scene(page, { late: true });
  await page.goto(url);
  await expect.poll(() => value.rankReads).toBe(1);
  // Start-up remains live for React lazy scheduling. Measure the actual armed
  // read-handoff timer after pausing, rather than treating page load as t=0.
  await page.clock.pauseAt(await page.evaluate(() => Date.now() + 50));
  const remaining = await page.evaluate(() =>
    (window as unknown as { handoffDeadline: number }).handoffDeadline - Date.now());
  expect(remaining).toBeGreaterThan(1);
  await page.clock.runFor(remaining - 1);
  await expect(page.getByText('归档读取超过五秒，旧请求已失效。')).toHaveCount(0);
  await page.clock.runFor(1);
  await expect(page.getByText('归档读取超过五秒，旧请求已失效。')).toBeVisible();
  value.release();
  await page.clock.runFor(1);
  await expect(page.getByRole('table', { name: '涨幅排行' })).toHaveCount(0);
  expect(value.posts).toEqual([]);
  await page.clock.resume();
  if (info.project.name === 'analysis-public') {
    expect(value.privateReads).toEqual([]);
    await expect(page.getByRole('button', { name: '运行自定义分析' })).toHaveCount(0);
  } else {
    await expect(page.getByRole('button', { name: '运行自定义分析' })).toBeEnabled();
  }
});

test('[owner] explicitly submits, polls a slow job, and reads authoritative cache', async ({ page }) => {
  const value = await scene(page);
  await page.goto(url);
  await expect(page.getByRole('button', { name: '运行自定义分析' })).toBeEnabled();
  await page.getByRole('button', { name: '运行自定义分析' }).click();
  await expect.poll(() => value.polls).toBeGreaterThanOrEqual(1);
  await expect(page.getByRole('button', { name: '使用原命令重试' })).toBeDisabled();
  await expect(page.getByText(/正在计算冻结输入/)).toBeVisible();
  expect(Object.keys(value.posts[0]).sort()).toEqual([
    'snapshot_id', 'start', 'end', 'benchmark', 'adj', 'symbols', 'idempotency_key',
  ].sort());
  expect(value.posts[0].symbols).toEqual([]);
  await page.clock.install();
  await page.clock.runFor(5001);
  expect(value.posts).toHaveLength(1);
  value.complete = true;
  await page.clock.runFor(1001);
  await expect(page.getByRole('table', { name: '涨幅排行' })).toBeVisible();
  expect(value.rankReads).toBeGreaterThanOrEqual(2);
  expect(value.posts).toHaveLength(1);
});

test('[public] cache miss sends no owner or job request', async ({ page }) => {
  const value = await scene(page);
  await page.goto(url);
  await expect(page.getByText('当前区间分析不可用', { exact: true })).toBeVisible();
  expect(value.privateReads).toEqual([]);
  expect(value.posts).toEqual([]);
});

test('[owner] unknown submit result retries the identical frozen command and key', async ({ page }) => {
  const value = await scene(page, { uncertain: true });
  await page.goto(url);
  await page.getByRole('button', { name: '运行自定义分析' }).click();
  await expect(page.getByRole('button', { name: '使用原命令重试' })).toBeEnabled();
  expect(value.posts).toHaveLength(1);
  await page.getByRole('button', { name: '使用原命令重试' }).click();
  await expect.poll(() => value.polls).toBeGreaterThanOrEqual(1);
  expect(value.posts).toHaveLength(2);
  expect(value.posts[1]).toEqual(value.posts[0]);
});

test('[owner] comparison explicitly computes selected symbols and re-reads curve cache', async ({ page }) => {
  const value = await scene(page, { cached: true });
  await page.goto(url + '&compare=A');
  const panel = page.locator('#workbench-comparison');
  const table = panel.getByRole('table', { name: '比较曲线精确值' });
  await expect(table).toBeVisible();
  await expect(table.getByRole('row').nth(1).getByRole('cell')).toHaveText('100');
  const initialReads = value.comparisonReads;
  value.cached = false;
  await page.getByRole('button', { name: '刷新快照' }).click();
  await expect(panel.getByText('当前比较曲线不可用', { exact: true })).toBeVisible();
  expect(value.comparisonReads).toBeGreaterThan(initialReads);
  const readsBeforeJob = value.comparisonReads;
  await panel.getByRole('button', { name: '运行自定义分析' }).click();
  await expect.poll(() => value.polls).toBeGreaterThanOrEqual(1);
  expect(value.posts[0].symbols).toEqual(['A']);
  value.complete = true;
  await expect(table).toBeVisible();
  await expect(table.getByRole('row')).toHaveCount(sessions.length + 1);
  await expect(table.getByRole('row').nth(1).getByRole('cell')).toHaveText('100');
  expect(value.comparisonReads).toBeGreaterThan(readsBeforeJob);
  expect(value.posts).toHaveLength(1);
  await expect(panel.getByRole('button', { name: '运行自定义分析' })).toHaveCount(0);
});

test('[owner] source denial clears job controls and prevents late protected revival', async ({ page }) => {
  const value = await scene(page);
  await page.goto(url);
  await page.getByRole('button', { name: '运行自定义分析' }).click();
  await expect.poll(() => value.polls).toBeGreaterThanOrEqual(1);
  value.denied = true;
  await expect(page.getByText('来源内容已清除')).toBeVisible();
  const polls = value.polls;
  value.complete = true;
  await page.clock.install();
  await page.clock.runFor(5001);
  expect(value.polls).toBe(polls);
  await expect(page.getByRole('table', { name: '涨幅排行' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '使用原命令重试' })).toHaveCount(0);
});

test('[owner] principal change stops polling and discards the previous command', async ({ page }) => {
  const value = await scene(page);
  await page.goto(url);
  await page.getByRole('button', { name: '运行自定义分析' }).click();
  await expect.poll(() => value.polls).toBeGreaterThanOrEqual(1);
  value.principalChanged = true;
  await expect(page.getByText('请先验证所有者会话。')).toBeVisible();
  const polls = value.polls;
  await page.clock.install();
  await page.clock.runFor(5001);
  expect(value.polls).toBe(polls);
  expect(value.posts).toHaveLength(1);
  await expect(page.getByRole('button', { name: '运行自定义分析' })).toBeDisabled();
});

test('[owner] navigation stops polling and a late job success cannot restore the old view', async ({ page }) => {
  const value = await scene(page, { latePoll: true });
  await page.goto(url);
  await page.getByRole('button', { name: '运行自定义分析' }).click();
  await expect.poll(() => value.polls).toBe(1);
  await page.getByRole('link', { name: '市场消息' }).click();
  await expect(page).toHaveURL(/view=news/);
  value.complete = true;
  value.releasePoll();
  await page.clock.install();
  await page.clock.runFor(5001);
  expect(value.polls).toBe(1);
  expect(value.posts).toHaveLength(1);
  await expect(page).toHaveURL(/view=news/);
  await expect(page.getByRole('table', { name: '涨幅排行' })).toHaveCount(0);
});

test('[owner] changing PUB invalidates the pending job and ignores its late completion', async ({ page }) => {
  const value = await scene(page, { latePoll: true });
  await page.goto(url);
  await page.getByRole('button', { name: '运行自定义分析' }).click();
  await expect.poll(() => value.polls).toBe(1);
  await page.getByRole('combobox', { name: '快照版本' }).selectOption(OTHER_PUB);
  await expect(page).toHaveURL(new RegExp(`snapshot=${OTHER_PUB}`));
  value.complete = true;
  value.releasePoll();
  await page.clock.install();
  await page.clock.runFor(5001);
  expect(value.polls).toBe(1);
  expect(value.posts).toHaveLength(1);
  await expect(page.getByRole('combobox', { name: '快照版本' })).toHaveValue(OTHER_PUB);
  await expect(page.getByRole('table', { name: '涨幅排行' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '使用原命令重试' })).toHaveCount(0);
});

test('snapshot details lazily show six clocks for each bound daily domain', async ({ page }) => {
  const value = await scene(page, { cached: true });
  await page.goto(url);
  await expect(page.getByRole('table', { name: '涨幅排行' })).toBeVisible();
  expect(value.lineageReads).toBe(0);
  await page.getByLabel('来源、覆盖率与版本详情', { exact: true }).click();
  const clocks = page.getByRole('region', { name: 'Clock lineage', exact: true });
  await expect(clocks).toBeVisible();
  await expect(clocks.getByText('Source event', { exact: true })).toHaveCount(5);
  await expect(clocks.getByText('Page accepted', { exact: true })).toHaveCount(5);
  expect(value.lineageReads).toBe(1);
});

test('[public] opens a PUB-bound reviewed daily archive without owner traffic', async ({ page }) => {
  const value = await scene(page, { cached: true });
  await page.goto(url.replace('view=analysis', 'view=overview'));
  await expect(page.getByText('复核日报 · 公开 AI 归档', { exact: true })).toBeVisible();
  expect(value.sidecarReads).toBe(0);
  await page.getByText('复核日报 · 公开 AI 归档', { exact: true }).click();
  await expect(page.getByText('合成日报证据缺失项', { exact: true })).toBeVisible();
  await expect(page.getByText('基础 PUB 发布时间', { exact: true })).toBeVisible();
  await expect(page.getByText('日报归档发布时间', { exact: true })).toBeVisible();
  expect(new Set(value.sidecarPaths)).toEqual(new Set([
    '/api/market/daily-ai', '/api/market/daily-ai/archives',
  ]));
  expect(value.privateReads).toEqual([]);
});

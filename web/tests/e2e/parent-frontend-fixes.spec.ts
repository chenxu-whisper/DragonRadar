import { expect, test, type Page, type Route } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import { PUB, TASK, OWNER, SESSION, DATE, AS_OF, calendar, task, eligibility, receipt,
  article, envelope } from '../fixtures/parent-frontend-fixes-data';
import { CLOCK_NAMES } from '../../src/features/lineage/types';
import { articles as c2Articles } from '../fixtures/contract-c2-scenes';
import { parseCsv } from '../../scripts/contract-c2-support.mjs';

const url = (surface: string) => `/tests/fixtures/parent-frontend-fixes.html?surface=${surface}`;
const json = (route: Route, value: unknown, status = 200) => route.fulfill({ status, json: value });
async function publicationScene(page: Page) {
  const value = { owner: OWNER, session: SESSION, sessionReads: 0, eligibilityReads: 0,
    posts: [] as Record<string, unknown>[], held: '' as string, release: () => {}, accepted: false,
    uncertain: false };
  const gate = new Promise<void>(resolve => { value.release = resolve; });
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/owner/v1/session') {
      value.sessionReads++;
      return json(route, { owner_id: value.owner, session_id: value.session, expires_at: '2099-01-01T00:00:00Z' });
    }
    if (path.endsWith('/publication-eligibility')) {
      value.eligibilityReads++;
      if (value.held === 'eligibility') await gate;
      return json(route, eligibility).catch(() => {});
    }
    if (path.endsWith('/publish-daily')) {
      value.posts.push(route.request().postDataJSON());
      if (value.uncertain && value.posts.length === 1) return route.abort('failed');
      if (value.held === 'publish') await gate;
      return json(route, receipt).catch(() => {});
    }
    if (path === '/api/ai/daily-review') {
      value.posts.push(route.request().postDataJSON());
      return json(route, task);
    }
    if (path.endsWith('/review')) {
      value.accepted = true;
      return json(route, { task_id: TASK, human_review_status: 'accepted', revision: 3,
        idempotency_key: route.request().postDataJSON().idempotency_key,
        verification_status: 'passed', note: 'Accepted' });
    }
    if (path === `/api/ai/task/${TASK}`) return json(route, { ...task,
      human_review_status: value.accepted ? 'accepted' : 'unreviewed' });
    return json(route, { code: 'not_found' }, 404);
  });
  return value;
}
async function fillPublication(page: Page) {
  await page.getByLabel('模型输出许可 reference').fill('licensed-model');
  await page.getByLabel('公开同意 reference').fill('public-consent');
  await page.getByLabel('非个人复核证明 reference').fill('nonpersonal');
}

test('F1 explicit public-safe daily submission creates empty original notes through acceptance and eligibility', async ({ page }) => {
  const scene = await publicationScene(page);
  await page.goto(url('daily'));
  await page.getByRole('button', { name: '展开完整 AI 分析' }).click();
  await page.getByRole('checkbox', { name: /公开安全每日复核/ }).check();
  await page.getByRole('button', { name: '生成每日复盘' }).click();
  await expect.poll(() => scene.posts.length).toBe(1);
  expect(scene.posts[0]).toMatchObject({ snapshot_id: PUB, date: DATE, as_of: AS_OF,
    version: 'v1', notes_context: '' });
  expect(JSON.stringify(scene.posts[0])).not.toContain('Private local market notes');
  await page.getByRole('button', { name: '接受校验草稿' }).click();
  await fillPublication(page);
  await page.getByRole('button', { name: '检查当前公开资格' }).click();
  await expect(page.getByText('服务端资格通过。发布时将重新核验。')).toBeVisible();
  expect(scene.posts).toHaveLength(1);
});

test('F1 private note path preserves original notes and cannot opt into public-safe stripping', async ({ page }) => {
  const scene = await publicationScene(page);
  await page.goto(url('notes'));
  await expect(page.getByRole('checkbox', { name: /公开安全每日复核/ })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '生成每日复盘' })).toBeDisabled();
  await page.getByRole('checkbox', { name: '标题', exact: true }).check();
  await page.getByRole('button', { name: '生成每日复盘' }).click();
  await expect.poll(() => scene.posts.length).toBe(1);
  expect(scene.posts[0].notes_context).toContain('Private original note');
});

for (const change of ['owner', 'session'] as const) {
  for (const held of ['eligibility', 'publish'] as const) {
    test(`F3 context ${change} switch invalidates late ${held} without a lock event`, async ({ page }) => {
      const scene = await publicationScene(page);
      await page.goto(url('publication'));
      await fillPublication(page);
      if (held === 'eligibility') scene.held = held;
      await page.getByRole('button', { name: '检查当前公开资格' }).click();
      await expect.poll(() => scene.eligibilityReads).toBe(1);
      if (held === 'publish') {
        await expect(page.getByRole('button', { name: '公开精确复核修订' })).toBeEnabled();
        scene.held = held;
        await page.getByRole('button', { name: '公开精确复核修订' }).click();
        await expect.poll(() => scene.posts.length).toBe(1);
      }
      await page.getByRole('button', { name: change === 'owner' ? 'Switch owner' : 'Switch session' }).click();
      scene.release();
      await expect(page.getByRole('button', { name: '公开精确复核修订' })).toBeDisabled();
      await expect(page.getByRole('button', { name: '撤回此归档' })).toHaveCount(0);
      await expect(page.getByText('服务端资格通过。发布时将重新核验。')).toHaveCount(0);
    });
  }
}

test('F3 reset without context rechecks actual owner/session before accepting a late receipt', async ({ page }) => {
  const scene = await publicationScene(page);
  await page.goto(url('publication-reset'));
  await fillPublication(page);
  await page.getByRole('button', { name: '检查当前公开资格' }).click();
  await expect(page.getByRole('button', { name: '公开精确复核修订' })).toBeEnabled();
  scene.held = 'publish';
  await page.getByRole('button', { name: '公开精确复核修订' }).click();
  await expect.poll(() => scene.posts.length).toBe(1);
  scene.session = TASK;
  scene.release();
  await expect(page.getByRole('button', { name: '撤回此归档' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '公开精确复核修订' })).toBeDisabled();
  await expect.poll(() => scene.sessionReads).toBeGreaterThanOrEqual(4);
});

test('F3 uncertain command retries identical bytes only in its bound reset owner/session', async ({ page }) => {
  const scene = await publicationScene(page);
  scene.uncertain = true;
  await page.goto(url('publication-reset'));
  await fillPublication(page);
  await page.getByRole('button', { name: '检查当前公开资格' }).click();
  await page.getByRole('button', { name: '公开精确复核修订' }).click();
  await expect(page.getByRole('button', { name: '使用原命令重试' })).toBeEnabled();
  await page.getByRole('button', { name: '使用原命令重试' }).click();
  await expect(page.getByRole('button', { name: '撤回此归档' })).toBeVisible();
  expect(scene.posts).toHaveLength(2);
  expect(scene.posts[1]).toEqual(scene.posts[0]);
});

test('F3 reset principal change cannot resend an uncertain frozen command with the new cookie', async ({ page }) => {
  const scene = await publicationScene(page);
  scene.uncertain = true;
  await page.goto(url('publication-reset'));
  await fillPublication(page);
  await page.getByRole('button', { name: '检查当前公开资格' }).click();
  await page.getByRole('button', { name: '公开精确复核修订' }).click();
  await expect(page.getByRole('button', { name: '使用原命令重试' })).toBeEnabled();
  scene.owner = TASK;
  await page.getByRole('button', { name: '使用原命令重试' }).click();
  await expect(page.getByRole('button', { name: '重新验证所有者会话' })).toBeVisible();
  expect(scene.posts).toHaveLength(1);
  await expect(page.getByRole('button', { name: '使用原命令重试' })).toHaveCount(0);
});

function clocks(selection: Record<string, unknown>) {
  const at = '2026-11-24T01:30:00Z';
  return { schema: 'dragon-radar.source-clocks/v1', identity: selection, as_of: AS_OF,
    clocks: Object.fromEntries(CLOCK_NAMES.map(name => [name,
      name === 'snapshot_published_at' || name === 'page_updated_at'
        ? { state: 'not_applicable', at: null, first_at: null, last_at: null,
          reason: name === 'page_updated_at' ? 'browser_clock' : 'domain_clock_not_applicable' }
        : { state: 'known', at, first_at: at, last_at: at, reason: null }])),
  };
}
async function ledgerScene(page: Page, surface: 'lhb' | 'realtime', status: number) {
  const value = { reads: 0, held: false, release: () => {} };
  const gate = new Promise<void>(resolve => { value.release = resolve; });
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/lineage')) {
      if (status === 200) return json(route, { malformed: true });
      return json(route, { code: status === 403 ? 'FORBIDDEN' : 'session_unauthorized' }, status);
    }
    if (surface === 'lhb' && path === '/api/lhb') {
      value.reads++;
      if (value.held) await gate;
      return json(route, { date: DATE, as_of: AS_OF, source: 'vendor', source_version: 'v1',
        quality_flags: [], basis: { unit: 'CNY', currency: 'CNY' },
        coverage: { total_disclosures: 1, visible_disclosures: 1, late_disclosures: 0,
          selected_disclosures: 1, seat_count: 1, complete_seats: 1, unknown_seats: 0 },
        data: { items: [{ disclosure_id: 'disc-1', security_id: 'TEST001', disclosure_date: DATE,
          reason: 'Protected seat evidence', revision: 1, source_id: 'vendor', source_version: 'v1',
          source_event_at: '2026-11-24T01:00:00Z', received_at: '2026-11-24T01:30:00Z',
          available_at: '2026-11-24T02:00:00Z', seats: [{
            source_row_id: 'row-1', seat_id: 'seat-1', source_seat_name: 'Protected seat',
            mapping_version: 'v1', side: 'buy', rank: 1, buy_amount: 10, sell_amount: 0,
            currency: 'CNY', unit: 'CNY', completeness: 'complete', net_amount: 10,
          }] }], total: 1, page: 1, size: 20 } }).catch(() => {});
    }
    if (path === '/api/realtime/quotes') {
      value.reads++;
      if (value.held) await gate;
      return json(route, { ...envelope({ quote_batch_id: 'quote-1',
        source_event_at: '2026-11-24T01:30:00Z', received_at: '2026-11-24T01:30:05Z',
        delay_ms: 5000, status: 'complete', quotes: [{
          security_id: 'TEST001', last: '10', bid: null, ask: null, bid_size: null, ask_size: null,
          volume: '1', amount: '10', order_book_status: 'disabled', order_book_unavailable_reason: 'disabled',
        }] }), trade_date: null, snapshot_id: null, rule_version: null,
        source: 'vendor', source_version: 'v1' }).catch(() => {});
    }
    return json(route, { code: 'NOT_FOUND' }, 404);
  });
  return value;
}

for (const surface of ['realtime', 'lhb'] as const) {
  for (const status of [401, 403, 404, 200]) {
    test(`F2 ${surface} clock ${status} ${[401, 403].includes(status) ? 'clears populated parent and late reads' : 'stays local'}`, async ({ page }) => {
      const scene = await ledgerScene(page, surface, status);
      await page.goto(url(surface));
      const table = page.getByRole('table', { name: surface === 'lhb' ? /席位记录/ : '实时报价', exact: surface !== 'lhb' });
      await expect(table).toBeVisible();
      scene.held = true;
      await page.getByRole('button', { name: 'Refresh context' }).click();
      await expect.poll(() => scene.reads).toBe(2);
      await page.locator('summary').filter({ hasText: surface === 'lhb' ? /披露来源时钟/ : /quotes source clocks/ }).click();
      if ([401, 403].includes(status)) {
        await expect(table).toHaveCount(0);
        if (status === 401) await expect(page.getByTestId('principal')).toHaveText('locked');
        else await expect(page.getByTestId('principal')).not.toHaveText('locked');
      } else await expect(table).toBeVisible();
      scene.release();
      await page.waitForLoadState('networkidle');
      if ([401, 403].includes(status)) await expect(table).toHaveCount(0);
      else await expect(table).toBeVisible();
    });
  }
}

for (const navigation of ['Navigate analysis', 'Change return context']) {
  test(`F4 ${navigation} invalidates shared comparison pending job while computation identity stays fixed`, async ({ page }) => {
    let polls = 0, reads = 0, release = () => {};
    const posts: Record<string, unknown>[] = [];
    const gate = new Promise<void>(resolve => { release = resolve; });
    await page.route('**/api/**', async route => {
      const path = new URL(route.request().url()).pathname;
      if (path === '/api/owner/v1/session') return json(route, {
        owner_id: OWNER, session_id: SESSION, expires_at: '2099-01-01T00:00:00Z',
      });
      if (path === '/api/compare/curves') { reads++; return json(route, { ...envelope(null),
        basis: { unit: 'ratio', currency: 'CNY', price_type: 'raw', benchmark: 'B000300' },
        reason: 'analysis_not_precomputed' }); }
      if (path === '/api/analysis/jobs') {
        posts.push(route.request().postDataJSON()); return json(route, { job_id: TASK, status: 'queued' });
      }
      if (path === `/api/analysis/jobs/${TASK}`) {
        polls++;
        const old = polls === 1;
        if (old) await gate;
        const input: Record<string, unknown> = { ...posts[0],
          algorithm_version: 'analysis-custom-v1-decimal34', adjustment_version: 'a1' };
        delete input.idempotency_key;
        const result = old ? {
          schema: 'dragon-radar.custom-analysis', schema_version: 1,
          algorithm_version: input.algorithm_version, snapshot_id: PUB,
          requested_start: input.start, requested_end: input.end,
          adjusted_start: input.start, adjusted_end: input.end,
          benchmark: input.benchmark, adjustment: input.adj,
          eligible_security_ids: ['A'], available: true, reason: null,
          ranking: [{ rank: 1, security_id: 'A', name: 'Synthetic A', topics: [],
            topic_status: 'unavailable', topic_reasons: ['topic_batch_unavailable'],
            return_ratio: 0.1, excess_return_ratio: null, max_drawdown_ratio: 0,
            amount_change_ratio: null, reasons: ['benchmark_identity_invalid'] }],
          exclusions: [], benchmark_return_ratio: null, benchmark_reasons: ['benchmark_identity_invalid'],
          curves: [{ security_id: 'A', points: calendar.map(trade_date =>
            ({ trade_date, normalized: 1, drawdown: 0 })), reasons: [] }],
        } : null;
        return json(route, { job_id: TASK, status: old ? 'succeeded' : 'running', input, result, failure: null }).catch(() => {});
      }
      return json(route, {}, 404);
    });
    await page.goto(url('comparison'));
    await page.getByRole('button', { name: '运行自定义分析' }).click();
    await expect.poll(() => polls).toBe(1);
    await page.getByRole('button', { name: navigation, exact: true }).click();
    // Destination resumption must reauthorize and issue a new GET before old GET is released.
    await expect.poll(() => polls).toBe(2);
    const currentReads = reads;
    release();
    await expect(page.getByText(/正在计算冻结输入/)).toBeVisible();
    await expect(page.getByText(/后台计算已成功/)).toHaveCount(0);
    expect(reads).toBe(currentReads);
    expect(Object.keys(posts[0]).sort()).toEqual([
      'snapshot_id', 'start', 'end', 'benchmark', 'adj', 'symbols', 'idempotency_key',
    ].sort());
    expect(posts[0]).toMatchObject({ snapshot_id: PUB, start: calendar[0], end: DATE, symbols: ['A'] });
  });
}

test('F5 list and detail show full Beijing timestamps; actual CSV contains exact new columns and no preview', async ({ page }) => {
  const preview = { status: 'ready', text: 'AI DISPLAY ONLY', truncated: false, reason: null,
    summary_id: TASK, summary_revision: 3, provenance: 'ai' };
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/news') return json(route, envelope({ articles: [{ ...article, ai_preview: preview }],
      page: 1, size: 20, total: 1, event_count: 1, sources: ['synthetic'] }));
    if (path === `/api/news/${TASK}`) return json(route, envelope({ ...article, ai_preview: preview,
      url: 'https://example.test/original', content_scope: 'summary', content: null, ai_summary: null,
      related_security_ids: [], is_novel_event: false, supporting_evidence: [], opposing_evidence: [],
      securities: [], topics: [],
    }));
    return json(route, envelope(null));
  });
  await page.goto(url('news'));
  const row = page.locator('.news-list-row');
  await expect(row).toContainText('2026/11/24 01:00:00');
  await expect(row).toContainText('UTC+08:00');
  await expect(row).toContainText(SESSION);
  await row.click();
  await expect(page.locator('.news-metadata')).toContainText('2026/11/24 01:00:00');
  await expect(page.locator('.news-metadata')).toContainText('UTC+08:00');
  const waiting = page.waitForEvent('download');
  await page.getByRole('button', { name: '导出当前页 CSV' }).click();
  const download = await waiting;
  const text = await readFile((await download.path())!, 'utf8');
  expect(text.split('\r\n')[0].split(',').slice(9)).toEqual([
    'article_id', 'article_type', 'source', 'source_version', 'title',
    'published_at', 'received_at', 'available_at', 'revision', 'revision_id',
  ]);
  expect(text).toContain(article.received_at);
  expect(text).toContain(article.revision_id);
  expect(text).not.toContain('AI DISPLAY ONLY');
  expect(text).not.toContain('ai_preview');
  expect(text).not.toContain('UTC+08:00');
});

test('F5 C2 fixtures export exact receipt/revision columns on both actual downloaded pages', async ({ page }) => {
  await page.route('**/api/**', route => {
    const target = new URL(route.request().url());
    if (target.pathname !== '/api/news') return json(route, envelope(null));
    const current = Number(target.searchParams.get('page'));
    return json(route, envelope({ articles: c2Articles.slice((current - 1) * 20, current * 20),
      page: current, size: 20, total: c2Articles.length, event_count: 1, sources: ['synthetic-wire'] }));
  });
  await page.goto(url('news'));
  for (const current of [1, 2]) {
    const expected = c2Articles.slice((current - 1) * 20, current * 20);
    await expect(page.locator('.news-list-row strong')).toHaveText(expected.map(item => item.title));
    const waiting = page.waitForEvent('download');
    await page.getByRole('button', { name: '导出当前页 CSV' }).click();
    const download = await waiting;
    const text = await readFile((await download.path())!, 'utf8');
    const rows = parseCsv(text);
    const fields = ['article_id', 'article_type', 'source', 'source_version', 'title',
      'published_at', 'received_at', 'available_at', 'revision', 'revision_id'];
    expect(Object.keys(rows[0]).slice(9)).toEqual(fields);
    expect(rows.map(row => fields.map(field => row[field]))).toEqual(expected.map(item =>
      fields.map(field => String((item as Record<string, unknown>)[field]))));
    expect(text).not.toContain('ai_preview');
    expect(rows.map(row => [row.snapshot_id, row.trade_date, row.as_of]))
      .toEqual(expected.map(() => [PUB, DATE, AS_OF]));
    if (current === 1) await page.getByRole('button', { name: '下一页', exact: true }).click();
  }
});

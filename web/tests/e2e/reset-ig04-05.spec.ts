import { expect, test, type Page } from '@playwright/test';
import { ID, OLD, mockLeaders, resetSummary, sessions } from '../fixtures/market';
import { ARTICLE_A, articleA, listArticleA, newsEnvelope } from '../fixtures/news';

const date = '2026-09-10';
const past = '2026-09-09';
const url = `/?view=leaders&snapshot=${ID}&date=${date}&sector=SEC001&role=trend_leader`;
const multi = `${url}&roles=trend_leader,zhongjun`;
const coverage = { expected: 2, valid: 2, excluded: 0, missing: 0 };
const promotion = { yesterday_ge2: 2, today_sealed: 1, tradable: 2, rate: 0.5 };

function envelope(data: unknown, snapshot = ID, day = date) {
  return {
    ...resetSummary(), snapshot_id: snapshot, trade_date: day,
    rule_version: 'role-v1', source: 'role_materialization', coverage, data,
  };
}

function card(security = 'TEST001', name = '合成测试证券1', roles = ['trend_leader']) {
  return {
    security_id: security, name, roles, sector_id: 'SEC001', change_ratio: 0.1234,
    topics: [{ sector_id: 'SEC001', name: '发布内题材甲' }],
    role_evidence: roles.map(role_type => ({
      role_type, sector_id: 'SEC001', evidence_status: 'candidate',
    })),
    seal: {
      is_limit_up: true, touched_limit: true,
      first_seal_time: null, last_seal_time: null, break_count: null,
      seal_amount: null, seal_amount_unit: null, continuous_boards: 2,
      reason: 'intraday_seal_fields_unavailable',
    },
  };
}

function ladder() {
  const cells = [{
    boards: 2, count: 3, stocks: [
      card(), card('TEST002', '合成测试证券2', ['zhongjun']),
      card('TEST003', '合成测试证券3', []),
    ],
  }];
  return {
    columns: [2], total: 3, cells, promote_rate: promotion,
    history: {
      days: 10, sector: 'SEC001', window: { start: '2026-08-28', end: date },
      rows: [
        ...sessions.slice(-10, -2).map(trade_date => ({
          trade_date, snapshot_id: null, rule_version: null,
          status: 'unavailable', reasons: ['publication_unavailable'],
          coverage: null, columns: null, total: null, cells: null,
        })),
        {
          trade_date: past, snapshot_id: OLD, rule_version: 'role-v1',
          status: 'available', reasons: [], coverage: { ...coverage, expected: 1, valid: 1 },
          columns: [2], total: 1,
          cells: [{ boards: 2, count: 1, stocks: [card('TEST001', '历史发布证券甲')] }],
        },
        {
          trade_date: date, snapshot_id: ID, rule_version: 'role-v1',
          status: 'available', reasons: [], coverage: { ...coverage, expected: 3, valid: 3 },
          columns: [2], total: 3, cells,
        },
      ],
    },
  };
}

function continuity(legacy = false) {
  const members = [
    {
      security_id: 'TEST001', name: '昨日固定池甲', status: 'valid',
      return: 0.1, direction: 'up', sealed: true, reasons: [],
    },
    {
      security_id: 'TEST002', name: '昨日固定池乙', status: 'valid',
      return: -0.1, direction: 'down', sealed: false, reasons: [],
    },
  ];
  return {
    promote_rate: promotion, promote_rate_quality: { coverage, reasons: [] },
    yesterday_limit_up_median_return: 0,
    yesterday_limit_up_feedback: { status: 'available', coverage, reasons: [] },
    tradable_count: 2,
    details: legacy ? {
      status: 'unavailable', version: null, data: null,
      reasons: ['continuity_detail_unavailable'],
    } : {
      status: 'available', version: 1, reasons: [],
      data: {
        trade_date: date, previous_trade_date: past,
        promotion: { coverage, members },
        feedback: { coverage, members, direction_counts: { up: 1, down: 1, flat: 0 } },
      },
    },
  };
}

type MockState = {
  denied?: boolean;
  wrongCoreIdentity?: boolean;
  coreInsufficient?: boolean;
  legacy?: boolean;
  large?: boolean;
  corrupt?: boolean;
  delayed?: Promise<void>;
};

async function setup(page: Page, state: MockState = {}) {
  await mockLeaders(page);
  await page.route('**/api/limit-up/ladder?**', async route => {
    if (state.denied) return route.fulfill({ status: 403, json: { code: 'FORBIDDEN' } });
    const data = ladder();
    if (state.corrupt) data.history.rows.at(-1)!.snapshot_id = OLD;
    return route.fulfill({ json: {
      ...envelope(data), coverage: { expected: 3, valid: 3, excluded: 0, missing: 0 },
    } });
  });
  await page.route('**/api/limit-up/continuity?**', route => {
    if (state.denied) return route.fulfill({ status: 403, json: { code: 'FORBIDDEN' } });
    return route.fulfill({ json: envelope(continuity(state.legacy)) });
  });
  await page.route('**/api/role/candidates?**', async route => {
    const query = new URL(route.request().url()).searchParams;
    const role = query.get('role')!;
    const core = role === 'zhongjun';
    if (core && state.delayed) await state.delayed;
    if (state.denied) return route.fulfill({ status: 403, json: { code: 'FORBIDDEN' } });
    const insufficient = core && state.coreInsufficient;
    const count = state.large ? 65 : core ? 1 : 2;
    const candidates = insufficient ? [] : Array.from({ length: count }, (_, index) => ({
      security_id: `TEST${String(index + 1).padStart(3, '0')}`,
      name: state.large && index === 64 ? null : `合成测试证券${index + 1}`,
      rank: state.large ? index + 1 : 1, evidence_status: 'candidate', sector_id: 'SEC001',
      boards: 2,
    }));
    const changes = candidates.map((candidate, index) => ({
      id: `${role}-${index}`, sector_id: 'SEC001', security_id: candidate.security_id,
      name: candidate.name, rank: candidate.rank, role,
      comparison_status: 'comparable', change_type: 'added', membership_changed: false,
      reasons: ['entered_rank'],
    }));
    return route.fulfill({ json: {
      ...envelope({
        role, candidates, total: candidates.length, role_changes: changes,
        change_summaries: [],
        statuses: [{
          sector_id: query.get('sector') || 'SEC001',
          status: insufficient ? 'insufficient' : 'ready', capability: 'ready',
          reasons: insufficient ? ['required_inputs_missing'] : [],
        }],
      }),
      snapshot_id: core && state.wrongCoreIdentity ? OLD : ID,
      coverage: insufficient
        ? { expected: 2, valid: 0, excluded: 0, missing: 2 }
        : { expected: count, valid: count, excluded: 0, missing: 0 },
    } });
  });
}

test('multi-role URL selects OR highlighting and independent candidates, ties and coverage', async ({ page }) => {
  const requests: string[] = [];
  await setup(page);
  page.on('request', request => {
    const u = new URL(request.url());
    if (u.pathname === '/api/role/candidates') requests.push(u.searchParams.get('role')!);
  });
  await page.goto(multi);
  await expect(page.getByRole('checkbox', { name: '趋势龙头', exact: true })).toBeChecked();
  await expect(page.getByRole('checkbox', { name: '中军', exact: true })).toBeChecked();
  const trend = page.getByRole('region', { name: '趋势龙头候选', exact: true });
  const core = page.getByRole('region', { name: '中军候选', exact: true });
  await expect(trend.getByText('并列 1', { exact: true })).toHaveCount(2);
  await expect(core.getByText('并列 1', { exact: true })).toHaveCount(1);
  await expect(trend).toContainText('有效 2 / 应有 2');
  await expect(core).toContainText('有效 1 / 应有 1');
  const matrix = page.getByRole('table', { name: '日期与板数矩阵' });
  await expect(matrix.getByRole('button', { name: '合成测试证券1', exact: true }))
    .toHaveAttribute('data-role-highlighted', 'true');
  await expect(matrix.getByRole('button', { name: '合成测试证券2', exact: true }))
    .toHaveAttribute('data-role-highlighted', 'true');
  await expect(matrix.getByRole('button', { name: '合成测试证券3', exact: true }))
    .toHaveAttribute('data-role-highlighted', 'false');
  expect(requests.sort()).toEqual(['trend_leader', 'zhongjun']);
});

test('independent insufficient role does not erase the other role or sum its coverage', async ({ page }) => {
  await setup(page, { coreInsufficient: true });
  await page.goto(multi);
  const trend = page.getByRole('region', { name: '趋势龙头候选', exact: true });
  const core = page.getByRole('region', { name: '中军候选', exact: true });
  await expect(trend.getByRole('button', { name: '合成测试证券1', exact: true })).toBeVisible();
  await expect(core).toContainText('角色输入不足');
  await expect(core).toContainText('有效 0 / 应有 2');
  await expect(core.getByRole('table', { name: /候选/ })).toHaveCount(0);
});

test('wrong role-response identity and source revocation fail closed independently', async ({ page }) => {
  const state: MockState = { wrongCoreIdentity: true };
  await setup(page, state);
  await page.goto(multi);
  const core = page.getByRole('region', { name: '中军候选', exact: true });
  await expect(core.getByRole('alert')).toContainText('快照');
  await expect(core.getByRole('button', { name: '合成测试证券1', exact: true })).toHaveCount(0);
  await expect(page.getByRole('region', { name: '趋势龙头候选', exact: true })
    .getByRole('button', { name: '合成测试证券1', exact: true })).toBeVisible();
  state.denied = true;
  await page.getByRole('button', { name: '刷新快照', exact: true }).click();
  await expect(page.getByText('来源访问已被拒绝', { exact: true }).first()).toBeVisible();
  await expect(page.getByRole('table', { name: '日期与板数矩阵' })).toHaveCount(0);
});

test('deselected delayed role cannot restore stale candidates or alter canonical URL', async ({ page }) => {
  let release!: () => void;
  const delayed = new Promise<void>(resolve => { release = resolve; });
  await setup(page, { delayed });
  try {
    await page.goto(multi);
    const checkbox = page.getByRole('checkbox', { name: '中军', exact: true });
    await expect(checkbox).toBeChecked();
    await checkbox.uncheck();
    release();
    await expect(page).toHaveURL(u => u.searchParams.get('roles') === 'trend_leader');
    await expect(page.getByRole('region', { name: '中军候选', exact: true })).toHaveCount(0);
    await expect(page.getByRole('region', { name: '趋势龙头候选', exact: true })).toBeVisible();
  } finally { release(); }
});

test('historical cells show actual stock lists and open their own publication without changing root', async ({ page }) => {
  const requests: string[] = [];
  await setup(page);
  await page.route('**/api/stock/TEST001?**', route => {
    const query = new URL(route.request().url()).searchParams;
    requests.push(`${query.get('snapshot_id')}:${query.get('date')}`);
    const historical = query.get('snapshot_id') === OLD;
    return route.fulfill({ json: envelope({
      security_id: 'TEST001', name: historical ? '历史发布证券甲' : '当前发布证券甲',
      quote: { last: 11, change_ratio: 0.1, amount: 100 },
      roles: [{ role_type: 'trend_leader', evidence_status: 'candidate', sector_id: 'SEC001' }],
      seal: { ...card().seal, reason: null },
      regulation: { formal_event_count: 0, event_source_completeness: 'complete', reason: null },
    }, historical ? OLD : ID, historical ? past : date) });
  });
  await page.goto(url);
  const matrix = page.getByRole('table', { name: '日期与板数矩阵' });
  await expect(matrix).not.toContainText('99 家');
  await expect(matrix).toContainText(OLD);
  await expect(matrix).toContainText('2026-09-08');
  await expect(matrix).toContainText('不可用');
  const trigger = matrix.getByRole('button', { name: '历史发布证券甲', exact: true });
  await trigger.click();
  await expect(page.getByRole('dialog').getByRole('heading', { name: '历史发布证券甲' }))
    .toBeVisible();
  await expect.poll(() => requests).toContain(`${OLD}:${past}`);
  await expect(page).toHaveURL(u => u.searchParams.get('snapshot') === ID
    && u.searchParams.get('date') === date && u.searchParams.get('sector') === 'SEC001');
  await trigger.dispatchEvent('click');
  await expect(page.locator('dialog[open]')).toHaveCount(0);
  await expect(trigger).toBeFocused();
  await trigger.click();
  await expect(page.getByRole('dialog')).toContainText('历史发布证券甲');
  await page.keyboard.press('Escape');
  await expect(trigger).toBeFocused();
  const article = {
    ...articleA, published_at: `${past}T07:00:00Z`, received_at: `${past}T08:00:00Z`,
    available_at: `${past}T09:00:00Z`, title: '原始发布关联消息',
  };
  await page.route('**/api/news?**', route => route.fulfill({ json: newsEnvelope({
    articles: [{ ...listArticleA, ...article }], sources: [article.source], event_count: 1,
    page: 1, size: Number(new URL(route.request().url()).searchParams.get('size')), total: 1,
  }, { snapshot_id: OLD, trade_date: past, as_of: `${past}T10:00:00Z` }) }));
  await page.route(`**/api/news/${ARTICLE_A}?**`, route => {
    const query = new URL(route.request().url()).searchParams;
    expect(query.get('snapshot_id')).toBe(OLD);
    expect(query.get('date')).toBe(past);
    return route.fulfill({ json: newsEnvelope(article, { snapshot_id: OLD, trade_date: past, as_of: `${past}T10:00:00Z` }) });
  });
  await trigger.click();
  await page.getByRole('dialog').getByRole('link', { name: '原始发布关联消息', exact: true }).click();
  await expect(page).toHaveURL(u => u.searchParams.get('snapshot') === OLD
    && u.searchParams.get('date') === past && u.searchParams.get('article') === ARTICLE_A);
  await expect(page.getByRole('region', { name: '消息详情', exact: true })).toContainText('原始发布关联消息');
});

test('cards disclose same-publication change, topic, role evidence and missing seal times without N+1', async ({ page }) => {
  const perStock: string[] = [];
  await setup(page);
  page.on('request', request => {
    const path = new URL(request.url()).pathname;
    if (path.startsWith('/api/stock/') || path === '/api/role/evidence') perStock.push(path);
  });
  await page.goto(multi);
  const matrix = page.getByRole('table', { name: '日期与板数矩阵' });
  const current = matrix.getByRole('button', { name: '合成测试证券1', exact: true });
  await expect(current).toContainText('12.34%');
  await expect(current).toContainText('发布内题材甲');
  await expect(current).toContainText('趋势龙头');
  await expect(current).toContainText('候选');
  await expect(matrix).toContainText('封板');
  await expect(matrix).toContainText('不可用');
  expect(perStock).toEqual([]);
});

test('legacy continuity retains summary and explicitly unavailable details; valid archive opens fixed pool', async ({ page }) => {
  const state: MockState = { legacy: true };
  await setup(page, state);
  await page.goto(url);
  const section = page.locator('section').filter({
    has: page.getByRole('heading', { name: '晋级与昨日反馈', exact: true }),
  });
  await expect(section).toContainText('详情不可用');
  await expect(section).toContainText('50.00%');
  state.legacy = false;
  await page.getByRole('button', { name: '刷新快照', exact: true }).click();
  await section.getByRole('button', { name: '查看昨日涨停反馈详情', exact: true }).click();
  await expect(section).toContainText('昨日固定池甲');
  await expect(section).toContainText('昨日固定池乙');
  await expect(section).toContainText('上涨 1');
  await expect(section).toContainText('下跌 1');
  await expect(section).toContainText('平盘 0');
});

test('corrupt historical anchor cannot render unverified historical cells', async ({ page }) => {
  await setup(page, { corrupt: true });
  await page.goto(url);
  await expect(page.getByRole('alert').filter({ hasText: /锚点|快照/ }).first()).toBeVisible();
  await expect(page.getByRole('button', { name: '历史发布证券甲', exact: true })).toHaveCount(0);
});

test('candidate and change lists page separately at 20/50/100 and sort both directions with nulls last', async ({ page }) => {
  await setup(page, { large: true });
  await page.goto(url);
  const candidates = page.getByRole('region', { name: '趋势龙头候选', exact: true });
  const table = candidates.getByRole('table', { name: /候选/ });
  await expect(candidates.getByLabel('每页条数')).toHaveValue('50');
  await expect(table.locator('tbody tr')).toHaveCount(50);
  await candidates.getByLabel('每页条数').selectOption('20');
  await expect(table.locator('tbody tr')).toHaveCount(20);
  await candidates.getByRole('button', { name: '下一页', exact: true }).click();
  await expect(table.locator('tbody tr').first()).toContainText('TEST021');
  await candidates.getByLabel('每页条数').selectOption('100');
  await expect(table.locator('tbody tr')).toHaveCount(65);
  const name = table.getByRole('button', { name: '名称排序', exact: true });
  await name.click();
  await expect(table.locator('tbody tr').last()).toContainText('TEST065');
  await name.click();
  await expect(table.locator('tbody tr').last()).toContainText('TEST065');
  const identity = table.getByRole('button', { name: '证券排序', exact: true });
  await identity.click();
  await expect(table.locator('tbody tr').first()).toContainText('TEST001');
  await identity.click();
  await expect(table.locator('tbody tr').first()).toContainText('TEST065');
  const rank = table.getByRole('button', { name: '名次排序', exact: true });
  await rank.click();
  await expect(table.locator('tbody tr').first()).toContainText('TEST001');
  await rank.click();
  await expect(table.locator('tbody tr').first()).toContainText('TEST065');
  const changes = page.getByRole('region', { name: '趋势龙头角色变化', exact: true });
  await expect(changes.getByLabel('每页条数')).toHaveValue('50');
  await expect(changes.getByRole('table').locator('tbody tr')).toHaveCount(50);
  await changes.getByLabel('每页条数').selectOption('20');
  await expect(changes.getByRole('table').locator('tbody tr')).toHaveCount(20);
  await changes.getByRole('button', { name: '下一页', exact: true }).click();
  await expect(changes.getByRole('table').locator('tbody tr').first()).toContainText('TEST021');
});

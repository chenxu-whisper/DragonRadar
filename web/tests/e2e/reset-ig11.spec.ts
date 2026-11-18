import { expect, test, type Page } from '@playwright/test';
import { ID, OLD, mockMarket, resetSummary, securities } from '../fixtures/market';
import { ARTICLE_A, articleA, listArticleA, newsEnvelope } from '../fixtures/news';

// Parent runs this suite only during unified RED/GREEN with frozen source.
const DATE = '2026-09-10';
const overviewUrl = `/?view=overview&snapshot=${ID}&date=${DATE}`;
const TOPIC = 'archived-topic-not-loaded-in-command-index';
const globalDialog = (page: Page) => page.getByRole('dialog', { name: '全局搜索 · 发布归档' });
const trigger = (page: Page) => page.getByRole('button', { name: '全局搜索', exact: true });

type Group = 'securities' | 'topics' | 'messages';
type Result = { group: Group; item: Record<string, unknown> };
type SearchState = {
  requests: URL[];
  wrong?: 'snapshot' | 'date' | 'page' | 'size' | 'query';
  missing?: boolean;
  denied?: boolean;
  release?: Promise<void>;
  settled?: () => void;
};

const results: Result[] = [
  ...securities.slice(0, 63).map(item => ({
    group: 'securities' as const,
    item: { security_id: item.security_id, code: item.security_id, name: item.name, exchange: 'SSE', match_field: 'name' },
  })),
  { group: 'topics', item: { sector_id: TOPIC, name: '合成归档题材', match_field: 'name' } },
  { group: 'messages', item: { ...listArticleA, title: '合成归档消息', match_field: 'title' } },
];

async function mockIG11(page: Page, state: SearchState = { requests: [] }) {
  await mockMarket(page);
  await page.route('**/api/search?**', async route => {
    const request = new URL(route.request().url());
    state.requests.push(request);
    if (state.release) await state.release;
    if (state.denied) return route.fulfill({ status: 403, json: { code: 'FORBIDDEN' } });
    const query = request.searchParams.get('q') || '';
    const pageNumber = Number(request.searchParams.get('page') || 1);
    const size = Number(request.searchParams.get('size') || 50);
    const matching = query === '无匹配' ? [] : results;
    const available = matching.filter(result => !state.missing || result.group === 'securities');
    const groupStatus = Object.fromEntries((['securities', 'topics', 'messages'] as const).map(group => [
      group,
      state.missing && group !== 'securities'
        ? { status: 'unavailable', total: null, reason: `${group}_materialization_unavailable` }
        : { status: 'ready', total: available.filter(result => result.group === group).length, reason: null },
    ]));
    const slice = available.slice((pageNumber - 1) * size, pageNumber * size);
    const groups = Object.fromEntries((['securities', 'topics', 'messages'] as const).map(group => [
      group, slice.filter(result => result.group === group).map(result => result.item),
    ]));
    await route.fulfill({ json: {
      ...resetSummary(),
      snapshot_id: state.wrong === 'snapshot' ? OLD : ID,
      trade_date: state.wrong === 'date' ? '2026-09-09' : DATE,
      data: {
        query: state.wrong === 'query' ? 'other-query' : query,
        page: state.wrong === 'page' ? pageNumber + 1 : pageNumber,
        size: state.wrong === 'size' ? 100 : size,
        total: available.length, groups, group_status: groupStatus,
      },
    } });
    state.settled?.();
  });
  await page.route(`**/api/topics/${TOPIC}?**`, route => {
    const request = new URL(route.request().url());
    expect(request.searchParams.get('snapshot_id')).toBe(ID);
    expect(request.searchParams.get('date')).toBe(DATE);
    return route.fulfill({ json: { ...resetSummary(), data: null, quality_flags: ['materialization_unavailable'] } });
  });
  await page.route('**/api/news?**', route => {
    const request = new URL(route.request().url());
    return route.fulfill({ json: newsEnvelope({
      articles: [listArticleA], sources: ['synthetic-wire'], event_count: 1,
      page: Number(request.searchParams.get('page') || 1),
      size: Number(request.searchParams.get('size') || 50), total: 1,
    }, { snapshot_id: ID }) });
  });
  await page.route(`**/api/news/${ARTICLE_A}?**`, route => {
    const request = new URL(route.request().url());
    expect(request.searchParams.get('snapshot_id')).toBe(ID);
    expect(request.searchParams.get('date')).toBe(DATE);
    return route.fulfill({ json: newsEnvelope(articleA, { snapshot_id: ID }) });
  });
}

async function submit(page: Page, query = '合成') {
  await globalDialog(page).getByRole('textbox', { name: '搜索发布归档' }).fill(query);
  await globalDialog(page).getByRole('button', { name: '搜索归档', exact: true }).click();
}

test('native search dialog submits explicitly, traps focus, restores trigger and leaves command palette intact', async ({ page }) => {
  const state: SearchState = { requests: [] };
  await mockIG11(page, state);
  await page.goto(overviewUrl);
  const opener = trigger(page);
  await opener.focus();
  await page.keyboard.press('Enter');
  const dialog = globalDialog(page);
  await expect(dialog).toBeVisible();
  expect(await dialog.evaluate(element => element instanceof HTMLDialogElement && element.open)).toBe(true);
  await expect(page.locator('dialog[open]')).toHaveCount(1);
  const input = dialog.getByRole('textbox', { name: '搜索发布归档' });
  await expect(input).toBeFocused();
  await input.fill('合成');
  expect(state.requests).toEqual([]);
  await page.keyboard.press('Enter');
  await expect(dialog.getByRole('button', { name: '合成测试证券1', exact: true })).toBeVisible();
  expect(state.requests).toHaveLength(1);
  expect(Object.fromEntries(state.requests[0].searchParams)).toEqual({
    q: '合成', snapshot_id: ID, date: DATE, page: '1', size: '50',
  });
  await dialog.getByRole('button', { name: '关闭全局搜索', exact: true }).focus();
  await page.keyboard.press('Tab');
  expect(await dialog.evaluate(element => element.contains(document.activeElement))).toBe(true);
  await page.keyboard.press('Escape');
  await expect(dialog).not.toBeVisible();
  await expect(opener).toBeFocused();
  await page.keyboard.press('Control+k');
  await expect(page.getByRole('dialog', { name: '命令面板', exact: true })).toBeVisible();
  await expect(page.getByRole('dialog')).toHaveCount(1);
  expect(state.requests).toHaveLength(1);
});

test('combined pagination is PUB-bound and keyboard securities open one actual target', async ({ page }) => {
  const state: SearchState = { requests: [] };
  await mockIG11(page, state);
  await page.goto(overviewUrl);
  await trigger(page).click();
  await submit(page);
  const dialog = globalDialog(page);
  await expect(dialog.getByRole('status').filter({ hasText: '共 65 条' })).toBeVisible();
  await dialog.getByRole('button', { name: '下一页', exact: true }).click();
  await expect(dialog.getByRole('button', { name: '合成归档题材', exact: true })).toBeVisible();
  await expect(dialog.getByRole('button', { name: '合成归档消息', exact: true })).toBeVisible();
  await expect(dialog.getByRole('button', { name: '合成测试证券1', exact: true })).toHaveCount(0);
  expect(state.requests.at(-1)!.searchParams.get('page')).toBe('2');
  await dialog.getByLabel('每页条数').selectOption('20');
  await expect(dialog.getByRole('button', { name: '合成测试证券1', exact: true })).toBeVisible();
  expect(state.requests.at(-1)!.searchParams.get('page')).toBe('1');
  expect(state.requests.at(-1)!.searchParams.get('size')).toBe('20');
  expect(state.requests.every(request => request.searchParams.get('snapshot_id') === ID
    && request.searchParams.get('date') === DATE)).toBe(true);
  await dialog.getByRole('button', { name: '合成测试证券1', exact: true }).focus();
  await page.keyboard.press('Enter');
  await expect(globalDialog(page)).not.toBeVisible();
  const stock = page.getByRole('dialog', { name: '证券详情 · 只读' });
  await expect(stock).toContainText('TEST001');
  await expect(page.locator('dialog[open]')).toHaveCount(1);
});

for (const target of ['topic', 'article'] as const) {
  test(`keyboard ${target} selection opens archived identity, never guessed current context`, async ({ page }) => {
    await mockIG11(page);
    await page.goto(overviewUrl);
    await trigger(page).click();
    await submit(page);
    await globalDialog(page).getByLabel('每页条数').selectOption('100');
    const name = target === 'topic' ? '合成归档题材' : '合成归档消息';
    await globalDialog(page).getByRole('button', { name, exact: true }).focus();
    await page.keyboard.press('Enter');
    await expect(globalDialog(page)).not.toBeVisible();
    if (target === 'topic') {
      await expect(page.getByRole('dialog', { name: '题材详情 · 只读' })).toContainText('materialization_unavailable');
      await expect(page.locator('dialog[open]')).toHaveCount(1);
    } else {
      await expect(page).toHaveURL(new RegExp(`article=${ARTICLE_A}`));
      await expect(page).toHaveURL(/view=news/);
      await expect(page).toHaveURL(new RegExp(`snapshot=${ID}`));
      await expect(page.getByRole('region', { name: '消息详情' })).toContainText(articleA.title);
      await expect(page.locator('dialog[open]')).toHaveCount(0);
    }
  });
}

for (const wrong of ['snapshot', 'date', 'page', 'size', 'query'] as const) {
  test(`search rejects ${wrong} mismatch and does not retain usable stale results`, async ({ page }) => {
    const state: SearchState = { requests: [] };
    await mockIG11(page, state);
    await page.goto(overviewUrl);
    await trigger(page).click();
    await submit(page);
    await expect(globalDialog(page).getByRole('button', { name: '合成测试证券1', exact: true })).toBeVisible();
    state.wrong = wrong;
    await submit(page);
    await expect(globalDialog(page).getByRole('alert')).toBeVisible();
    await expect(globalDialog(page).getByRole('button', { name: '合成测试证券1', exact: true })).toHaveCount(0);
    await expect(globalDialog(page).getByRole('button', { name: '合成归档题材', exact: true })).toHaveCount(0);
  });
}

test('missing group archives disclose reasons while true zero matches remain ready', async ({ page }) => {
  const state: SearchState = { requests: [], missing: true };
  await mockIG11(page, state);
  await page.goto(overviewUrl);
  await trigger(page).click();
  await submit(page);
  await expect(globalDialog(page)).toContainText('topics_materialization_unavailable');
  await expect(globalDialog(page)).toContainText('messages_materialization_unavailable');
  await expect(globalDialog(page)).not.toContainText('没有匹配结果');
  state.missing = false;
  await submit(page, '无匹配');
  await expect(globalDialog(page)).toContainText('没有匹配结果');
  await expect(globalDialog(page)).not.toContainText('materialization_unavailable');
});

test('closing before a late response does not reopen search or target', async ({ page }) => {
  let release!: () => void;
  let settle!: () => void;
  const settled = new Promise<void>(resolve => { settle = resolve; });
  const state: SearchState = {
    requests: [], release: new Promise<void>(resolve => { release = resolve; }), settled: settle,
  };
  await mockIG11(page, state);
  await page.goto(overviewUrl);
  await trigger(page).click();
  await submit(page);
  await expect.poll(() => state.requests.length).toBe(1);
  const completed = page.waitForEvent('requestfailed', {
    predicate: request => new URL(request.url()).pathname === '/api/search',
  });
  await page.keyboard.press('Escape');
  release();
  await completed;
  await settled;
  await expect(trigger(page)).toBeFocused();
  await expect(page.locator('dialog[open]')).toHaveCount(0);
});

test('native dialog is contained on mobile and at enlarged text size', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await mockIG11(page);
  await page.goto(overviewUrl);
  await trigger(page).click();
  await submit(page);
  await page.addStyleTag({ content: 'html { font-size: 200%; }' });
  const dialog = globalDialog(page);
  await expect(dialog).toBeVisible();
  expect(await dialog.evaluate(element => element.getBoundingClientRect().width)).toBeLessThanOrEqual(375);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
  await dialog.getByRole('button', { name: '关闭全局搜索', exact: true }).click();
  await expect(trigger(page)).toBeFocused();
});

async function mockMemberOverview(page: Page) {
  await mockIG11(page);
  const members = securities.slice(0, 63).map((item, index) => ({
    security_id: item.security_id, code: `C${String(63 - index).padStart(3, '0')}`,
    name: index >= 60 ? ['', '   ', null][index - 60] : `Name${String(index).padStart(3, '0')}`,
  }));
  const coverage = { expected: 63, valid: 63, excluded: 0, missing: 0 };
  const ratio = { numerator: 0, denominator: 63, value: 0, reason: null, coverage };
  await page.route('**/api/market/overview?**', route => route.fulfill({ json: {
    ...resetSummary(), data: {
      indexes: [], breadth: {
        up: 40, down: 23, flat: 0, valid_total: 63, coverage,
        members: { up: members.slice(0, 40), down: members.slice(40), flat: [] },
        seal_rate: ratio,
      },
      sentiment: {
        limit_up_count: 0, limit_down_count: 0, max_boards: 0,
        promote_rate: { ...ratio, yesterday_ge2: 0, excluded: 0, missing: 0 },
        yesterday_limit_up_median_return: null,
        yesterday_limit_up_return_coverage: { expected: 0, valid: 0, excluded: 0, missing: 0 },
        yesterday_limit_up_return_reason: 'no_yesterday_limit_up_members',
      },
      hot_sectors: [], factors: {
        'FAC-M01': { advancing: ratio, above_ma20: ratio, method: 'synthetic' },
        'FAC-M02': { value: null, today_total: null, baseline_mean: null, baseline_days: 20,
          baseline_excludes_current: true, coverage, reason: 'not_available', method: 'synthetic' },
        'FAC-M03': { indexes: [], method: 'synthetic' },
      },
      historical_topic_analysis: { enabled: false, reason: 'historical_topic_coverage_unavailable' },
    },
  } }));
}

test('overview member paging binds URL, real count, filter reset and explicit page export scope', async ({ page }) => {
  await mockMemberOverview(page);
  await page.goto(`${overviewUrl}&page=2&page_size=20&sort=security_id&order=asc`);
  const members = page.getByRole('region', { name: '证券筛选结果', exact: true });
  await expect(members.locator('.filter-members > li')).toHaveCount(20);
  await expect(members.locator('.filter-members > li').first()).toContainText('TEST021');
  await expect(members.getByRole('status')).toContainText('共 63 条 · 第 2 / 4 页');
  await members.getByLabel('每页条数').selectOption('50');
  await expect(page).toHaveURL(/page_size=50/);
  await expect(members.locator('.filter-members > li')).toHaveCount(50);
  await members.getByLabel('每页条数').selectOption('100');
  await expect(members.locator('.filter-members > li')).toHaveCount(63);
  await members.getByLabel('每页条数').selectOption('20');
  await members.getByRole('button', { name: '下一页', exact: true }).click();
  await expect(page).toHaveURL(/page=2/);
  await page.getByRole('button', { name: '下跌 23', exact: true }).click();
  await expect(page).toHaveURL(/market_filter=down/);
  await expect(members.getByRole('status')).toContainText('共 23 条 · 第 1 / 2 页');
  await expect(members.locator('.filter-members > li')).toHaveCount(20);
  await expect(page.getByRole('button', { name: '导出当前页 CSV', exact: true })).toBeVisible();
  await expect(page.getByRole('region', { name: '证券行情', exact: true })).toContainText(/导出范围.*当前筛选.*当前页/);
});

for (const order of ['asc', 'desc'] as const) {
  test(`overview name ${order} sorting leaves empty names last and identity sorting uses ID not code`, async ({ page }) => {
    await mockMemberOverview(page);
    await page.goto(`${overviewUrl}&page_size=100&sort=name&order=${order}`);
    const items = page.getByRole('region', { name: '证券筛选结果', exact: true }).locator('.filter-members > li');
    await expect(items).toHaveCount(63);
    await expect(items.first()).toContainText(order === 'asc' ? 'Name000' : 'Name059');
    const text = await items.allTextContents();
    expect(text.slice(-3).every(value => value.includes('名称未知'))).toBe(true);
    expect(text.slice(-3).join(' ')).toContain('TEST063');
    await page.getByLabel('身份排序', { exact: true }).selectOption('security_id');
    await expect(page).toHaveURL(/sort=security_id/);
    await expect(items.first()).toContainText(order === 'asc' ? 'TEST001' : 'TEST063');
    await expect(items.first()).toContainText(order === 'asc' ? 'C063' : 'C001');
  });
}

test('overview member default is bounded to 50 without changing real count', async ({ page }) => {
  await mockMemberOverview(page);
  await page.goto(overviewUrl);
  const members = page.getByRole('region', { name: '证券筛选结果', exact: true });
  await expect(members.locator('.filter-members > li')).toHaveCount(50);
  await expect(members.getByLabel('每页条数')).toHaveValue('50');
  await expect(members.getByRole('status')).toContainText('共 63 条 · 第 1 / 2 页');
});

// SPEC V03: code exists in these archived members. State owner must add the
// code sort enum; IG11 must not silently present security_id sorting as code.
for (const order of ['asc', 'desc'] as const) {
  test(`overview archived code ${order} sort is distinct from security ID`, async ({ page }) => {
    await mockMemberOverview(page);
    await page.goto(`${overviewUrl}&page_size=20&sort=code&order=${order}`);
    const members = page.getByRole('region', { name: '证券筛选结果', exact: true });
    await expect(page).toHaveURL(/sort=code/);
    await expect(page.getByLabel('身份排序', { exact: true })).toHaveValue('code');
    await expect(members.locator('.filter-members > li').first()).toContainText(
      order === 'asc' ? 'TEST063' : 'TEST001',
    );
  });
}

test('search source revocation clears results instead of serving a partial permitted group', async ({ page }) => {
  const state: SearchState = { requests: [] };
  await mockIG11(page, state);
  await page.goto(overviewUrl);
  await trigger(page).click();
  await submit(page);
  await expect(globalDialog(page).getByRole('button', { name: '合成测试证券1', exact: true })).toBeVisible();
  state.denied = true;
  await submit(page);
  await expect(page.getByRole('heading', { name: '来源访问已被拒绝' })).toBeVisible();
  await expect(page.locator('dialog[open]')).toHaveCount(0);
});

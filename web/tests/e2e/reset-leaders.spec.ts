import { expect, test } from '@playwright/test';
import { ID, mockLeaders, type LeaderMockState } from '../fixtures/market';

const leadersUrl = `/?view=leaders&snapshot=${ID}&date=2026-09-10&sector=SEC001&role=trend_leader`;
const allSectorsUrl = `/?view=leaders&snapshot=${ID}&date=2026-09-10&role=trend_leader`;

test('sector matrix never mixes full-market historical counts with filtered ladder rows', async ({ page }) => {
  await mockLeaders(page);
  await page.goto(leadersUrl);

  const matrix = page.getByRole('table', { name: '日期与板数矩阵' });
  await expect(matrix.getByText('2026-09-10', { exact: true })).toBeVisible();
  await expect(matrix.getByText('2026-09-09', { exact: true })).toHaveCount(0);
  await expect(matrix.getByText('99 家', { exact: true })).toHaveCount(0);
  await expect(page.getByText('题材筛选下仅展示所选日期的可验证梯队；历史题材矩阵不可用。', { exact: true })).toBeVisible();
});

test('renders the date-by-board matrix, tied candidates and honest comparison evidence', async ({ page }) => {
  await mockLeaders(page);
  await page.goto(leadersUrl);

  await expect(page.getByRole('heading', { name: '梯队', exact: true })).toBeVisible();
  const matrix = page.getByRole('table', { name: '日期与板数矩阵' });
  await expect(matrix).toBeVisible();
  await expect(matrix.getByRole('button', { name: '合成测试证券1', exact: true })).toBeVisible();
  const candidates = page.getByRole('table', { name: '角色候选' });
  await expect(candidates.getByText('并列 1')).toHaveCount(2);
  await expect(candidates.getByRole('button').nth(0)).toHaveAccessibleName('合成测试证券1');
  await expect(candidates.getByRole('button').nth(1)).toHaveAccessibleName('合成测试证券2');
  await expect(page.getByText('不可比较', { exact: true })).toBeVisible();
  await expect(page.getByText('规则版本已变化')).toBeVisible();
  await expect(page.getByText('题材成员已变化')).toBeVisible();
  await expect(page.getByText('盘中封板字段不可用', { exact: true })).toBeVisible();
  await expect(page.getByText('rule_version_changed', { exact: true })).toBeHidden();
});

test('matrix securities share one drawer and expose six traceable evidence groups', async ({ page }) => {
  await mockLeaders(page);
  await page.goto(leadersUrl);

  await page.getByRole('table', { name: '日期与板数矩阵' })
    .getByRole('button', { name: '合成测试证券1', exact: true }).click();
  await expect(page.getByRole('dialog')).toHaveCount(1);
  await expect(page.getByRole('dialog').getByRole('heading', { name: '合成测试证券1' })).toBeVisible();
  await expect(page.getByRole('dialog').getByRole('heading', { name: '六类角色证据' })).toBeVisible();
  await expect(page.getByRole('dialog').getByRole('row', { name: /FAC-R01/ })).toBeVisible();
  await expect(page.getByRole('dialog').getByText('2026-09-10 15:30:00', { exact: true }).first()).toBeVisible();
  await expect(page.getByRole('dialog').getByText('相对强度为正', { exact: true })).toBeVisible();
  await expect(page.getByRole('dialog').getByText('落后于唯一核心标的', { exact: true })).toBeVisible();
  await expect(page.getByRole('dialog').getByText('盘中封板明细不可用', { exact: true })).toBeVisible();
  const dialog = page.getByRole('dialog');
  expect(await dialog.evaluate(element => (element as HTMLElement).innerText)).not.toContain('relative_strength_positive');
  const trendEvidence = dialog.locator('.role-evidence').filter({ hasText: '趋势龙头' });
  await trendEvidence.getByText('原始证据').click();
  await expect(trendEvidence.locator('pre')).toContainText('relative_strength_positive');

  await page.getByRole('table', { name: '角色候选' })
    .getByRole('button', { name: '合成测试证券2', exact: true })
    .evaluate((button: HTMLButtonElement) => button.click());
  await expect(page.getByRole('dialog')).toHaveCount(1);
  await expect(page.getByRole('dialog').getByRole('heading', { name: '合成测试证券2' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.getByRole('table', { name: '角色候选' })
    .getByRole('button', { name: '合成测试证券2', exact: true })).toBeFocused();
});

test('all-sector matrix and candidate rows carry their own evidence sector without changing the filter', async ({ page }) => {
  const evidenceRequests: string[] = [];
  await mockLeaders(page);
  page.on('request', request => {
    const url = new URL(request.url());
    if (url.pathname === '/api/role/evidence') {
      evidenceRequests.push(`${url.searchParams.get('security_id')}:${url.searchParams.get('sector')}`);
    }
  });
  await page.goto(allSectorsUrl);

  await page.getByRole('table', { name: '日期与板数矩阵' })
    .getByRole('button', { name: '合成测试证券1', exact: true }).click();
  await expect(page.getByRole('dialog').getByRole('heading', { name: '六类角色证据' })).toBeVisible();
  await expect.poll(() => evidenceRequests).toContain('TEST001:SEC001');

  await page.getByRole('table', { name: '角色候选' })
    .getByRole('button', { name: '合成测试证券2', exact: true })
    .evaluate((button: HTMLButtonElement) => button.click());
  await expect(page.getByRole('dialog')).toHaveCount(1);
  await expect(page.getByRole('dialog').getByRole('heading', { name: '合成测试证券2' })).toBeVisible();
  await expect.poll(() => evidenceRequests).toContain('TEST002:SEC001');
  await expect(page).not.toHaveURL(/sector=/);
});

test('renders role change details even when comparison summaries are empty', async ({ page }) => {
  await mockLeaders(page, { roleChangesOnly: true });
  await page.goto(leadersUrl);

  const changes = page.getByRole('table', { name: '角色变化明细' });
  await expect(changes).toBeVisible();
  await expect(changes.getByRole('row', { name: /TEST001.*新增.*趋势龙头.*可比较.*进入排名/ })).toBeVisible();
});

test('all topics use the weakest status while a selected sector uses only its own status', async ({ page }) => {
  await mockLeaders(page, { mixedStatuses: true });
  await page.goto(allSectorsUrl);

  await expect(page.locator('.leaders-controls .status-token')).toHaveText('证据不足');
  const topicDetails = page.getByText('逐题材状态与原始依据');
  await topicDetails.click();
  await expect(page.getByRole('row', { name: /SEC001.*可正式候选/ })).toBeVisible();
  await expect(page.getByRole('row', { name: /SEC002.*证据不足.*必要输入缺失/ })).toBeVisible();
  await expect(page.getByText('有效 2 / 应有 4 · 排除 1 · 缺失 1', { exact: true })).toBeVisible();

  await page.getByLabel('题材筛选').selectOption('SEC001');
  await expect(page.locator('.leaders-controls .status-token')).toHaveText('可正式候选');
  await expect(page.getByText('角色输入不足', { exact: true })).toHaveCount(0);
});

test('reset stock 404 is shown directly and never falls back to the legacy endpoint', async ({ page }) => {
  const legacyStockRequests: string[] = [];
  await mockLeaders(page, { stockNotFound: true });
  page.on('request', request => {
    if (new URL(request.url()).pathname.includes('/api/v1/snapshots/') && request.url().includes('/securities/')) {
      legacyStockRequests.push(request.url());
    }
  });
  await page.goto(leadersUrl);
  await page.getByRole('table', { name: '角色候选' })
    .getByRole('button', { name: '合成测试证券1', exact: true }).click();

  await expect(page.getByRole('dialog').getByRole('alert')).toContainText('未找到该快照或证券');
  expect(legacyStockRequests).toEqual([]);
});

test('malformed reset stock response uses the public error state and clears stale detail', async ({ page }) => {
  const state: LeaderMockState = {};
  await mockLeaders(page, state);
  await page.goto(leadersUrl);
  await page.getByRole('table', { name: '角色候选' })
    .getByRole('button', { name: '合成测试证券1', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByRole('heading', { name: '合成测试证券1' })).toBeVisible();

  state.malformedStock = true;
  await page.getByRole('button', { name: '刷新快照' }).evaluate((button: HTMLButtonElement) => button.click());
  await expect(dialog.getByRole('alert')).toContainText('证券详情加载失败：数据格式不完整。');
  await expect(dialog.getByRole('heading', { name: '合成测试证券1' })).toHaveCount(0);
  await expect(dialog.getByText('不应泄露的旧证券数据')).toHaveCount(0);
  await expect(page.getByRole('heading', { name: '我的复盘', exact: true })).toHaveCount(0);
  await expect(page.locator('main')).toHaveCount(1);
});

test('candidate coverage discloses valid, expected, excluded and missing counts', async ({ page }) => {
  await mockLeaders(page);
  await page.goto(leadersUrl);

  await expect(page.getByText('有效 2 / 应有 4 · 排除 1 · 缺失 1', { exact: true })).toBeVisible();
});

test('preserves leaders filters through drawer, local watchlist and a bound note', async ({ page }) => {
  await mockLeaders(page);
  await page.goto(leadersUrl);
  const original = new URL(page.url()).search;

  await page.getByRole('table', { name: '角色候选' })
    .getByRole('button', { name: '合成测试证券1', exact: true }).click();
  const drawer = page.getByRole('dialog');
  await drawer.getByRole('button', { name: '加入本地自选', exact: true }).click();
  await drawer.getByRole('button', { name: '记录本地判断', exact: true }).click();
  await expect(page).toHaveURL(/view=review/);
  await expect(page).toHaveURL(/return_view=leaders/);
  await expect(page.getByTestId('local-watchlist')).toContainText('TEST001');

  await page.getByLabel('标题', { exact: true }).fill('梯队判断');
  await page.getByRole('textbox', { name: '判断', exact: true }).fill('观察角色延续');
  await page.getByRole('button', { name: '保存笔记', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: '回读验证通过' })).toBeVisible();
  expect(await page.evaluate(async () => {
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      const opening = indexedDB.open('dragon-radar-local-v3');
      opening.onsuccess = () => resolve(opening.result);
      opening.onerror = () => reject(opening.error);
    });
    const query = db.transaction('notes').objectStore('notes').getAll();
    const notes = await new Promise<Record<string, unknown>[]>((resolve, reject) => {
      query.onsuccess = () => resolve(query.result);
      query.onerror = () => reject(query.error);
    });
    db.close();
    return notes.map(note => note.security_id);
  })).toEqual(['TEST001']);

  await page.getByRole('link', { name: '返回梯队' }).click();
  await expect(page).toHaveURL(url => url.search === original);
});

test('distinguishes no candidate, insufficient, unavailable, forbidden and offline states', async ({ page }) => {
  const state: LeaderMockState = { noCandidate: true };
  await mockLeaders(page, state);
  await page.goto(leadersUrl);
  await expect(page.getByText('完整输入下暂无候选', { exact: true })).toBeVisible();
  await expect(page.getByText('合成测试证券1', { exact: true })).toHaveCount(1);

  state.noCandidate = false;
  state.insufficient = true;
  await page.getByRole('button', { name: /刷新/ }).first().click();
  await expect(page.getByText('角色输入不足', { exact: true })).toBeVisible();
  await expect(page.getByRole('table', { name: '角色候选' })).toHaveCount(0);

  state.insufficient = false;
  state.unapproved = true;
  await page.getByRole('button', { name: /刷新/ }).first().click();
  await expect(page.getByText('规则尚未批准', { exact: true })).toBeVisible();
  await expect(page.getByRole('table', { name: '角色候选' })).toHaveCount(0);

  state.unapproved = false;
  state.domainUnavailable = true;
  await page.getByRole('button', { name: /刷新/ }).first().click();
  await expect(page.getByText('角色域未发布', { exact: true })).toBeVisible();

  state.domainUnavailable = false;
  state.denied = true;
  await page.getByRole('button', { name: /刷新/ }).first().click();
  await expect(page.getByRole('heading', { name: '来源访问已被拒绝' })).toBeVisible();
  await expect(page.getByText('合成测试证券1', { exact: true })).toHaveCount(0);
});

test('offline refresh retains only same-selection stale data and desktop/mobile never overflow', async ({ page }) => {
  const state: LeaderMockState = {};
  await mockLeaders(page, state);
  await page.goto(leadersUrl);
  await expect(page.getByRole('table', { name: '角色候选' })).toBeVisible();
  await page.screenshot({ path: '.temp/reset-leaders/desktop.png', fullPage: true });

  await page.setViewportSize({ width: 375, height: 812 });
  await page.screenshot({ path: '.temp/reset-leaders/mobile.png', fullPage: true });
  const dimensions = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    page: document.documentElement.scrollWidth,
  }));
  expect(dimensions.page).toBeLessThanOrEqual(dimensions.viewport);

  state.offline = true;
  await page.getByRole('button', { name: /刷新/ }).first().click();
  await expect(page.getByRole('alert').filter({ hasText: '当前保留上次成功数据' }).first()).toBeVisible();
  await expect(page.getByRole('table', { name: '角色候选' })).toBeVisible();
  await page.getByLabel('角色筛选').selectOption('zhongjun');
  await expect(page.getByRole('table', { name: '角色候选' })).toHaveCount(0);
});

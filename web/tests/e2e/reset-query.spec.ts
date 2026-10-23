import { expect, test, type Page } from '@playwright/test';

import { ID, OLD, mockMarket } from '../fixtures/market';

// Task 26 slice 4：白名单查询面板 E2E。后端 EX12/EX13 路由尚不存在，query 端点另立
// page.route('**/api/query/**') mock，请求/响应严格按 EX12-13 冻结契约构造。
const QUERY_ID = '11111111-1111-4111-8111-111111111111';
const HASH = 'a'.repeat(64);
const ANALYSIS_URL = `/?view=analysis&snapshot=${ID}&date=2026-09-10`;

const fieldDescs = [
  { key: 'security_id', label: '代码' },
  { key: 'name', label: '名称' },
  { key: 'sector_ids', label: '题材' },
  { key: 'roles', label: '角色' },
  { key: 'boards', label: '板数' },
  { key: 'return_ratio', label: '涨幅' },
];

function parseSuccess(): Record<string, unknown> {
  return {
    query_id: QUERY_ID,
    typed_ast: {
      version: 1,
      ast_hash: HASH,
      predicates: [{ field: 'return_ratio', op: 'gt', value: 0.05 }],
      sort: { field: 'return_ratio', direction: 'desc' },
      limit: 50,
    },
    explanation: '涨幅大于5%；按涨幅降序排列；最多返回 50 条。',
    unsupported: [],
  };
}

function parseUnsupported(): Record<string, unknown> {
  return {
    query_id: null,
    typed_ast: null,
    explanation: null,
    unsupported: [{ code: 'unknown_field', token: '市值', reason: '字段不在白名单内' }],
  };
}

function executeEnvelope(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    trade_date: '2026-09-10',
    as_of: '2026-09-10T08:01:00Z',
    snapshot_id: ID,
    source: 'analysis_materialization',
    source_version: 'analysis-v1',
    coverage: { expected: 65, valid: 3, excluded: 60, missing: 2 },
    quality_flags: [],
    rule_version: 'role-v1',
    basis: {
      unit: 'ratio',
      currency: 'CNY',
      price_type: 'raw_close',
      benchmark: 'B000300',
      adjustment_version: 'adj-v1',
      rounding: 'ROUND_HALF_UP',
    },
    data: {
      fields: fieldDescs,
      rows: [
        {
          security_id: 'TEST001',
          name: '合成测试证券1',
          sector_ids: ['SEC001'],
          roles: ['trend_leader'],
          boards: 2,
          return_ratio: 0.0523,
        },
        {
          security_id: 'TEST002',
          name: null,
          sector_ids: [],
          roles: [],
          boards: null,
          return_ratio: null,
        },
      ],
      total: 3,
      unavailable: [],
    },
    ...overrides,
  };
}

function executeEmpty(): Record<string, unknown> {
  return executeEnvelope({
    coverage: { expected: 65, valid: 0, excluded: 63, missing: 2 },
    data: {
      fields: fieldDescs,
      rows: [],
      total: 0,
      unavailable: [
        { code: 'return_ratio_unavailable', reason: 'insufficient_calendar_history' },
      ],
    },
  });
}

// ResetAnalysis 自身的排行接口：data:null 退化为「当前区间分析不可用」，不产生错误告警。
async function mockRankNull(page: Page) {
  await page.route('**/api/rank/**', route => route.fulfill({
    json: {
      trade_date: '2026-09-10',
      as_of: '2026-09-10T08:01:00Z',
      snapshot_id: ID,
      source: 'analysis_materialization',
      source_version: 'analysis-v1',
      coverage: { expected: 65, valid: 0, excluded: 65, missing: 0 },
      quality_flags: [],
      rule_version: 'role-v1',
      basis: { unit: 'ratio', currency: 'CNY', price_type: 'raw_close', benchmark: 'B000300' },
      data: null,
    },
  }));
}

type Supplier<T> = T | (() => T);
function resolve<T>(value: Supplier<T>): T {
  return typeof value === 'function' ? (value as () => T)() : value;
}

async function mockQuery(page: Page, options: {
  parseBody?: Supplier<unknown>;
  parseStatus?: Supplier<number>;
  executeBody?: Supplier<unknown>;
  executeStatus?: Supplier<number>;
} = {}) {
  const counts = { parse: 0, execute: 0 };
  const executeBodies: unknown[] = [];
  await page.route('**/api/query/parse', async route => {
    counts.parse += 1;
    const status = options.parseStatus === undefined ? 200 : resolve(options.parseStatus);
    const body = options.parseBody === undefined ? parseSuccess() : resolve(options.parseBody);
    await route.fulfill({
      status,
      contentType: 'application/json',
      body: JSON.stringify(body),
    });
  });
  await page.route('**/api/query/execute', async route => {
    counts.execute += 1;
    executeBodies.push(route.request().postDataJSON());
    const status = options.executeStatus === undefined ? 200 : resolve(options.executeStatus);
    const body = options.executeBody === undefined ? executeEnvelope() : resolve(options.executeBody);
    await route.fulfill({
      status,
      contentType: 'application/json',
      body: JSON.stringify(body),
    });
  });
  return { counts, executeBodies };
}

async function gotoAnalysis(page: Page, url = ANALYSIS_URL) {
  await mockMarket(page);
  await mockRankNull(page);
  await page.goto(url);
  await expect(page.getByLabel('快照版本')).toHaveValue(ID);
  const panel = page.getByRole('region', { name: '白名单查询面板' });
  await expect(panel).toBeVisible();
  return panel;
}

test('解析预览出现前不执行；取消/修改不发 execute 且焦点回到解析按钮', async ({ page }) => {
  const query = await mockQuery(page);
  const panel = await gotoAnalysis(page);
  expect(query.counts.execute).toBe(0);

  const textbox = panel.getByRole('textbox', { name: '查询语句' });
  await textbox.fill('涨幅大于5%');
  await panel.getByRole('button', { name: '解析' }).click();

  const preview = panel.getByRole('group', { name: '查询预览' });
  await expect(preview).toBeVisible();
  await expect(preview).toContainText('涨幅大于5%');
  await expect(preview).toContainText('涨幅 从高到低');
  await expect(preview).toContainText('最多返回 50 条');
  await expect(preview).toContainText('raw_close');
  expect(query.counts.execute).toBe(0);

  await panel.getByRole('button', { name: '取消/修改' }).click();
  await expect(preview).toHaveCount(0);
  expect(query.counts.execute).toBe(0);
  await expect(panel.getByRole('button', { name: '解析' })).toBeFocused();
  await expect(textbox).toHaveValue('涨幅大于5%');
});

test('显式确认才执行；execute body 闭集钉住快照；改文案后确认态作废', async ({ page }) => {
  const query = await mockQuery(page);
  const panel = await gotoAnalysis(page);
  const textbox = panel.getByRole('textbox', { name: '查询语句' });
  const executeButton = panel.getByRole('button', { name: '执行查询' });

  await textbox.fill('涨幅大于5%');
  await panel.getByRole('button', { name: '解析' }).click();
  await expect(panel.getByRole('group', { name: '查询预览' })).toBeVisible();
  expect(query.counts.execute).toBe(0);

  await executeButton.click();
  const table = panel.getByRole('table', { name: '查询结果' });
  await expect(table).toBeVisible();
  expect(query.counts.execute).toBe(1);
  const body = query.executeBodies[0] as Record<string, unknown>;
  expect(Object.keys(body).sort()).toEqual(
    ['confirmed_ast_hash', 'idempotency_key', 'query_id', 'snapshot_id'].sort(),
  );
  expect(body).toMatchObject({ query_id: QUERY_ID, confirmed_ast_hash: HASH, snapshot_id: ID });
  expect(body).not.toHaveProperty('date');
  expect(body.idempotency_key).toMatch(/^[\da-f]{8}-[\da-f]{4}-[1-5][\da-f]{3}-[89ab][\da-f]{3}-[\da-f]{12}$/i);

  const row = table.locator('tbody tr').first();
  await expect(row.getByRole('button', { name: 'TEST001' })).toBeVisible();
  await expect(row.locator('td').nth(0)).toHaveText('合成测试证券1');
  await expect(row.locator('td').nth(1)).toHaveText('SEC001');
  await expect(row.locator('td').nth(2)).toHaveText('趋势');
  await expect(row.locator('td').nth(3)).toHaveText('2');
  await expect(row.locator('td').nth(4)).toHaveText('+5.23%');
  await expect(panel).toContainText('结果快照 2026-09-10 · v1');

  await textbox.fill('涨幅大于5%，连板数大于2');
  await expect(panel.getByRole('alert').filter({ hasText: '查询文案已修改' })).toBeVisible();
  await expect(executeButton).toBeDisabled();
  expect(query.counts.execute).toBe(1);

  await panel.getByRole('button', { name: '解析' }).click();
  await expect(executeButton).toBeEnabled();
  expect(query.counts.execute).toBe(1);
});

test('结果绑定执行时快照：改日期出现旧快照条，回到原快照恢复 URL 且结果保留', async ({ page }) => {
  const query = await mockQuery(page);
  const panel = await gotoAnalysis(page);
  await panel.getByRole('textbox', { name: '查询语句' }).fill('涨幅大于5%');
  await panel.getByRole('button', { name: '解析' }).click();
  await panel.getByRole('button', { name: '执行查询' }).click();
  const table = panel.getByRole('table', { name: '查询结果' });
  await expect(table).toBeVisible();
  expect(query.counts.execute).toBe(1);
  const resultRow = table.locator('tbody tr').first();
  const securityButton = resultRow.getByRole('button', { name: 'TEST001' });

  await page.getByLabel('交易日期').fill('2026-09-09');
  await expect(page).toHaveURL(new RegExp(`snapshot=${OLD}`));
  const bindingBar = panel.getByRole('status').filter({ hasText: '结果绑定旧快照' });
  await expect(bindingBar).toBeVisible();
  // F1：漂移态结果行的安全入口禁用，tr 不再带 clickable-row（行点击被门控）。
  await expect(securityButton).toBeDisabled();
  await expect(resultRow).not.toHaveClass(/clickable-row/);

  await panel.getByRole('button', { name: '回到原快照' }).click();
  await expect(page).toHaveURL(new RegExp(`snapshot=${ID}`));
  await expect(page).toHaveURL(/date=2026-09-10/);
  await expect(bindingBar).toHaveCount(0);
  await expect(table).toBeVisible();
  await expect(panel).toContainText('结果快照 2026-09-10 · v1');
  // 回到原快照后安全入口恢复可点、行点击恢复。
  await expect(securityButton).toBeEnabled();
  await expect(resultRow).toHaveClass(/clickable-row/);
});

test('第二次解析阻塞期间执行查询按钮保持 disabled（parsing 中不可执行）', async ({ page }) => {
  let parseCalls = 0;
  const parseGate: { release?: () => void } = {};
  await page.route('**/api/query/parse', async route => {
    parseCalls += 1;
    if (parseCalls === 1) return route.fulfill({ json: parseSuccess() });
    // 第二次 parse 阻塞：显式放行前永不返回。
    await new Promise<void>(resolve => { parseGate.release = resolve; });
    return route.fulfill({ json: parseSuccess() });
  });
  await page.route('**/api/query/execute', route => route.fulfill({ json: executeEnvelope() }));
  const panel = await gotoAnalysis(page);
  const textbox = panel.getByRole('textbox', { name: '查询语句' });
  const executeButton = panel.getByRole('button', { name: '执行查询' });

  await textbox.fill('涨幅大于5%');
  await panel.getByRole('button', { name: '解析' }).click();
  await expect(panel.getByRole('group', { name: '查询预览' })).toBeVisible();
  await expect(executeButton).toBeEnabled();

  // 同文案二次解析（不改文案）：textStale 为假，按钮失活只能由 busy==='parsing'
  // 解释，避免 stale 态与 busy 守卫混淆、保证对 P2-3 修复的区分度。
  await panel.getByRole('button', { name: '解析' }).click();
  await expect(panel.getByRole('status').filter({ hasText: '正在解析查询语句' })).toBeVisible();
  await expect(panel.getByRole('button', { name: '解析中…' })).toBeVisible();
  // P2-3：parsing 中执行按钮不得可点。
  await expect(executeButton).toBeDisabled();

  parseGate.release?.();
  await expect(executeButton).toBeEnabled();
});

test('执行中按 Esc 不清空确认卡；阻塞放行后结果表正常出现', async ({ page }) => {
  await page.route('**/api/query/parse', route => route.fulfill({ json: parseSuccess() }));
  const executeGate: { release?: () => void } = {};
  await page.route('**/api/query/execute', async route => {
    // execute 阻塞：显式放行前永不返回。
    await new Promise<void>(resolve => { executeGate.release = resolve; });
    await route.fulfill({ json: executeEnvelope() });
  });
  const panel = await gotoAnalysis(page);
  const preview = panel.getByRole('group', { name: '查询预览' });
  await panel.getByRole('textbox', { name: '查询语句' }).fill('涨幅大于5%');
  await panel.getByRole('button', { name: '解析' }).click();
  await expect(preview).toBeVisible();

  await panel.getByRole('button', { name: '执行查询' }).click();
  await expect(panel.getByRole('button', { name: '执行中…' })).toBeVisible();
  await expect(panel.getByRole('table', { name: '查询结果' })).toHaveCount(0);

  // P2-4：执行进行中 Esc 不得清空确认卡（取消按钮在执行中同样禁用）。
  await page.keyboard.press('Escape');
  await expect(preview).toBeVisible();
  await expect(panel.getByRole('button', { name: '取消/修改' })).toBeDisabled();
  await expect(panel.getByRole('table', { name: '查询结果' })).toHaveCount(0);

  executeGate.release?.();
  await expect(panel.getByRole('table', { name: '查询结果' })).toBeVisible();
});

test('unsupported 原因列表、409/403/413 文案与空结果声明性不可用如实展示', async ({ page }) => {
  let parseCalls = 0;
  await page.route('**/api/query/parse', async route => {
    parseCalls += 1;
    await route.fulfill({ json: parseCalls === 1 ? parseUnsupported() : parseSuccess() });
  });
  let executeCalls = 0;
  await page.route('**/api/query/execute', async route => {
    executeCalls += 1;
    if (executeCalls === 1) {
      return route.fulfill({ status: 409, json: { code: 'VERSION_CONFLICT' } });
    }
    if (executeCalls === 2) {
      return route.fulfill({ status: 403, json: { code: 'FORBIDDEN' } });
    }
    if (executeCalls === 3) {
      return route.fulfill({ status: 413, json: { code: 'INVALID_PARAM' } });
    }
    return route.fulfill({ json: executeEmpty() });
  });
  const panel = await gotoAnalysis(page);
  const textbox = panel.getByRole('textbox', { name: '查询语句' });
  const executeButton = panel.getByRole('button', { name: '执行查询' });

  await textbox.fill('市值大于100亿');
  await panel.getByRole('button', { name: '解析' }).click();
  const unsupportedAlert = panel.getByRole('alert').filter({ hasText: '该语句暂不支持' });
  await expect(unsupportedAlert).toBeVisible();
  await expect(unsupportedAlert).toContainText('未知字段');
  await expect(unsupportedAlert).toContainText('市值');
  await expect(unsupportedAlert).toContainText('字段不在白名单内');
  await expect(panel.getByRole('group', { name: '查询预览' })).toHaveCount(0);
  expect(executeButton).toHaveCount(0);

  await panel.getByRole('button', { name: '解析' }).click();
  await expect(panel.getByRole('group', { name: '查询预览' })).toBeVisible();

  await executeButton.click();
  await expect(panel.getByRole('alert').filter({ hasText: '查询确认已失效' })).toBeVisible();
  await executeButton.click();
  await expect(panel.getByRole('alert').filter({ hasText: '当前会话无权在此快照上执行查询' })).toBeVisible();
  await executeButton.click();
  await expect(panel.getByRole('alert').filter({ hasText: '查询请求体积超过上限' })).toBeVisible();

  await executeButton.click();
  await expect(panel.getByRole('table', { name: '查询结果' })).toBeVisible();
  await expect(panel.getByText('没有符合白名单条件的记录。')).toBeVisible();
  const unavailable = panel.getByRole('status').filter({ hasText: '部分口径声明性不可用' });
  await expect(unavailable).toContainText('return_ratio_unavailable');
  await expect(unavailable).toContainText('insufficient_calendar_history');
  await expect(panel).toContainText('共 0 条；本次返回 0 条（单次上限 100 条）。');
});

test('全键盘路径：IME 组合期 Enter 不解析，Esc 取消并焦点回触发器，执行后焦点入结果', async ({ page }) => {
  const query = await mockQuery(page);
  const panel = await gotoAnalysis(page);
  const textbox = panel.getByRole('textbox', { name: '查询语句' });
  const parseButton = panel.getByRole('button', { name: '解析' });
  const executeButton = panel.getByRole('button', { name: '执行查询' });

  await textbox.click();
  await textbox.fill('涨幅大于5%');
  // IME 组合中的 Enter（isComposing + keyCode 229）不得触发解析。
  await textbox.evaluate(el => {
    const event = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true });
    Object.defineProperty(event, 'keyCode', { value: 229 });
    Object.defineProperty(event, 'isComposing', { value: true });
    el.dispatchEvent(event);
  });
  expect(query.counts.parse).toBe(0);
  // Shift+Enter 仅换行。
  await page.keyboard.press('Shift+Enter');
  expect(query.counts.parse).toBe(0);

  await page.keyboard.press('Tab');
  await expect(parseButton).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(panel.getByRole('group', { name: '查询预览' })).toBeVisible();
  expect(query.counts.parse).toBe(1);
  await expect(executeButton).toBeFocused();

  await page.keyboard.press('Escape');
  await expect(panel.getByRole('group', { name: '查询预览' })).toHaveCount(0);
  await expect(parseButton).toBeFocused();

  await page.keyboard.press('Enter');
  await expect(executeButton).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(panel.getByRole('table', { name: '查询结果' })).toBeVisible();
  await expect(panel.getByRole('heading', { name: '查询结果' })).toBeFocused();
  expect(query.counts.execute).toBe(1);
});

test('深色主题下 375px 视口结果表格无页面级横向溢出', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.addInitScript(() => window.localStorage.setItem('dr:theme', 'dark'));
  await mockQuery(page);
  const panel = await gotoAnalysis(page);

  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  const textbox = panel.getByRole('textbox', { name: '查询语句' });
  const backgroundColor = await textbox.evaluate(el => getComputedStyle(el).backgroundColor);
  expect(backgroundColor).not.toBe('rgb(255, 255, 255)');

  await textbox.fill('涨幅大于5%');
  await panel.getByRole('button', { name: '解析' }).click();
  await panel.getByRole('button', { name: '执行查询' }).click();
  await expect(panel.getByRole('table', { name: '查询结果' })).toBeVisible();

  const dimensions = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    page: document.documentElement.scrollWidth,
  }));
  expect(dimensions.page).toBeLessThanOrEqual(dimensions.viewport);
});

test('未选快照时面板禁用并提示，且不产生任何 query 请求', async ({ page }) => {
  const query = await mockQuery(page);
  await mockMarket(page);
  await mockRankNull(page);
  await page.goto('/?view=analysis&date=2030-01-01');
  const panel = page.getByRole('region', { name: '白名单查询面板' });
  await expect(panel).toBeVisible();
  await expect(panel.getByRole('textbox', { name: '查询语句' })).toBeDisabled();
  await expect(panel.getByRole('button', { name: '解析' })).toBeDisabled();
  await expect(panel.getByRole('status').filter({ hasText: '请先选择交易日期与快照版本' })).toBeVisible();
  expect(query.counts.parse + query.counts.execute).toBe(0);
});

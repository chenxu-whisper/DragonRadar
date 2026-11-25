import { expect, test, type Locator, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fixture, seedLocal, localRecords, nondefaultUnits, mixedLhb, roleBody, OLD, H, DATE, sessions, url, historicalSelection, resetSummary, d04Fixture } from '../fixtures/b8-residual';
import { parseCsv } from '../../scripts/contract-c2-support.mjs';
import { securities } from '../fixtures/market';
import { articles } from '../fixtures/contract-c2-scenes';

async function geometry(rows: Locator, label: string, width: number, density: string) {
  await expect(rows.first()).toBeVisible();
  await expect(rows.filter({ hasText: '正在加载本地操作' })).toHaveCount(0);
  await rows.first().scrollIntoViewIfNeeded();
  const values = await rows.evaluateAll(elements => elements.map(element => {
    const rect = element.getBoundingClientRect();
    const identity = element.querySelector('th[data-label="证券"],th,.security-button,strong,button,small') || element;
    const identityRect = identity.getBoundingClientRect();
    const wrapper = element.closest('.table-scroll') as HTMLElement | null;
    const buttons = [...element.querySelectorAll('button')];
    if (element instanceof HTMLButtonElement) buttons.push(element);
    const actionable = buttons.map(button => button.getBoundingClientRect().height);
    return { text: element.textContent?.trim(), height: rect.height,
      identity: identity.textContent?.trim(), identityLeft: identityRect.left, identityWidth: identityRect.width,
      identityPosition: getComputedStyle(identity).position,
      rowToken: getComputedStyle(document.documentElement).getPropertyValue('--row-h').trim(),
      scroll: wrapper ? { width: wrapper.clientWidth, full: wrapper.scrollWidth } : null,
      actionable, display: getComputedStyle(element).display,
      group: element.closest('[role="region"],section[aria-label]')?.getAttribute('aria-label'),
      ariaLabel: element.getAttribute('aria-label') };
  }));
  const first = values[0];
  expect.soft(first.identity, `${label} archived identity`).toBeTruthy();
  expect.soft(first.rowToken, `${label} declared density`).toBe(density === 'compact' ? '36px' : '44px');
  for (const row of values) {
    expect.soft(row.identityLeft, `${label} each identity within viewport`).toBeGreaterThanOrEqual(-1);
    expect.soft(row.identityLeft + row.identityWidth, `${label} each identity within viewport`).toBeLessThanOrEqual(width + 1);
  }
  for (const height of first.actionable) expect.soft(height, `${label} actionable target`).toBeGreaterThanOrEqual(44);
  const wrapper = rows.first().locator('xpath=ancestor::div[contains(@class,"table-scroll")]').first();
  let scrollEvidence: unknown = { applicable: false, reason: 'non-table or no actual horizontal overflow' };
  if (first.scroll && first.scroll.full > first.scroll.width + 1) {
    await wrapper.evaluate(element => { element.scrollLeft = 100; });
    const after = await rows.first().evaluate(element => {
      const identity = element.querySelector('th[data-label="证券"],th,.security-button,strong,button,small') || element;
      return { left: identity.getBoundingClientRect().left, text: identity.textContent?.trim() };
    });
    scrollEvidence = { applicable: true, before: first.identityLeft, after };
    expect.soft(after.text).toBe(first.identity);
    expect.soft(Math.abs(after.left - first.identityLeft), `${label} actual scrolling identity stays fixed`).toBeLessThanOrEqual(1);
    await wrapper.evaluate(element => { element.scrollLeft = 0; });
  }
  const declared = density === 'compact' ? 36 : 44;
  const exception = Math.abs(first.height - declared) <= 1 ? null
    : first.actionable.length ? '44px actionable target and/or multiline identity'
      : width === 375 && first.display !== 'table-row' ? 'mobile record reflow'
        : 'multiline evidence/content: exact measured height recorded; parent acceptance required';
  expect.soft(first.height).toBeGreaterThanOrEqual(declared - 1);
  return { label, width, density, records: values, exception, scrollEvidence };
}
async function pageChange(region: Locator, rows: Locator) {
  const before = await rows.first().textContent();
  await region.getByRole('combobox', { name: '每页条数', exact: true }).selectOption('20');
  await region.getByRole('button', { name: '下一页', exact: true }).click();
  await expect(rows.first()).not.toHaveText(before || '');
}
async function recordCell(receipts: unknown[], rows: Locator, label: string, width: number,
  density: string, change: () => Promise<void>) {
  const cell = { label, width, density, before: await geometry(rows, label, width, density), after: null as unknown };
  receipts.push(cell);
  await change();
  cell.after = await geometry(rows, label, width, density);
  const all = label === 'overview members' ? securities.slice(0, 63).filter(row => Number(row.change) > 0)
    : label === 'news' ? articles : label === 'expanded stock history' ? sessions
      : securities.slice(0, 25);
  const assertIdentity = (record: { records: { text?: string }[] }, offset: number) => {
    expect.soft(record.records).toHaveLength(all.slice(offset).length);
    for (const [index, row] of record.records.entries()) {
      const expected = all[index + offset];
      const identity = typeof expected === 'string' ? expected
        : 'title' in expected ? expected.title : expected.security_id;
      expect.soft(row.text, `${label} exact ordered identity ${index + offset}`).toContain(identity);
      if (typeof expected !== 'string' && 'security_id' in expected) {
        const name = label === 'topic members' ? `B8 member ${index + offset + 1}`
          : label === 'global search' ? `B8 search ${index + offset + 1}` : expected.name;
        expect.soft(row.text, `${label} exact archived name ${index + offset}`).toContain(name || '');
      }
    }
  };
  assertIdentity(cell.before, 0);
  assertIdentity(cell.after as typeof cell.before, 20);
}
async function openSearch(page: Page, query: string) {
  await page.getByRole('button', { name: '全局搜索', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '全局搜索 · 发布归档' });
  await dialog.getByLabel('搜索发布归档').fill(query);
  await dialog.getByRole('button', { name: '搜索归档' }).click();
  return dialog;
}

test('B8-LIST-01 eight lists at 1440/375 compact/comfortable with measured exceptions', async ({ page }, info) => {
  await fixture(page);
  const receipts: unknown[] = [];
  try {
    for (const width of [1440, 375]) for (const density of ['compact', 'comfortable']) {
      await page.setViewportSize({ width, height: 1000 });
      await page.goto(url('overview'));
      await page.evaluate(value => localStorage.setItem('dr:density', value), density);
      await page.reload();
      await page.getByRole('button', { name: /^上涨 / }).click();
      const overview = page.getByRole('region', { name: '证券筛选结果' });
      const members = overview.locator('.filter-members > li');
      await recordCell(receipts, members, 'overview members', width, density, () => pageChange(overview, members));
      await page.goto(url('leaders', '&sector=SEC001'));
      const candidateRegion = page.getByRole('region', { name: '趋势龙头候选', exact: true });
      const candidateRows = candidateRegion.locator('tbody tr').first().locator('xpath=..').locator('tr');
      await recordCell(receipts, candidateRows, 'role candidates', width, density,
        () => pageChange(candidateRegion, candidateRows));
      const changesRegion = page.getByRole('region', { name: '趋势龙头角色变化', exact: true });
      const changes = changesRegion.locator('.role-changes-table tbody tr');
      await recordCell(receipts, changes, 'role changes', width, density, () => pageChange(changesRegion, changes));
      await page.goto(url('overview'));
      const search = await openSearch(page, 'topic');
      await search.getByRole('region', { name: '题材搜索结果' }).getByRole('button').click();
      const topic = page.getByRole('dialog', { name: '题材详情 · 只读' });
      const topicRows = topic.getByRole('table', { name: '题材成员', exact: true }).locator('tbody tr');
      await recordCell(receipts, topicRows, 'topic members', width, density, () => pageChange(topic, topicRows));
      await page.keyboard.press('Escape');
      await page.goto(url('news'));
      const news = page.getByRole('region', { name: '消息列表', exact: true });
      const newsRows = news.locator('.news-list-row');
      await recordCell(receipts, newsRows, 'news', width, density, () => pageChange(page.locator('main'), newsRows));
      await page.goto(url('analysis'));
      const rankRows = page.getByRole('table', { name: '涨幅排行' }).locator('tbody tr');
      await recordCell(receipts, rankRows, 'rank', width, density, () => pageChange(page.locator('main'), rankRows));
      await page.goto(url('overview'));
      const global = await openSearch(page, 'B8');
      const globalRows = global.locator('.global-search-results > li');
      await recordCell(receipts, globalRows, 'global search', width, density, () => pageChange(global, globalRows));
      await page.keyboard.press('Escape');
      await page.goto(url('stock', '&security=TEST001'));
      await page.getByText(/查看完整行情数据/).click();
      const historyRows = page.getByRole('table', { name: '完整行情数据', exact: true }).locator('tbody tr');
      await recordCell(receipts, historyRows, 'expanded stock history', width, density, async () => {
        await page.getByLabel('行情每页条数').selectOption('20');
        await page.getByRole('button', { name: '行情下一页', exact: true }).click();
        await expect(historyRows.first()).toContainText(DATE);
      });
    }
    expect(receipts).toHaveLength(32);
  } finally {
    await info.attach('b8-list-geometry', { body: JSON.stringify(receipts, null, 2), contentType: 'application/json' });
  }
});

test('B8-UNIT-01 nondefault units null zero percent and actual decoded stock CSV', async ({ page }, info) => {
  await fixture(page);
  await nondefaultUnits(page);
  await page.goto(url('stock', '&security=TEST001'));
  await page.getByText(/查看完整行情数据/).click();
  const table = page.getByRole('table', { name: '完整行情数据', exact: true });
  await expect(table.locator('tbody tr').first()).toContainText('—');
  await expect(table.locator('tbody tr').nth(1)).toContainText('0');
  const pending = page.waitForEvent('download');
  await page.getByRole('button', { name: '导出当前筛选 CSV', exact: true }).click();
  const download = await pending;
  const stream = await download.createReadStream();
  const chunks: Buffer[] = [];
  for await (const chunk of stream) chunks.push(Buffer.from(chunk));
  const bytes = Buffer.concat(chunks);
  const rows = parseCsv(bytes.toString('utf8'));
  expect(rows).toHaveLength(22);
  expect(rows.every(row => row.currency === 'USD' && row.unit === 'USD/share')).toBe(true);
  expect(rows[1]).toMatchObject({ volume: '', amount: '', close: '11', row_trade_date: sessions[0] });
  expect(rows[2]).toMatchObject({ volume: '0', amount: '0', close: '12' });
  expect(rows.slice(1).every(row => row.row_currency === 'USD' && row.price_unit === 'USD/share'
    && row.volume_unit === 'lot' && row.amount_unit === 'USD')).toBe(true);
  await info.attach('b8-stock-csv', { body: bytes, contentType: 'text/csv' });
  const context = page.locator('main');
  expect.soft(await context.textContent(), 'nondefault price currency local context').toContain('USD/share');
  expect.soft(await context.textContent(), 'nondefault volume local context').toContain('lot');
  await page.goto(url('analysis'));
  await expect(page.getByRole('table', { name: '涨幅排行' }).locator('tbody tr').first()).toContainText('10');
  expect.soft(await page.getByRole('table', { name: '涨幅排行' }).textContent()).toContain('%');
  await info.attach('b8-unit-na', { body: JSON.stringify({
    countBoardDateOnly: ['role candidates', 'role changes', 'topic members', 'news', 'global search'],
    syntheticHTTP: true, noDefaultSubstitution: rows.every(row => row.currency === 'USD'),
  }), contentType: 'application/json' });
});

test('B8-Q04-01 protected denial late response LOCAL records and explicit H unchanged', async ({ page }, info) => {
  await fixture(page);
  const probe = JSON.parse(readFileSync(resolve(process.env.B8_OUTPUT_DIR ||
    '../.temp/b8-residual/standalone', 'b8-q04-probe.json'), 'utf8'));
  expect(probe.real_evaluators).toBe(true);
  expect(probe.affected_core.candidates).toEqual([]);
  expect(probe.independent_trend).toEqual(probe.baseline_trend);
  await historicalSelection(page);
  const pending = { ...probe.pending_rule, threshold: Number(probe.pending_rule.threshold),
    warning_threshold: Number(probe.pending_rule.warning_threshold),
    computed_value: Number(probe.pending_rule.computed_value),
    official_source_url: null, effective_from: DATE, effective_to: null,
    production_enabled: false, production_gate_reasons: probe.pending_rule.reasons };
  await page.route('**/api/regulation/status?**', route => route.fulfill({ json: {
    ...resetSummary(), coverage: { expected: 1, valid: 0, excluded: 0, missing: 1 },
    data: {
      region1_completeness: { status: 'missing', expected: 1, valid: 0, excluded: 0, missing: 1,
        missing_count: 1, source_check: 'pass', coverage_ratio: 0 },
      region2_calc_state: [pending], region3_events: [],
    },
  } }));
  await page.addInitScript(() => {
    const fetch = window.fetch.bind(window);
    const receipts: unknown[] = [];
    (window as unknown as { b8LateTransport: unknown[] }).b8LateTransport = receipts;
    window.fetch = async (input, init) => {
      const path = String(input);
      if (!path.includes('/api/role/candidates') || !path.includes('role=trend_leader')) return fetch(input, init);
      const response = await fetch(input, { ...init, signal: undefined });
      receipts.push({ path, status: response.status, originalSignalAborted: init?.signal?.aborted ?? false,
        arrivedAt: Date.now() });
      return response;
    };
  });
  await seedLocal(page);
  const before = await localRecords(page);
  expect(before.notes).toHaveLength(1);
  expect(before.note_revisions).toHaveLength(2);
  expect(before.verification_records).toHaveLength(1);
  expect(before.watchlist).toHaveLength(1);
  await page.goto(url('regulation'));
  const pendingRow = page.getByRole('table', { name: '监管数值计算' }).locator('tbody tr');
  await expect(pendingRow).toHaveCount(1);
  await expect(pendingRow).toContainText('待核验');
  await expect(pendingRow).toContainText('原文缺失');
  expect(probe.pending_rule.state).toBe('pending');
  await info.attach('b8-real-kernel-probe', { body: JSON.stringify(probe), contentType: 'application/json' });
  let mode: 'ready' | 'hold' | 'deny' = 'ready';
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  let started!: () => void;
  const observed = new Promise<void>(resolve => { started = resolve; });
  await page.route('**/api/role/candidates?**', async route => {
    const query = new URL(route.request().url()).searchParams;
    const response = roleBody(query, query.get('role') === 'zhongjun');
    const evaluated = query.get('role') === 'zhongjun' ? probe.affected_core : probe.independent_trend;
    response.coverage = { expected: evaluated.topic_status.expected, valid: evaluated.topic_status.valid,
      excluded: evaluated.topic_status.legal_excluded, missing: evaluated.topic_status.missing };
    response.data.role_changes = [];
    if (query.get('role') === 'zhongjun') response.data.statuses[0].reasons = evaluated.topic_status.reasons;
    else {
      response.data.candidates = probe.independent_trend.candidates.filter((row: { status: string }) =>
        row.status === 'candidate').map((row: { security_id: string; tie_rank: number }) => ({
        ...response.data.candidates[0], security_id: row.security_id, rank: row.tie_rank,
      }));
      response.data.total = response.data.candidates.length;
    }
    if (mode === 'hold' && query.get('role') === 'trend_leader') {
      started(); await held;
      await route.fulfill({ json: response });
      return;
    }
    if (mode === 'deny') return route.fulfill({ status: 403, json: { code: 'FORBIDDEN' } });
    return route.fulfill({ json: response });
  });
  await page.goto(url('leaders', '&sector=SEC001&roles=trend_leader,zhongjun'));
  const core = page.getByRole('region', { name: '中军候选', exact: true });
  await expect(core).toContainText('角色输入不足');
  await expect(core).toContainText(probe.affected_core.topic_status.reasons[0]);
  await expect(core.getByRole('table', { name: '角色候选' })).toHaveCount(0);
  await expect(page.getByRole('region', { name: '趋势龙头候选', exact: true })
    .getByRole('table', { name: '角色候选' })).toBeVisible();
  await expect(page.getByLabel('快照版本')).toHaveValue(H);
  await expect(page.getByLabel('快照版本').locator('option')).toHaveCount(2);
  mode = 'hold';
  await page.getByRole('button', { name: '刷新快照', exact: true }).click();
  await observed;
  mode = 'deny';
  await page.route('**/api/limit-up/ladder?**', route =>
    route.fulfill({ status: 403, json: { code: 'FORBIDDEN' } }));
  await page.getByRole('button', { name: '刷新快照', exact: true }).click();
  await expect(page.getByText('来源访问已被拒绝', { exact: true }).first()).toBeVisible();
  release();
  await expect.poll(() => page.evaluate(() =>
    (window as unknown as { b8LateTransport: { originalSignalAborted: boolean; status: number }[] })
      .b8LateTransport.filter(row => row.originalSignalAborted && row.status === 200).length)).toBeGreaterThan(0);
  await expect(page.getByRole('table', { name: '角色候选' })).toHaveCount(0);
  expect(await localRecords(page)).toEqual(before);
  expect(new URL(page.url()).searchParams.get('snapshot')).toBe(H);
  const lateTransport = await page.evaluate(() =>
    (window as unknown as { b8LateTransport: unknown[] }).b8LateTransport);
  await page.reload();
  expect(await localRecords(page)).toEqual(before);
  expect(new URL(page.url()).searchParams.get('snapshot')).toBe(H);
  await info.attach('b8-q04-local', { body: JSON.stringify({ before, after: await localRecords(page),
    historicalSelector: H, syntheticHTTP: true,
    lateTransport, realKernelSyntheticInputs: true,
    limitation: 'Synthetic HTTP projection and transport cancellation fault injection; no real source authorization or rule approval' }),
    contentType: 'application/json' });
});

test('B8-D04-01 actual supported isolated operator replay with same browser LOCAL and H', async ({ page }, info) => {
  test.setTimeout(180000);
  const output = process.env.B8_OUTPUT_DIR;
  expect(output, 'D04 must use source-bound B8 runner').toBeTruthy();
  expect(process.env.B8_DB_RELEASE, 'D04 blocked: parent must explicitly hand back exclusive dragon_test ownership')
    .toBe('exclusive');
  const operator = (phase: string) => spawnSync(resolve('../.venv/Scripts/python.exe'),
    ['.temp/b8-residual/b8-d04-operator.py', phase, output!], { cwd: resolve('..'),
      encoding: 'utf8', timeout: 120000, env: { ...process.env, PYTHONPATH: 'src;tests' } });
  try {
    const prepared = operator('prepare');
    await info.attach('b8-d04-prepare-log', { body: prepared.stderr, contentType: 'text/plain' });
    expect(prepared.status, prepared.stderr).toBe(0);
    const state = JSON.parse(prepared.stdout);
    await fixture(page);
    await d04Fixture(page, state);
    await seedLocal(page, state.h.snapshot_id, state.h.trade_date);
    const before = await localRecords(page);
    expect(before.notes).toHaveLength(1);
    expect(before.note_revisions).toHaveLength(2);
    expect(before.verification_records).toHaveLength(1);
    expect(before.watchlist).toHaveLength(1);
    await page.goto(url('stock', '&security=TEST001').replace(H, state.h.snapshot_id).replace(DATE, state.h.trade_date));
    await expect(page.getByLabel('快照版本')).toHaveValue(state.h.snapshot_id);
    await expect(page.getByLabel('快照版本').locator('option')).toHaveCount(2);
    await page.getByText(/查看完整行情数据/).click();
    await page.getByLabel('行情每页条数').selectOption('20');
    await page.getByRole('button', { name: '行情下一页', exact: true }).click();
    const historyTable = page.getByRole('table', { name: '完整行情数据', exact: true });
    await expect(historyTable.locator('tbody tr')).toHaveCount(1);
    const history = await historyTable.textContent();
    const lastHistoryRow = await historyTable.locator('tbody tr').last().textContent();
    const historyUrl = page.url();
    const operated = operator('operate');
    await info.attach('b8-d04-operation-log', { body: operated.stderr, contentType: 'text/plain' });
    expect(operated.status, operated.stderr).toBe(0);
    const receipt = JSON.parse(operated.stdout);
    expect(receipt.actual_cli).toBe(true);
    expect(receipt.publishF.exit).toBe(0);
    expect(receipt.replayV.exit).toBe(0);
    expect(receipt.recoverySelection).toBe(state.v.snapshot_id);
    expect(receipt.rereadSnapshot).toBe(state.v.snapshot_id);
    expect(receipt.latestSnapshot).toBe(receipt.f.snapshot_id);
    expect(receipt.latestSnapshot).not.toBe(state.v.snapshot_id);
    expect(receipt.historicalSnapshot).toBe(state.h.snapshot_id);
    const recovery = await page.context().newPage();
    await fixture(recovery);
    await d04Fixture(recovery, { ...state, f: receipt.f });
    await recovery.goto(url('stock', '&security=TEST001').replace(H, state.v.snapshot_id).replace(DATE, state.v.trade_date));
    await expect(recovery.getByLabel('快照版本')).toHaveValue(state.v.snapshot_id);
    await expect(recovery.getByLabel('快照版本').locator('option')).toHaveCount(3);
    await expect(recovery.locator('main')).toContainText(`发布 ${state.v.snapshot_id}`);
    expect(await localRecords(recovery)).toEqual(before);
    await info.attach('b8-d04-explicit-v-selection', { body: JSON.stringify({
      recoveryUrl: recovery.url(), selectedV: state.v.snapshot_id, latestF: receipt.f.snapshot_id,
      noGlobalRollback: true, api: receipt.api,
    }), contentType: 'application/json' });
    await recovery.close();
    expect(await localRecords(page)).toEqual(before);
    expect(page.url()).toBe(historyUrl);
    expect(await historyTable.textContent()).toBe(history);
    await page.reload();
    expect(await localRecords(page)).toEqual(before);
    expect(new URL(page.url()).searchParams.get('snapshot')).toBe(state.h.snapshot_id);
    await page.getByText(/查看完整行情数据/).click();
    await expect(historyTable.locator('tbody tr')).toHaveCount(21);
    const afterReloadDates = await historyTable.locator('tbody tr th').allTextContents();
    expect(afterReloadDates).toEqual(state.points.map((row: { trade_date: string }) => row.trade_date));
    expect(await historyTable.locator('tbody tr').last().textContent())
      .toBe(lastHistoryRow);
    await page.getByLabel('行情每页条数').selectOption('20');
    await page.getByRole('button', { name: '行情下一页', exact: true }).click();
    expect(await historyTable.textContent()).toBe(history);
    await info.attach('b8-d04-local-history', { body: JSON.stringify({
      before, after: await localRecords(page), history, historyUrl, receipt, afterReloadDates,
      reloadPagination: 'Existing table-local pagination resets; same H archive retained, page 2 reselected only for row parity.',
      http: 'synthetic browser fixture projection bound to actual isolated H and archived market points',
    }), contentType: 'application/json' });
  } finally {
    const cleanup = operator('cleanup');
    await info.attach('b8-d04-cleanup-log', { body: cleanup.stderr || cleanup.stdout, contentType: 'text/plain' });
    expect(cleanup.status, cleanup.stderr).toBe(0);
  }
});

test('B8-LHB-01 two suppliers reused IDs revision-map timing and contributions', async ({ page }, info) => {
  await fixture(page);
  await mixedLhb(page);
  await page.goto(url('stock', '&security=TEST001'));
  const panel = page.getByRole('region', { name: '龙虎榜席位证据' });
  const disclosures = panel.locator('.leaders-section');
  await expect(disclosures).toHaveCount(20);
  await expect(disclosures.nth(0)).toContainText('修订 2 · feed-a / a-v1');
  await expect(disclosures.nth(0)).toContainText('REUSED · map2 · 行 feed-a-row-0');
  await expect(disclosures.nth(1)).toContainText('修订 1 · feed-b / b-v9');
  await expect(disclosures.nth(1)).toContainText('REUSED · map1 · 行 feed-b-row-1');
  await expect(disclosures.nth(0).locator('summary')).toHaveText('披露来源时钟 · feed-a / a-v1 · shared-disc · 修订 2');
  await expect(disclosures.nth(1).locator('summary')).toHaveText('披露来源时钟 · feed-b / b-v9 · shared-disc · 修订 1');
  await expect(disclosures.nth(1).getByRole('cell', { name: '未知', exact: true })).toHaveCount(4);
  await expect(disclosures.nth(1).getByRole('cell', { name: '0', exact: true })).toHaveCount(1);
  const trigger = disclosures.nth(0).getByRole('button', { name: '查看席位 REUSED 近 30 日历史' });
  await trigger.focus();
  await page.keyboard.press('Enter');
  const dialog = page.getByRole('dialog', { name: '席位 REUSED 近 30 日上榜历史' });
  await expect(dialog.locator('.lhb-history-list > li')).toHaveCount(2);
  const a = dialog.locator('.lhb-history-list > li').nth(0);
  const b = dialog.locator('.lhb-history-list > li').nth(1);
  await expect(a).toContainText('REUSED · feed-a / a-v1');
  await expect(a).toContainText('净额 140 USD / dollar');
  await expect(a.locator('tbody tr')).toHaveCount(2);
  await expect(a.locator('tbody tr').nth(0)).toContainText('feed-a-original');
  await expect(a.locator('tbody tr').nth(0)).toContainText('map1');
  await expect(a.locator('tbody tr').nth(0).locator('td[data-label="披露 ID"]')).toHaveText('prior-disc');
  await expect(a.locator('tbody tr').nth(1)).toContainText('feed-a-corrected');
  await expect(a.locator('tbody tr').nth(1)).toContainText('map2');
  await expect(a.locator('tbody tr').nth(1).locator('td[data-label="披露 ID"]')).toHaveText('shared-disc');
  await expect(a.locator('tbody tr').nth(1).locator('td[data-label="修订"]')).toHaveText('2');
  await expect(b).toContainText('REUSED · feed-b / b-v9');
  await expect(b).toContainText('买入 未知 · 卖出 0');
  await expect(b.locator('tbody tr')).toHaveCount(1);
  await expect(b.locator('tbody tr')).toContainText('feed-b-original');
  await expect(b.locator('tbody tr').locator('td[data-label="披露 ID"]')).toHaveText('prior-disc');
  await info.attach('b8-lhb-late-history', { body: await dialog.textContent() || '', contentType: 'text/plain' });
  await page.keyboard.press('Escape');
  await expect(trigger).toBeFocused();
  await panel.getByRole('button', { name: '下一页', exact: true }).click();
  await expect(disclosures).toHaveCount(1);
  await expect(disclosures).toHaveAttribute('aria-label', '披露 b8-disc-20');
  await page.goto(url('stock', '&security=TEST001').replace(H, OLD));
  await expect(disclosures.nth(0)).toContainText('修订 1 · feed-a / a-v1');
  await expect(disclosures.nth(0)).toContainText('REUSED · map1 · 行 feed-a-row-0');
  await expect(panel).not.toContainText('map2');
  await disclosures.nth(0).getByRole('button', { name: '查看席位 REUSED 近 30 日历史' }).click();
  await expect(dialog).not.toContainText('A corrected');
  await expect(dialog.locator('.lhb-history-list > li').nth(0)).toContainText('净额 60 USD / dollar');
  await expect(dialog.locator('.lhb-history-list > li').nth(0).locator('tbody tr')).toHaveCount(1);
  await page.keyboard.press('Escape');
  await disclosures.nth(2).getByRole('button').click();
  await expect(page.getByRole('dialog')).toContainText('没有可显示的上榜历史');
  await page.keyboard.press('Escape');
  await disclosures.nth(3).getByRole('button').click();
  await expect(page.getByRole('dialog')).toContainText('未映射席位');
  await info.attach('b8-lhb-evidence-scope', { body: JSON.stringify({
    syntheticHTTP: true, sources: ['feed-a/a-v1', 'feed-b/b-v9'],
    reused: ['REUSED', 'shared-disc'], capturedPermission: 'authorized fixture, not real authorization',
    contributionAmounts: 'not fields in frozen contribution wire schema; exact source aggregate amounts asserted',
  }), contentType: 'application/json' });
});

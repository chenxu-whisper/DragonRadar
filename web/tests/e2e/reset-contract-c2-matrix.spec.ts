import { expect, test, type Page } from '@playwright/test';
import { mkdirSync, writeFileSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expectedCells, cellKey, parseCsv } from '../../scripts/contract-c2-support.mjs';
import { ownedBrowser, hitTest, keyboardFocus, readingMeasurements } from '../fixtures/contract-c2-browser';
import { ID, DATE, mockC2, articles, candidates } from '../fixtures/contract-c2-scenes';
import { securities, sessions } from '../fixtures/market';

const headings: Record<string, string> = {
  overview: '市场总览', news: '市场消息', leaders: '龙头梯队', analysis: '涨幅分析',
  stock: '证券详情', regulation: '监管观察', review: '我的复盘',
};
const evidence = process.env.C2_EVIDENCE_DIR || '/evidence';
const origins: Record<string, string> = {
  public: 'http://127.0.0.1:4171', local: 'http://127.0.0.1:4172', owner: 'http://127.0.0.1:4173',
};
async function download(page: Page, label: string) {
  const button = page.getByRole('button', { name: label, exact: true });
  const hit = await hitTest(button);
  await keyboardFocus(page, button);
  const pending = page.waitForEvent('download');
  await button.press('Enter');
  const value = await pending;
  const stream = await value.createReadStream();
  const chunks: Buffer[] = [];
  for await (const chunk of stream) chunks.push(Buffer.from(chunk));
  return { filename: value.suggestedFilename(), text: Buffer.concat(chunks).toString('utf8'), hitTest: hit,
    expectedRows: undefined as Record<string, string>[] | undefined };
}
async function openClose(page: Page, opener: ReturnType<Page['locator']>, target = '合成测试证券1',
  audit: Awaited<ReturnType<typeof mockC2>>, securityId = 'TEST001', sector?: string) {
  const close = async (operation: string, action: () => Promise<unknown>) => {
    const receipt = audit.beginDrawerClose(securityId, operation, sector);
    await action();
    await expect(dialog).toHaveCount(0);
    await expect(opener).toBeFocused();
    receipt.asserted(`${securityId}: ${operation}; exact drawer absent and actual opener focused`);
  };
  await hitTest(opener);
  await keyboardFocus(page, opener);
  audit.beginDrawerOpen();
  await opener.press('Enter');
  const dialog = page.getByRole('dialog', { name: '证券详情 · 只读' });
  await expect(dialog).toContainText(target);
  await expect(page.locator('dialog[open]')).toHaveCount(1);
  await page.waitForLoadState('networkidle');
  await audit.settled();
  await close('first Escape', () => page.keyboard.press('Escape'));
  audit.beginDrawerOpen();
  await opener.press('Enter');
  await expect(dialog).toContainText(target);
  await expect(page.locator('dialog[open]')).toHaveCount(1);
  await page.waitForLoadState('networkidle');
  await audit.settled();
  // Inert modal background: dispatch tests same-trigger handler semantics only.
  await close('same-trigger toggle', () => opener.dispatchEvent('click'));
  audit.beginDrawerOpen();
  await opener.press('Enter');
  await expect(dialog).toContainText(target);
  await page.waitForLoadState('networkidle');
  await audit.settled();
  await close('final Escape', () => page.keyboard.press('Escape'));
}
function exactCsv(result: { text: string; expectedRows?: Record<string, string>[] }, envelope: any, filters: Record<string, unknown>, expected: Record<string, unknown>[]) {
  const rows = parseCsv(result.text);
  expect(rows).toHaveLength(expected.length);
  const metadata = ['snapshot_id', 'trade_date', 'as_of', 'rule_version', 'filters', 'unit', 'currency', 'price_type', 'benchmark'];
  for (const row of rows) {
    expect(JSON.parse(row.filters)).toEqual(filters);
    for (const key of metadata.filter(k => k !== 'filters')) {
      expect(row[key], `exact CSV metadata ${key}`).toBe(String((key in envelope ? envelope[key] : envelope.basis[key]) ?? ''));
    }
  }
  const fields = Object.keys(rows[0]).filter(k => !metadata.includes(k));
  expect(rows.map(row => Object.fromEntries(fields.map(k => [k, row[k]]))))
    .toEqual(expected.map(row => Object.fromEntries(fields.map(k => [k, String(row[k] ?? '')]))));
  result.expectedRows = rows;
  return rows.length;
}
async function mainTask(page: Page, surface: string, audit: Awaited<ReturnType<typeof mockC2>>) {
  let result;
  let rowCount = 0, targets: string[] = [], record: any;
  await audit.settled();
  if (surface === 'overview') {
    await page.getByPlaceholder('输入证券代码或名称').fill('合成');
    await page.getByRole('button', { name: '搜索', exact: true }).click();
    const up = page.getByRole('button', { name: /^上涨 / });
    await hitTest(up);
    await keyboardFocus(page, up);
    await up.press('Enter');
    await expect(up).toHaveAttribute('aria-pressed', 'true');
    await page.getByRole('combobox', { name: '身份排序', exact: true }).selectOption('code');
    await page.getByRole('combobox', { name: '排序方向', exact: true }).selectOption('desc');
    await page.getByRole('combobox', { name: '每页条数', exact: true }).selectOption('20');
    await page.getByRole('button', { name: '下一页', exact: true }).click();
    await expect(page.getByRole('region', { name: '证券筛选结果' })).toContainText('TEST023');
    result = await download(page, '导出当前页 CSV');
    expect(result.text).toContain('TEST023');
    expect(result.text).not.toContain('TEST063');
    expect(result.text).toContain('""market_filter"":""up""');
    expect(result.text).not.toContain('""page""');
    expect(result.text).not.toContain('""sort""');
    const expected = securities.slice(0, 63).filter(s => Number(s.change) > 0).reverse().slice(20).map(s => ({
      security_id: s.security_id, code: s.security_id, name: s.name, direction: 'up',
    }));
    targets = expected.map(s => s.security_id);
    expect(await page.getByRole('region', { name: '证券筛选结果' }).locator('.security-button').allTextContents())
      .toEqual(expected.map(s => s.name));
    rowCount = exactCsv(result, audit.payloads['/api/market/overview'], { market_filter: 'up', q: '合成' }, expected);
  } else if (surface === 'news') {
    await page.getByRole('combobox', { name: '消息类型' }).selectOption('announcement');
    const article = page.getByRole('region', { name: '消息列表' }).getByRole('button', { name: /机器人公司签署/ });
    await hitTest(article);
    await keyboardFocus(page, article);
    await article.press('Enter');
    await expect(page.getByRole('region', { name: '消息详情' })).toContainText('公告主体代码 TEST001');
    await openClose(page, page.getByRole('region', { name: '关联证券与题材' }).getByRole('button', { name: '合成测试证券1', exact: true }), undefined, audit);
    result = await download(page, '导出当前页 CSV');
    expect(result.text).toContain('机器人公司签署合成测试订单');
    expect(result.text).not.toContain('行业跟踪');
    expect(result.text).toContain('""news_type"":""announcement""');
    await page.getByRole('combobox', { name: '每页条数', exact: true }).selectOption('20');
    await page.getByRole('button', { name: '下一页', exact: true }).click();
    const expected = articles.filter(a => a.article_type === 'announcement').slice(20, 40);
    await expect(page.getByRole('region', { name: '消息列表' }).locator('.news-list-row strong')).toHaveText(expected.map(a => a.title));
    result = await download(page, '导出当前页 CSV');
    await audit.settled();
    rowCount = exactCsv(result, audit.payloads['/api/news'], { news_type: 'announcement', news_source: '', q: '', page: 2, page_size: 20 }, expected);
    targets = expected.map(a => a.article_id);
  } else if (surface === 'leaders') {
    await page.getByRole('combobox', { name: '题材筛选' }).selectOption('SEC001');
    await expect(page.getByRole('table', { name: '角色候选' })).toContainText('合成测试证券1');
    await openClose(page, page.getByRole('table', { name: '角色候选' }).getByRole('button', { name: '合成测试证券1', exact: true }), undefined, audit, 'TEST001', 'SEC001');
    result = await download(page, '导出当前筛选 CSV');
    expect(result.text).toContain('TEST001');
    expect(result.text).toContain('""sector"":""SEC001""');
    expect(result.text).toContain('""page"":""1""');
    expect(result.text).not.toContain('""sort""');
    await page.getByRole('region', { name: '趋势龙头候选' }).getByRole('combobox', { name: '每页条数', exact: true }).selectOption('20');
    await page.getByRole('region', { name: '趋势龙头候选' }).getByRole('button', { name: '下一页', exact: true }).click();
    await expect(page.getByRole('table', { name: '角色候选' }).locator('tbody .security-button')).toHaveText(candidates.slice(20).map(c => `${c.name}${c.security_id}`));
    result = await download(page, '导出当前筛选 CSV');
    rowCount = exactCsv(result, audit.payloads['/api/role/candidates'],
      { date: DATE, sector: 'SEC001', role: 'trend_leader', page: '2', page_size: '20' }, candidates.slice(20));
    targets = candidates.slice(20).map(c => c.security_id);
    await expect(page.locator('.changes-section').getByRole('button', { name: /CSV/ })).toHaveCount(0);
  } else if (surface === 'analysis') {
    await page.getByRole('combobox', { name: '每页条数', exact: true }).selectOption('20');
    await page.getByRole('button', { name: '下一页', exact: true }).click();
    const table = page.getByRole('table', { name: '涨幅排行' });
    await expect(table).toContainText('TEST021');
    await openClose(page, table.getByRole('button', { name: 'TEST021', exact: true }), '合成测试证券21', audit, 'TEST021');
    result = await download(page, '导出当前页 CSV');
    expect(result.text).toContain('TEST021');
    expect(result.text).not.toContain('TEST001');
    expect(result.text).toContain('""page"":2');
    expect(result.text).toContain(',ratio,CNY,raw,B000300,');
    const envelope = audit.payloads['/api/rank/range'];
    const filters = { start: sessions[0], end: DATE, q: '', sort: 'return', order: 'desc', page: 2, page_size: 20, adjustment: 'raw' };
    await expect(table.locator('tbody .security-button')).toHaveText(envelope.data.ranking.slice(20).map((r: any) => r.security_id));
    rowCount = exactCsv(result, envelope, filters, envelope.data.ranking.slice(20));
    targets = envelope.data.ranking.slice(20).map((r: any) => r.security_id);
  } else if (surface === 'stock') {
    await expect(page.getByText('TEST001', { exact: true }).first()).toBeVisible();
    await page.getByText(/查看完整行情数据/).click();
    await page.getByRole('combobox', { name: '行情每页条数', exact: true }).selectOption('20');
    await page.getByRole('button', { name: '行情下一页', exact: true }).click();
    await expect(page.getByRole('table', { name: '完整行情数据', exact: true })).toContainText(sessionsEnd());
    result = await download(page, '导出当前筛选 CSV');
    expect(result.text).toContain('""security_id"":""TEST001""');
    expect(result.text).toContain('2026-08-13');
    expect(result.text).toContain(DATE);
    expect(result.text).toContain(',currency/share,CNY,raw,');
    const before = result.text;
    const sort = page.getByRole('table', { name: '完整行情数据', exact: true }).getByRole('button', { name: '交易日期排序', exact: true });
    await hitTest(sort);
    await keyboardFocus(page, sort);
    await sort.press('Enter');
    await expect(page.getByRole('table', { name: '完整行情数据' }).locator('tbody tr').first()).toContainText(DATE);
    await page.getByRole('button', { name: '行情下一页', exact: true }).click();
    result = await download(page, '导出当前筛选 CSV');
    expect(result.text).toBe(before);
    const raw = audit.payloads['/api/stock/TEST001/kline'], quote = audit.payloads['/api/stock/TEST001'].data;
    rowCount = exactCsv(result, raw, { security_id: 'TEST001', start: sessions[0], end: DATE, adjustment: 'raw' },
      [{ record_type: 'quote', security_id: 'TEST001', row_trade_date: DATE, ...quote.quote,
        roles: quote.roles.map((r: any) => r.role_type).join('|'), formal_event_count: quote.regulation.formal_event_count },
      ...raw.data.points.map((p: any) => ({ ...p, record_type: 'kline', security_id: 'TEST001', row_trade_date: p.trade_date }))]);
    targets = ['TEST001'];
  } else if (surface === 'regulation') {
    await expect(page.getByRole('region', { name: '数据完整性' })).toContainText('75');
    result = await download(page, '导出当前筛选 CSV');
    expect(result.text).toContain('synthetic-v1');
    expect(result.text).toContain('calculation');
    expect(result.text).toContain('event');
    await audit.settled();
    const status = audit.payloads['/api/regulation/status'], events = audit.payloads['/api/regulation/events'];
    const expected = [...status.data.region2_calc_state.map((r: any) => ({
      record_type: 'calculation', rule_id: r.rule_id, record_rule_version: r.rule_version, security_id: r.security_id,
      state: r.state, value: r.computed_value, effective_from: r.effective_from, effective_to: r.effective_to,
    })), ...events.data.events.map((r: any) => ({
      record_type: 'event', rule_id: r.rule_id, record_rule_version: r.rule_version, security_id: r.security_id,
      state: r.display_state, value: r.event_type, effective_from: r.effective_from, effective_to: r.effective_to,
    }))];
    rowCount = exactCsv(result, status, { date: DATE }, expected);
    targets = expected.map(r => `${r.record_type}/${r.rule_id}/${r.security_id}`);
  } else {
    const title = page.getByRole('textbox', { name: '标题', exact: true });
    await hitTest(title);
    await keyboardFocus(page, title);
    await title.fill('C2 persisted LOCAL record');
    await page.getByRole('textbox', { name: '正文', exact: true }).fill('C2 readback evidence');
    await page.getByRole('button', { name: '保存笔记', exact: true }).click();
    await expect(page.locator('.local-note-editor').getByRole('status')).toContainText('回读验证通过');
    await expect(page.getByRole('button', { name: /CSV/ })).toHaveCount(0);
    result = await download(page, '导出本机备份');
    const backup = JSON.parse(result.text);
    expect(backup.notes).toHaveLength(1);
    expect(JSON.stringify(backup)).toContain('C2 persisted LOCAL record');
    expect(JSON.stringify(backup)).toContain('C2 readback evidence');
    await page.reload();
    await page.getByRole('button', { name: 'C2 persisted LOCAL record', exact: true }).click();
    await expect(title).toHaveValue('C2 persisted LOCAL record');
    await expect(page.getByRole('textbox', { name: '正文', exact: true })).toHaveValue('C2 readback evidence');
    record = { ...backup.notes[0], revision: backup.notes[0].current_revision };
    rowCount = 1;
  }
  if (surface !== 'review') {
    expect(result.text).toContain(ID);
    expect(result.text).toContain(DATE);
  }
  return { operation: `${surface}: actual fixture filter/page/open/Escape/export or LOCAL persist/readback`,
    identity: surface === 'review' ? { kind: 'LOCAL', recordId: record.note_id, revision: record.revision, snapshotId: record.snapshot_id, date: record.trade_date }
      : { kind: 'PUB', snapshotId: ID, date: DATE, targets },
    readback: surface === 'review' ? { recordId: record.note_id, revision: record.revision, title: 'C2 persisted LOCAL record', body: 'C2 readback evidence', reloaded: true } : undefined,
    download: { filename: result.filename, decoded: result.text, rowCount, verified: true, expectedRows: result.expectedRows,
      hitTest: result.hitTest },
    filename: result.filename, decoded: result.text };
}
function sessionsEnd() { return DATE; }

for (const native of expectedCells().filter(cell => cell.zoom === 1)) {
  test(`matrix ${cellKey(native).replace(/\/1$/, '')}`, async () => {
    const cells: Record<string, unknown>[] = [];
    for (const requested of process.env.C2_HOST_NATIVE === '1' ? [1] : [1, 2]) {
      const owned = await ownedBrowser(native.viewport, native.theme);
      const cell: Record<string, unknown> = { ...native, zoom: requested, outcome: 'failed', profile: owned.profile };
      let failure;
      try {
        const audit = await mockC2(owned.page);
        cell.traffic = audit.traffic;
        cell.trafficReceipts = audit.receipts;
        cell.closeTrace = audit.closeTrace;
        const path = native.surface === 'stock' ? '/stock/TEST001' : '/';
        await owned.page.goto(`${origins[native.variant]}${path}?view=${native.surface}&snapshot=${ID}&date=${DATE}`);
        await expect(owned.page.getByRole('heading', { name: headings[native.surface], exact: true })).toBeVisible();
        const nativeGeometry = await owned.page.evaluate(() => ({ outerWidth, outerHeight, innerWidth, innerHeight }));
        cell.nativeGeometry = nativeGeometry;
        cell.receipt = await owned.zoom(requested);
        cell.measured = { width: nativeGeometry.innerWidth, height: nativeGeometry.innerHeight };
        cell.changedGeometry = await owned.page.evaluate(() => ({
          outerWidth, outerHeight, innerWidth, innerHeight, dpr: devicePixelRatio,
        }));
        await expect(owned.page.locator('html')).toHaveAttribute('data-theme', native.theme);
        Object.assign(cell, await mainTask(owned.page, native.surface, audit));
        const builds = JSON.parse(readFileSync(resolve(evidence, 'builds.json'), 'utf8'));
        cell.build = { variant: native.variant, assetsSha256: builds.find((b: any) => b.variant === native.variant).assetsSha256 };
        cell.reading = await readingMeasurements(owned.page);
        expect(new URL(owned.page.url()).searchParams.get('snapshot')).toBe(ID);
        expect(new URL(owned.page.url()).searchParams.get('date')).toBe(DATE);
        audit.assertClean();
        cell.traffic = audit.traffic;
      } catch (error) { failure = error; cell.error = String(error); }
      finally {
        try { cell.cleanup = await owned.cleanup(); }
        catch (error) { failure = new AggregateError([failure, error].filter(Boolean), 'Cell cleanup failed'); }
        cell.outcome = failure ? 'failed' : 'passed';
        if (failure) cell.error = String(failure);
        cells.push(cell);
        mkdirSync(resolve(evidence, 'cells'), { recursive: true });
        writeFileSync(resolve(evidence, 'cells', `${cellKey(native).replaceAll('/', '-')}.json`), JSON.stringify(cells, null, 2));
      }
      if (failure) throw failure;
    }
  });
}

test('public review boundary is structurally unavailable, not skipped', async () => {
  const owned = await ownedBrowser({ width: 768, height: 1024 }, 'light');
  let failure;
  const proof: Record<string, unknown> = { outcome: 'failed', structural: true };
  try {
    const audit = await mockC2(owned.page);
    await owned.page.goto(`${origins.public}/?view=review&snapshot=${ID}&date=${DATE}`);
    await owned.zoom(1);
    await expect(owned.page.getByRole('heading', { name: '市场总览', exact: true })).toBeVisible();
    await expect(owned.page.locator('.local-review')).toHaveCount(0);
    await expect(owned.page.getByRole('button', { name: '保存笔记', exact: true })).toHaveCount(0);
    audit.assertClean();
  } catch (error) { failure = error; }
  finally {
    try { proof.cleanup = await owned.cleanup(); }
    catch (error) { failure = new AggregateError([failure, error].filter(Boolean), 'Boundary cleanup failed'); }
    proof.outcome = failure ? 'failed' : 'passed';
    if (failure) proof.error = String(failure);
    writeFileSync(resolve(evidence, 'public-review-boundary.json'), JSON.stringify(proof));
  }
  if (failure) throw failure;
});

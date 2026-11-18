import { expect, test, type Page } from '@playwright/test';
import { ID, OLD, mockMarket, oldSnapshot, resetSummary, snapshot } from '../fixtures/market';
import { parseReviewBackup } from '../../src/repositories/local/migrate';

const originalContext = {
  trade_date: '2026-09-09', security_name: 'Original security name',
  sector_id: 'SEC001', sector_name: 'Original sector name',
};
const NEW = '00000000-0000-4000-8000-000000000003';
type EvidenceMode = 'available' | 'missing' | 'wrong-publication' | 'wrong-security' | 'late';

async function waitForLocalProfile(page: Page) {
  // Observe the app-created v5 database; never create an empty database ahead of it.
  await expect.poll(() => page.evaluate(async () => {
    const existing = (await indexedDB.databases()).find(row => row.name === 'dragon-radar-local-v3');
    if (!existing || existing.version !== 5) return false;
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      const opening = indexedDB.open(existing.name!);
      opening.onupgradeneeded = () => opening.transaction!.abort();
      opening.onsuccess = () => resolve(opening.result);
      opening.onerror = () => reject(opening.error);
    });
    try {
      if (!['profiles', 'notes', 'note_revisions', 'watchlist', 'verification_records', 'commands']
        .every(store => db.objectStoreNames.contains(store))) return false;
      const profile = await new Promise<unknown>((resolve, reject) => {
        const query = db.transaction('profiles').objectStore('profiles').get('active');
        query.onsuccess = () => resolve(query.result);
        query.onerror = () => reject(query.error);
      });
      return typeof profile === 'string' && profile.length > 0;
    } finally { db.close(); }
  })).toBe(true);
}

async function localRows(page: Page, store: string) {
  await waitForLocalProfile(page);
  return page.evaluate(async storeName => {
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      const opening = indexedDB.open('dragon-radar-local-v3');
      opening.onsuccess = () => resolve(opening.result);
      opening.onerror = () => reject(opening.error);
    });
    try {
      return await new Promise<Record<string, unknown>[]>((resolve, reject) => {
        const query = db.transaction(storeName).objectStore(storeName).getAll();
        query.onsuccess = () => resolve(query.result);
        query.onerror = () => reject(query.error);
      });
    } finally { db.close(); }
  }, store);
}

async function seedWatch(page: Page, legacy = false, unresolved = false) {
  // Only seed persisted fixtures. Every tested interaction uses the real app.
  await expect(page.getByRole('region', { name: '本地复盘', exact: true })).toBeVisible();
  await waitForLocalProfile(page);
  await page.evaluate(async ({ context, old, legacy, unresolved }) => {
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      const opening = indexedDB.open('dragon-radar-local-v3');
      opening.onsuccess = () => resolve(opening.result);
      opening.onerror = () => reject(opening.error);
    });
    const profile = await new Promise<string>((resolve, reject) => {
      const query = db.transaction('profiles').objectStore('profiles').get('active');
      query.onsuccess = () => resolve(query.result);
      query.onerror = () => reject(query.error);
    });
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction('watchlist', 'readwrite');
      tx.oncomplete = () => resolve();
      tx.onabort = () => reject(tx.error);
      tx.objectStore('watchlist').put({
        local_profile_id: profile, security_id: 'TEST001',
        snapshot_id: unresolved ? '00000000-0000-4000-8000-000000000099' : old,
        watch_id: 'original-watch', active: true, revision: 1,
        added_at: '2026-09-09T10:00:00.000Z', updated_at: '2026-09-09T10:00:00.000Z',
        ...(legacy ? {} : { context }),
      });
    });
    db.close();
  }, { context: originalContext, old: OLD, legacy, unresolved });
  await page.reload();
  await expect(page.getByTestId('local-watchlist').getByRole('button', { name: /打开原始证据/ })).toBeVisible();
}

async function evidence(page: Page, mode: EvidenceMode = 'available') {
  await mockMarket(page);
  const requests: URL[] = [];
  let release!: () => void;
  let requested!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  const received = new Promise<void>(resolve => { requested = resolve; });
  const publication = (id: string) => {
    const day = id === OLD ? '2026-09-09' : id === NEW ? '2026-09-11' : '2026-09-10';
    const summary = resetSummary(id === OLD ? oldSnapshot : snapshot);
    return {
      ...summary,
      snapshot_id: id, trade_date: day, as_of: `${day}T10:00:00Z`,
      source: 'synthetic-market', source_version: 'market-v1', rule_version: 'rule-v1',
      data: {
        ...summary.data, published_at: `${day}T10:01:00Z`,
        calendar_sessions: ['2026-09-09', '2026-09-10', '2026-09-11'].filter(session => session <= day),
        earliest_date: '2026-09-09',
      },
    };
  };
  await page.route('**/api/snapshots/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/snapshots/list') {
      const day = url.searchParams.get('date');
      const id = day === '2026-09-09' ? OLD : day === '2026-09-11' ? NEW : ID;
      return route.fulfill({ json: { ...publication(id), data: { snapshots: [publication(id)] } } });
    }
    const id = url.pathname.split('/').at(-1)!;
    if (![OLD, ID, NEW].includes(id)) return route.fulfill({ status: 404, json: {} });
    return route.fulfill({ json: publication(id) });
  });
  await page.route('**/api/stock/**', async route => {
    const url = new URL(route.request().url());
    requests.push(url);
    const id = url.searchParams.get('snapshot_id') || ID;
    const summary = publication(mode === 'wrong-publication' ? OLD : id);
    const security = mode === 'wrong-security' ? 'OTHER' : 'TEST001';
    if (url.pathname.endsWith('/kline')) {
      requested();
      if (mode === 'late') await pending;
      return route.fulfill({ json: {
        ...summary,
        data: {
          security_id: security, adjustment: 'raw',
          adjusted_start: '2026-09-09', adjusted_end: '2026-09-10', chart_type: 'candlestick',
          points: mode === 'missing' ? [] : [
            { trade_date: '2026-09-09', open: 10, high: 12, low: 9, close: 11, volume: 100, amount: 1100 },
            { trade_date: '2026-09-10', open: 11, high: 13, low: 10, close: 12, volume: 200, amount: 2400 },
          ],
        },
      } });
    }
    return route.fulfill({ json: {
      ...summary,
      data: {
        security_id: security, name: id === OLD ? originalContext.security_name : 'Later renamed security',
        quote: { last: 99, change_ratio: 0.99, amount: 999 },
        roles: [{ role_type: 'trend_leader', evidence_status: 'candidate', sector_id: 'SEC001' }],
        seal: {
          is_limit_up: null, touched_limit: null, first_seal_time: null, last_seal_time: null,
          break_count: null, seal_amount: null, seal_amount_unit: null, continuous_boards: null,
          reason: 'intraday_seal_fields_unavailable',
        },
        regulation: { formal_event_count: 0, event_source_completeness: 'complete', reason: null },
      },
    } });
  });
  return { requests, release, received };
}

async function createConfirmedNote(page: Page) {
  await page.goto(`/?view=review&snapshot=${OLD}&date=2026-09-09&security=TEST001`);
  await expect(page.locator('.snapshot-header')).toContainText('2026-09-09');
  await page.getByLabel('标题', { exact: true }).fill('Original confirmed judgment');
  await page.getByRole('textbox', { name: '正文', exact: true }).fill('Original text');
  await page.getByRole('textbox', { name: '判断', exact: true }).fill('Original strength judgment');
  await page.getByRole('textbox', { name: '依据', exact: true }).fill('Original immutable evidence');
  await page.getByRole('textbox', { name: '失效条件', exact: true }).fill('Close below 10 invalidates');
  await page.getByLabel('验证日期', { exact: true }).fill('2026-09-10');
  await page.getByRole('button', { name: '确认笔记', exact: true }).click();
  await expect(page.locator('.local-note-editor').getByRole('status')).toContainText('回读验证通过');
  const confirmed = (await localRows(page, 'note_revisions')).find(row => row.state === 'confirmed');
  expect(confirmed).toBeDefined();
  return confirmed!;
}

async function openVerification(page: Page) {
  await page.getByRole('button', { name: '验证判断', exact: true }).click();
  const panel = page.getByRole('region', { name: '判断验证', exact: true });
  await expect(panel).toBeVisible();
  const publication = panel.getByRole('combobox', { name: '验证发布', exact: true });
  await expect(publication.locator(`option[value="${ID}"]`)).toHaveCount(1);
  await publication.selectOption(ID);
  return panel;
}

test('IG06 opening a saved watch on B uses A with original date, security and sector', async ({ page }) => {
  const { requests } = await evidence(page);
  await page.goto(`/?view=review&snapshot=${ID}&date=2026-09-10`);
  await seedWatch(page);
  const list = page.getByTestId('local-watchlist');
  await expect(list).toBeVisible();
  await expect(list).toContainText(originalContext.security_name);
  await expect(list).toContainText(originalContext.sector_name);
  await list.getByRole('button', { name: /打开原始证据/ }).click();
  await expect(page).toHaveURL(new RegExp(`snapshot=${OLD}`));
  await expect(page).toHaveURL(/date=2026-09-09/);
  await expect(page).toHaveURL(/security=TEST001/);
  await expect(page).toHaveURL(/sector=SEC001/);
  await expect.poll(() => requests.filter(url => url.pathname === '/api/stock/TEST001').length).toBeGreaterThan(0);
  expect(requests.filter(url => url.pathname === '/api/stock/TEST001').every(url =>
    url.searchParams.get('snapshot_id') === OLD && url.searchParams.get('date') === '2026-09-09',
  )).toBe(true);
  const persisted = (await localRows(page, 'watchlist'))[0];
  expect(persisted).toMatchObject({ snapshot_id: OLD, context: originalContext, watch_id: 'original-watch' });
  await page.getByRole('dialog').getByRole('button', { name: '加入本地自选', exact: true }).click();
  await page.goto(`/?view=review&snapshot=${ID}&date=2026-09-10`);
  await page.getByTestId('local-watchlist').getByRole('button', { name: /移除/ }).click();
  await expect(page.getByTestId('local-watchlist')).not.toContainText('TEST001');
  await page.getByRole('button', { name: '撤销移除 TEST001', exact: true }).click();
  const undone = (await localRows(page, 'watchlist'))[0];
  expect(undone).toMatchObject({
    snapshot_id: OLD, context: originalContext, watch_id: 'original-watch', added_at: persisted.added_at,
  });
});

test('IG06 legacy watch resolves its own publication; unresolved provenance refuses current fallback', async ({ page }) => {
  const { requests } = await evidence(page);
  await page.goto(`/?view=review&snapshot=${ID}&date=2026-09-10`);
  await seedWatch(page, true);
  await page.getByTestId('local-watchlist').getByRole('button', { name: /打开原始证据/ }).click();
  await expect(page).toHaveURL(new RegExp(`snapshot=${OLD}`));
  await page.goto(`/?view=review&snapshot=${ID}&date=2026-09-10`);
  await seedWatch(page, true, true);
  const before = requests.length;
  await page.getByTestId('local-watchlist').getByRole('button', { name: /打开原始证据/ }).click();
  await expect(page.getByRole('region', { name: '本地复盘', exact: true })
    .getByRole('alert').filter({ hasText: '无法打开原始发布' })).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`snapshot=${ID}`));
  expect(requests.slice(before).filter(url => url.pathname === '/api/stock/TEST001')).toEqual([]);
});

test('IG06 same-publication pending watch reopen checks the newly dirty note before showing a drawer', async ({ page }) => {
  await evidence(page);
  await createConfirmedNote(page);
  await page.goto(`/?view=review&snapshot=${OLD}&date=2026-09-09&security=TEST001&sector=SEC001`);
  await seedWatch(page);
  await page.getByRole('button', { name: 'Original confirmed judgment', exact: true }).click();
  const before = page.url();
  let release!: () => void;
  let received!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  const requested = new Promise<void>(resolve => { received = resolve; });
  let intercepted = false;
  await page.route(`**/api/snapshots/${OLD}`, async route => {
    if (intercepted) return route.fallback();
    intercepted = true;
    received();
    await pending;
    return route.fallback();
  });
  const prompts: string[] = [];
  let prompted!: () => void;
  const dismissed = new Promise<void>(resolve => { prompted = resolve; });
  page.on('dialog', async dialog => {
    prompts.push(dialog.message());
    await dialog.dismiss();
    prompted();
  });
  await page.getByTestId('local-watchlist').getByRole('button', { name: /打开原始证据/ }).click();
  await requested;
  await page.getByRole('textbox', { name: '判断', exact: true }).fill('Edited while original publication lookup is pending');
  release();
  await dismissed;
  await expect(page).toHaveURL(before);
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.getByRole('textbox', { name: '判断', exact: true })).toHaveValue('Edited while original publication lookup is pending');
  expect(prompts).toHaveLength(1);
  expect((await localRows(page, 'note_revisions')).filter(row => row.state === 'confirmed')).toHaveLength(1);
});

test('IG06 changed-URL watch reopen asks once for a dirty note and opens its pinned origin after acceptance', async ({ page }) => {
  await evidence(page);
  await createConfirmedNote(page);
  await page.goto(`/?view=review&snapshot=${ID}&date=2026-09-10`);
  await seedWatch(page);
  await page.getByRole('button', { name: 'Original confirmed judgment', exact: true }).click();
  await page.getByRole('textbox', { name: '判断', exact: true }).fill('Unsaved note before changed-URL reopen');
  const prompts: string[] = [];
  page.on('dialog', async dialog => { prompts.push(dialog.message()); await dialog.accept(); });
  await page.getByTestId('local-watchlist').getByRole('button', { name: /打开原始证据/ }).click();
  await expect(page).toHaveURL(new RegExp(`snapshot=${OLD}`));
  await expect(page).toHaveURL(/date=2026-09-09/);
  await expect(page.getByRole('dialog')).toBeVisible();
  expect(prompts).toHaveLength(1);
});

test('IG06 opening search invalidates a pending saved-watch lookup and leaves search as the only dialog', async ({ page }) => {
  await evidence(page);
  await page.goto(`/?view=review&snapshot=${ID}&date=2026-09-10`);
  await seedWatch(page);
  let release!: () => void;
  let received!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  const requested = new Promise<void>(resolve => { received = resolve; });
  let intercepted = false;
  await page.route(`**/api/snapshots/${OLD}`, async route => {
    if (intercepted) return route.fallback();
    intercepted = true; received(); await pending;
    return route.fallback();
  });
  await page.getByTestId('local-watchlist').getByRole('button', { name: /打开原始证据/ }).click();
  await requested;
  await page.getByRole('button', { name: '全局搜索', exact: true }).click();
  const search = page.getByRole('dialog', { name: '全局搜索 · 发布归档', exact: true });
  await expect(search).toBeVisible();
  const processed = page.waitForResponse(response => response.url().endsWith(`/api/snapshots/${OLD}`));
  release();
  await processed;
  await expect(page.getByRole('alert').filter({ hasText: /导航已取消/ })).toBeVisible();
  await expect(search).toBeVisible();
  await expect(page.getByRole('dialog')).toHaveCount(1);
  await expect(page).toHaveURL(new RegExp(`snapshot=${ID}`));
  await expect(page.getByRole('dialog')).not.toContainText('加入本地自选');
});

test('IG07 confirmed r1 remains beside selected B raw interval after editing r2; local save survives reload', async ({ page }) => {
  const { requests } = await evidence(page);
  const uploads: string[] = [];
  page.on('request', request => {
    if (request.method() !== 'GET' && /\/api\//.test(request.url())) uploads.push(request.url());
  });
  const r1 = await createConfirmedNote(page);
  await page.getByRole('textbox', { name: '判断', exact: true }).fill('Edited r2 judgment');
  await page.getByRole('button', { name: '保存笔记', exact: true }).click();
  await expect(page.locator('.local-note-editor').getByRole('status')).toContainText('回读验证通过');
  const before = await localRows(page, 'note_revisions');
  const panel = await openVerification(page);
  await expect(panel.getByRole('combobox', { name: '原始修订', exact: true })).toHaveValue(String(r1.revision));
  await expect(panel.getByRole('region', { name: '原始判断', exact: true })).toContainText('Original strength judgment');
  await expect(panel.getByRole('region', { name: '原始判断', exact: true })).toContainText('Original immutable evidence');
  await expect(panel.getByRole('region', { name: '原始判断', exact: true })).toContainText('Close below 10 invalidates');
  const facts = panel.getByRole('region', { name: '验证区间事实', exact: true });
  await expect(facts).toContainText(ID);
  await expect(facts).toContainText('2026-09-09');
  await expect(facts).toContainText('2026-09-10');
  await expect(facts.getByRole('table')).toContainText('11');
  await expect(facts.getByRole('table')).toContainText('12');
  await panel.getByRole('combobox', { name: '验证结论', exact: true }).selectOption('weakened');
  await panel.getByRole('textbox', { name: '验证依据', exact: true }).fill('Human disposition, not automatic win rate');
  await panel.getByRole('button', { name: '保存验证', exact: true }).click();
  await expect(panel.getByRole('status')).toContainText('回读验证通过');
  expect(await localRows(page, 'note_revisions')).toEqual(before);
  expect(await localRows(page, 'verification_records')).toMatchObject([{
    revision: r1.revision, snapshot_id: ID, outcome: 'weakened',
    fact_interval: { adjusted_start: '2026-09-09', adjusted_end: '2026-09-10', adjustment: 'raw' },
    context: {
      requested_start: '2026-09-09', requested_end: '2026-09-10',
      adjusted_start: '2026-09-09', adjusted_end: '2026-09-10',
      trade_date: '2026-09-10', as_of: '2026-09-10T10:00:00Z',
      source: 'synthetic-market', source_version: 'market-v1', rule_version: 'rule-v1',
      published_at: '2026-09-10T10:01:00Z', publication_revision: 1,
      calendar_sessions: ['2026-09-09', '2026-09-10'],
    },
  }]);
  expect(requests.some(url => url.pathname.endsWith('/kline')
    && url.searchParams.get('snapshot_id') === ID && url.searchParams.get('adj') === 'raw')).toBe(true);
  expect(uploads).toEqual([]);
  await expect(panel).not.toContainText(/自动胜率|主观胜率\s*\d/);
  await page.reload();
  expect(await localRows(page, 'verification_records')).toHaveLength(1);
  const saved = (await localRows(page, 'verification_records'))[0];
  await page.goto(`/?view=review&snapshot=${NEW}&date=2026-09-11`);
  await page.getByRole('button', { name: 'Original confirmed judgment', exact: true }).click();
  const requestOffset = requests.length;
  await page.getByRole('button', { name: '验证判断', exact: true }).click();
  const reopened = page.getByRole('region', { name: '判断验证', exact: true });
  await reopened.getByRole('combobox', { name: '验证记录', exact: true }).selectOption(String(saved.verification_id));
  await expect(reopened.getByRole('combobox', { name: '验证发布', exact: true })).toHaveValue(ID);
  await expect(reopened.getByRole('region', { name: '验证区间事实', exact: true })).toContainText(ID);
  expect(requests.slice(requestOffset).filter(url => url.pathname.endsWith('/kline')
    && url.searchParams.get('snapshot_id') === NEW)).toEqual([]);
  expect(await localRows(page, 'verification_records')).toEqual([saved]);
});

for (const mode of ['missing', 'wrong-publication', 'wrong-security'] as const) {
  test(`IG07 ${mode} interval cannot become a disposition from the single-day stock quote`, async ({ page }) => {
    await evidence(page, mode);
    await createConfirmedNote(page);
    const panel = await openVerification(page);
    await expect(panel.getByRole('alert')).toContainText(/区间.*(?:缺失|不可用)|身份.*不匹配|发布.*不匹配/);
    await expect(panel.getByRole('combobox', { name: '验证结论', exact: true })).not.toHaveValue('not_due');
    await panel.getByRole('combobox', { name: '验证结论', exact: true }).selectOption('supported');
    await panel.getByRole('textbox', { name: '验证依据', exact: true }).fill('Must not commit substituted quote');
    await expect(panel.getByRole('button', { name: '保存验证', exact: true })).toBeDisabled();
    expect(await localRows(page, 'verification_records')).toEqual([]);
  });
}

test('IG07 dirty verification blocks navigation and publication changes; dismiss retains input', async ({ page }) => {
  await evidence(page);
  await createConfirmedNote(page);
  const panel = await openVerification(page);
  await panel.getByRole('textbox', { name: '验证依据', exact: true }).fill('Unsaved verification draft');
  const dialogMessages: string[] = [];
  page.on('dialog', async dialog => { dialogMessages.push(dialog.message()); await dialog.dismiss(); });
  await page.getByRole('link', { name: '市场总览', exact: true }).click();
  await expect(page).toHaveURL(/view=review/);
  await expect(panel.getByRole('textbox', { name: '验证依据', exact: true })).toHaveValue('Unsaved verification draft');
  await panel.getByRole('combobox', { name: '验证发布', exact: true }).selectOption(OLD);
  await expect(panel.getByRole('combobox', { name: '验证发布', exact: true })).toHaveValue(ID);
  await expect(panel.getByRole('textbox', { name: '验证依据', exact: true })).toHaveValue('Unsaved verification draft');
  expect(dialogMessages.length).toBeGreaterThanOrEqual(2);
  expect(await localRows(page, 'verification_records')).toEqual([]);
});

test('IG07 late B response cannot replace newly selected A facts or enable a stale save', async ({ page }) => {
  const pending = await evidence(page, 'late');
  await createConfirmedNote(page);
  const panel = await openVerification(page);
  await pending.received;
  page.once('dialog', dialog => dialog.accept());
  await panel.getByRole('combobox', { name: '验证发布', exact: true }).selectOption(OLD);
  pending.release();
  await expect(panel.getByRole('combobox', { name: '验证发布', exact: true })).toHaveValue(OLD);
  await expect(panel.getByRole('region', { name: '验证区间事实', exact: true })).not.toContainText(ID);
  await expect(panel.getByRole('button', { name: '保存验证', exact: true })).toBeDisabled();
  expect(await localRows(page, 'verification_records')).toEqual([]);
});

for (const selection of ['revision', 'publication'] as const) {
  test(`IG07 collected3 unsaved ${selection} selection alone guards navigation and accepted switches`, async ({ page }) => {
    await evidence(page);
    const r1 = await createConfirmedNote(page);
    if (selection === 'revision') {
      await page.getByRole('textbox', { name: '判断', exact: true }).fill('Second confirmed judgment');
      await page.getByRole('button', { name: '确认笔记', exact: true }).click();
      await expect(page.locator('.local-note-editor').getByRole('status')).toContainText(`revision ${Number(r1.revision) + 1}`);
      await expect(page.locator('.local-note-editor').getByRole('status')).toContainText('回读验证通过');
    }
    await page.getByRole('button', { name: '验证判断', exact: true }).click();
    const panel = page.getByRole('region', { name: '判断验证', exact: true });
    const label = selection === 'revision' ? '原始修订' : '验证发布';
    const value = selection === 'revision' ? String(r1.revision) : ID;
    const control = panel.getByRole('combobox', { name: label, exact: true });
    await expect(panel.getByRole('combobox', { name: '原始修订', exact: true })).toHaveValue(
      String(Number(r1.revision) + (selection === 'revision' ? 1 : 0)),
    );
    await expect(panel.getByRole('combobox', { name: '验证发布', exact: true })).toHaveValue('');
    await control.selectOption(value);
    await expect(control).toHaveValue(value);
    await expect(panel.getByRole('region', { name: '原始判断', exact: true })).toContainText('Original strength judgment');
    await expect(panel.getByRole('textbox', { name: '验证依据', exact: true })).toHaveValue('');
    const before = page.url(), messages: string[] = [];
    const dismiss = async (dialog: import('@playwright/test').Dialog) => {
      messages.push(dialog.message()); await dialog.dismiss();
    };
    page.on('dialog', dismiss);
    await page.getByRole('link', { name: '市场总览', exact: true }).click();
    await expect(page).toHaveURL(before);
    await expect(control).toHaveValue(value);
    expect(messages).toHaveLength(1);
    page.off('dialog', dismiss);
    page.once('dialog', dialog => dialog.accept());
    const switched = selection === 'revision' ? '' : OLD;
    await control.selectOption(switched);
    await expect(control).toHaveValue(switched);
    page.on('dialog', dismiss);
    await page.getByRole('link', { name: '市场总览', exact: true }).click();
    await expect(page).toHaveURL(before);
    expect(messages).toHaveLength(2);
    expect(await localRows(page, 'verification_records')).toEqual([]);
  });
}

test('IG07 collected3 confirmed original and copied backup re-export with parseable revision/correction bindings', async ({ page }) => {
  await evidence(page);
  await createConfirmedNote(page);
  const panel = await openVerification(page);
  await panel.getByRole('textbox', { name: '验证依据', exact: true }).fill('Actual complete interval');
  await panel.getByRole('button', { name: '保存验证', exact: true }).click();
  await expect(panel.getByRole('status')).toContainText('回读验证通过');
  await panel.getByRole('button', { name: '追加修正', exact: true }).click();
  await panel.getByRole('combobox', { name: '验证结论', exact: true }).selectOption('weakened');
  await panel.getByRole('button', { name: '保存验证', exact: true }).click();
  await expect(panel.getByRole('status')).toContainText('回读验证通过');
  const backup = page.getByRole('region', { name: '备份与恢复', exact: true });
  const downloadText = async () => {
    const pending = page.waitForEvent('download');
    await backup.getByRole('button', { name: '导出本机备份', exact: true }).click();
    const stream = await (await pending).createReadStream();
    const chunks: Buffer[] = [];
    for await (const chunk of stream) chunks.push(Buffer.from(chunk));
    return Buffer.concat(chunks).toString('utf8');
  };
  const original = await downloadText();
  const damaged = parseReviewBackup(original);
  for (const row of damaged.verifications) {
    row.context!.adjusted_start = '2026-09-10';
    delete row.context!.calendar_sessions;
    row.fact_interval!.adjusted_start = '2026-09-10';
    row.fact_interval!.points = row.fact_interval!.points.slice(1);
  }
  const beforeImport = await localRows(page, 'verification_records');
  await backup.getByLabel('选择复盘备份文件').setInputFiles({
    name: 'missing-baseline.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(damaged)),
  });
  await expect(page.getByRole('alert').filter({ hasText: /备份文件无效或不完整/ })).toBeVisible();
  await expect(backup.getByRole('button', { name: '执行恢复', exact: true })).toBeDisabled();
  expect(await localRows(page, 'verification_records')).toEqual(beforeImport);
  await backup.getByLabel('选择复盘备份文件').setInputFiles({
    name: 'original.json', mimeType: 'application/json', buffer: Buffer.from(original),
  });
  await backup.getByRole('combobox', { name: '导入方式', exact: true }).selectOption('save-as-new');
  await backup.getByRole('button', { name: '执行恢复', exact: true }).click();
  await expect.poll(async () => (await localRows(page, 'notes')).length).toBe(2);
  const combined = parseReviewBackup(await downloadText());
  expect(combined.counts).toMatchObject({ notes: 2, verifications: 4 });
  expect(new Set(combined.revisions.map(row => row.revision_id)).size).toBe(combined.revisions.length);
  for (const note of combined.notes) {
    const rows = combined.verifications.filter(row => row.note_id === note.note_id);
    const first = rows.find(row => row.supersedes === null)!;
    expect(rows.find(row => row.supersedes === first.verification_id)?.outcome).toBe('weakened');
    expect(rows.every(row => combined.revisions.some(revision =>
      revision.note_id === note.note_id && revision.revision_id === row.revision_id && revision.state === 'confirmed',
    ))).toBe(true);
  }
  await backup.getByLabel('选择复盘备份文件').setInputFiles({
    name: 'combined.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(combined)),
  });
  await expect(backup).toContainText('未解决冲突');
});

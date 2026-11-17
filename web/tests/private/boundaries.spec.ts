import { expect, test } from '@playwright/test';
import { ID, mockMarket } from '../fixtures/market';
import { initialNote, mockReview, NOTE, reviewServer } from '../fixtures/review';

test('市场401锁定全部内容、取消请求且同主体恢复', async ({ page }) => {
  const db = reviewServer();
  await mockMarket(page); await mockReview(page, db);
  await page.goto(`/?view=review&note=${NOTE}&snapshot=${ID}`);
  await page.getByLabel('判断', { exact: true }).fill('市场401仍保留输入');
  await page.route('**/api/v1/**', route => route.fulfill({ status: 401, contentType: 'application/json', body: '{"code":"session_unauthorized"}' }));
  await page.getByRole('button', { name: '刷新快照' }).click();
  await expect(page.getByRole('heading', { name: '私人会话已锁定' })).toBeVisible();
  await expect(page.getByText('合成测试来源 / 测试版一')).not.toBeVisible();
  await expect(page.getByLabel('判断', { exact: true })).not.toBeVisible();
  await page.unroute('**/api/v1/**');
  await mockMarket(page);
  await page.getByLabel('一次性配对码').fill('synthetic-fixture-code');
  await page.getByRole('button', { name: '配对', exact: true }).click();
  await expect(page.getByLabel('判断', { exact: true })).toHaveValue('市场401仍保留输入');
});

test('csrf403不清行情且保留正文可重新保存', async ({ page }) => {
  const db = reviewServer();
  await mockMarket(page); await mockReview(page, db);
  await page.goto(`/?view=review&note=${NOTE}&snapshot=${ID}`);
  await page.getByLabel('判断', { exact: true }).fill('CSRF拒绝的输入');
  db.csrfDenied = true;
  await page.getByRole('button', { name: '保存草稿', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('CSRF');
  await expect(page.getByRole('banner', { name: '快照上下文' })).toContainText('合成测试来源 / 测试版一');
  await expect(page.getByLabel('判断', { exact: true })).toHaveValue('CSRF拒绝的输入');
  db.csrfDenied = false;
  await page.getByRole('button', { name: '保存草稿', exact: true }).click();
  await expect(page.getByText(/已保存.*版本 2/)).toBeVisible();
});

test('popstate与快照切换取消离开保URL和输入', async ({ page }) => {
  const db = reviewServer();
  await mockMarket(page); await mockReview(page, db);
  await page.goto(`/?snapshot=${ID}`);
  await page.getByRole('link', { name: '我的复盘' }).click();
  await page.getByRole('button', { name: /人工观察 草稿/ }).click();
  await page.getByLabel('判断', { exact: true }).fill('离开保护');
  const before = page.url();
  page.once('dialog', dialog => dialog.dismiss());
  await page.goBack();
  await expect(page).toHaveURL(before);
  await expect(page.getByLabel('判断', { exact: true })).toHaveValue('离开保护');
  page.once('dialog', dialog => dialog.dismiss());
  await page.getByLabel('交易日期', { exact: true }).fill('2026-09-09');
  await expect(page).toHaveURL(before);
  await expect(page.getByLabel('交易日期', { exact: true })).toHaveValue('2026-09-10');
});

test('无快照仍可编辑，笔记20/50/100分页遵循has_more', async ({ page }) => {
  const db = reviewServer();
  db.notes = Array.from({ length: 51 }, (_, index) => ({ ...structuredClone(initialNote), id: index ? crypto.randomUUID() : NOTE, content: { ...initialNote.content, title: `观察 ${index}` } }));
  await mockMarket(page, { empty: true }); await mockReview(page, db);
  await page.goto(`/?view=review&note=${NOTE}&date=2020-01-01`);
  await expect(page.getByRole('button', { name: '新建笔记', exact: true })).toBeDisabled();
  await page.getByLabel('判断', { exact: true }).fill('无快照原创');
  await page.getByRole('button', { name: '保存草稿', exact: true }).click();
  await expect(page.getByText(/已保存.*版本 2/)).toBeVisible();
  await page.getByLabel('笔记每页条数').selectOption('20');
  const list = page.getByRole('region', { name: '笔记列表', exact: true });
  await expect(list.locator('.note-row')).toHaveCount(20);
  await list.getByRole('button', { name: '下一页' }).click();
  await expect(list.getByText(/第 2 页/)).toBeVisible();
  await page.getByLabel('笔记每页条数').selectOption('100');
  await expect(list.locator('.note-row')).toHaveCount(51);
  await expect(list.getByRole('button', { name: '下一页' })).toBeDisabled();
});

test('inactive重复POST要求明确带当前版本恢复', async ({ page }) => {
  const db = reviewServer();
  db.watches.push({ id: crypto.randomUUID(), owner_id: db.owner, security_id: 'TEST001', snapshot_id: ID,
    active: false, version: 7, added_at: initialNote.saved_at, updated_at: initialNote.saved_at, facts: { status: 'unavailable' } });
  await mockMarket(page); await mockReview(page, db);
  await page.goto(`/?snapshot=${ID}`);
  await page.getByRole('button', { name: '加入自选 TEST001', exact: true }).click();
  await expect(page.getByRole('button', { name: '此项已移除，明确恢复' })).toBeVisible();
  expect(db.watches[0].active).toBe(false);
  await page.getByRole('button', { name: '此项已移除，明确恢复' }).click();
  await expect(page.getByText('已恢复自选')).toBeVisible();
  expect(db.calls.at(-1)?.match).toBe('"7"');
});

test('来源拒绝时自选名称和价格均显示来源不可用', async ({ page }) => {
  const db = reviewServer(), market = { denied: false };
  db.watches.push({
    id: crypto.randomUUID(), owner_id: db.owner, security_id: 'TEST001', snapshot_id: ID,
    active: true, version: 1, added_at: initialNote.saved_at, updated_at: initialNote.saved_at,
    facts: { status: 'unavailable' },
  });
  await mockMarket(page, market); await mockReview(page, db);
  await page.goto(`/?view=review&snapshot=${ID}`);
  await page.getByRole('button', { name: '自选', exact: true }).click();
  const row = page.getByRole('row').filter({ hasText: 'TEST001' });
  await expect(row.locator('th small')).toHaveText('合成测试证券1');

  market.denied = true;
  await page.getByRole('button', { name: '刷新快照' }).click();

  await expect(row.locator('th small')).toHaveText('来源不可用');
  await expect(row.locator('td').first()).toHaveText('来源不可用');
  await expect(row).not.toContainText('本快照无行情');
});

test('验证输入受离开防护，切页后恢复且不进入storage或URL', async ({ page }) => {
  const db = reviewServer();
  db.notes[0].state = 'confirmed'; db.revisions[0].state = 'confirmed';
  await mockMarket(page); await mockReview(page, db);
  await page.goto(`/?view=review&note=${NOTE}&snapshot=${ID}`);
  await page.getByRole('button', { name: '人工验证', exact: true }).click();
  await page.getByLabel('验证依据').fill('验证未保存输入');
  const before = page.url();
  page.once('dialog', dialog => dialog.dismiss());
  await page.getByRole('link', { name: '市场总览', exact: true }).click();
  await expect(page).toHaveURL(before);
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('link', { name: '市场总览', exact: true }).click();
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('link', { name: '我的复盘' }).click();
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('button', { name: '人工验证', exact: true }).click();
  await expect(page.getByLabel('验证依据')).toHaveValue('验证未保存输入');
  expect(page.url()).not.toContain('验证未保存输入');
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
});

test('来源恢复必须重读私人facts，不重新展示撤权前缓存', async ({ page }) => {
  const db = reviewServer(), market = { denied: false };
  db.notes[0].facts = { status: 'available', snapshot_id: ID, trade_date: '2026-09-10',
    as_of: '2026-09-10T12:00:00Z', source_time: null, provenance: 'contemporaneous', manifest: {}, result: {
      security_id: 'TEST001', name: '已撤权旧名称', close: '10', change: '0', amount: '100',
      exchange: null, board: null, suspended: false, missing_reasons: [],
      currency: 'CNY', price_unit: 'currency/share', volume_unit: 'share', amount_unit: 'currency', change_unit: 'fraction',
    } };
  await mockMarket(page, market); await mockReview(page, db);
  await page.goto(`/?view=review&note=${NOTE}&snapshot=${ID}`);
  await page.getByRole('button', { name: '修订历史', exact: true }).click();
  db.revisions[0].facts = structuredClone(db.notes[0].facts);
  await page.getByRole('button', { name: /查看版本 1/ }).click();
  await expect(page.getByRole('region', { name: '原快照事实' }).last()).toContainText('已撤权旧名称');
  market.denied = true;
  await page.getByRole('button', { name: '刷新快照' }).click();
  await expect(page.getByRole('button', { name: '重新检查访问权限' })).toBeVisible();
  db.notes[0].facts = { status: 'unavailable' }; db.revisions[0].facts = { status: 'unavailable' };
  market.denied = false;
  await page.getByRole('button', { name: '重新检查访问权限' }).click();
  await expect(page.getByRole('banner', { name: '快照上下文' })).toContainText('合成测试来源');
  await expect(page.getByText(/已撤权旧名称/)).toHaveCount(0);
});

test('来源恢复重新GET并为脏草稿恢复新facts且保留原创', async ({ page }) => {
  const db = reviewServer(), market = { denied: false };
  db.notes[0].facts = { status: 'available', snapshot_id: ID, trade_date: '2026-09-10',
    as_of: '2026-09-10T12:00:00Z', source_time: null, provenance: 'contemporaneous', manifest: {}, result: {
      security_id: 'TEST001', name: '授权前名称', close: '10', change: '0', amount: '100',
      exchange: null, board: null, suspended: false, missing_reasons: [],
      currency: 'CNY', price_unit: 'currency/share', volume_unit: 'share', amount_unit: 'currency', change_unit: 'fraction',
    } };
  await mockMarket(page, market); await mockReview(page, db);
  await page.goto(`/?view=review&note=${NOTE}&snapshot=${ID}`);
  await page.getByLabel('判断', { exact: true }).fill('来源恢复仍保留原创');
  market.denied = true;
  await page.getByRole('button', { name: '刷新快照' }).click();
  await expect(page.getByText('来源不可用，原创内容仍可保存。').first()).toBeVisible();
  db.notes[0].facts = { ...db.notes[0].facts, status: 'available', result: {
    ...db.notes[0].facts.result!, name: '重新授权新名称',
  } };
  market.denied = false;
  await page.getByRole('button', { name: '重新检查访问权限' }).click();
  await expect(page.getByLabel('判断', { exact: true })).toHaveValue('来源恢复仍保留原创');
  await expect(page.getByRole('region', { name: '原快照事实' }).first()).toContainText('重新授权新名称');
});

test('403验证事实撤权保留人工历史与待提交更正', async ({ page }) => {
  const db = reviewServer(), market = { denied: false };
  db.notes[0].state = 'confirmed'; db.revisions[0].state = 'confirmed';
  db.verifications.push({ id: crypto.randomUUID(), owner_id: db.owner, note_id: NOTE, revision_id: db.notes[0].revision_id,
    snapshot_id: ID, outcome: 'supported', evidence: '已有人工验证文字', supersedes: null, created_at: initialNote.saved_at });
  await mockMarket(page, market); await mockReview(page, db);
  await page.goto(`/?view=review&note=${NOTE}&snapshot=${ID}`);
  await page.getByRole('button', { name: '人工验证', exact: true }).click();
  await page.getByRole('button', { name: '更正此记录' }).click();
  await page.getByLabel('验证依据').fill('更正输入不能清除');
  market.denied = true;
  await page.getByRole('button', { name: '读取此记录验证事实' }).click();
  await expect(page.getByRole('button', { name: '重新检查访问权限' })).toBeVisible();
  await expect(page.getByText('已有人工验证文字', { exact: true })).toBeVisible();
  await expect(page.getByLabel('验证依据')).toHaveValue('更正输入不能清除');
});

test('期限锁定、配对提交清码、退出取消与确认丢弃', async ({ page }) => {
  const db = reviewServer();
  await mockMarket(page); await mockReview(page, db);
  let shortSession = false;
  await page.route('**/api/owner/v1/session', async route => {
    if (route.request().method() !== 'GET' || !shortSession) return route.fallback();
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
      owner_id: db.owner, session_id: '40000000-0000-4000-8000-000000000001', expires_at: new Date(Date.now() + 900).toISOString(),
    }) });
  });
  await page.goto(`/?view=review&note=${NOTE}&snapshot=${ID}`);
  await page.getByLabel('判断', { exact: true }).fill('退出时丢弃');
  shortSession = true;
  await page.getByRole('button', { name: '检查会话' }).click();
  await expect(page.getByRole('heading', { name: '私人会话已锁定' })).toBeVisible();
  shortSession = false;
  await page.getByLabel('一次性配对码').fill('synthetic-fixture-code');
  await page.getByRole('button', { name: '配对', exact: true }).click();
  await expect(page.getByLabel('判断', { exact: true })).toHaveValue('退出时丢弃');
  page.once('dialog', dialog => dialog.dismiss());
  await page.getByRole('button', { name: '退出私人会话' }).click();
  await expect(page.getByLabel('判断', { exact: true })).toHaveValue('退出时丢弃');
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('button', { name: '退出私人会话' }).click();
  await expect(page.getByLabel('一次性配对码')).toHaveValue('');
  await page.getByLabel('一次性配对码').fill('synthetic-fixture-code');
  await page.getByRole('button', { name: '配对', exact: true }).click();
  await expect(page.getByLabel('判断', { exact: true })).toHaveValue('原判断');
});

test('已有草稿以当前快照另存，不移动原笔记引用', async ({ page }) => {
  const db = reviewServer();
  await mockMarket(page); await mockReview(page, db);
  await page.goto(`/?view=review&note=${NOTE}&snapshot=${ID}`);
  await page.getByRole('button', { name: '以当前快照另存' }).click();
  await expect(page.getByText(/另存来源修订/)).toBeVisible();
  const sent = db.calls.find(call => call.path.endsWith('/fork'));
  expect(JSON.parse(sent!.body).snapshot_id).toBe(ID);
  expect(db.notes.find(note => note.id === NOTE)?.snapshot_id).toBe(ID);
});

test('退出在撤销响应等待期间立即隐藏并阻止保存', async ({ page }) => {
  const db = reviewServer();
  await mockMarket(page); await mockReview(page, db);
  let release = () => {};
  await page.route('**/api/owner/v1/session', async route => {
    if (route.request().method() !== 'DELETE') return route.fallback();
    await new Promise<void>(resolve => { release = resolve; });
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{"status":"logged_out"}' });
  });
  await page.goto(`/?view=review&note=${NOTE}&snapshot=${ID}`);
  await page.getByLabel('判断', { exact: true }).fill('确认退出即丢弃');
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('button', { name: '退出私人会话' }).click();
  try {
    await expect(page.getByRole('heading', { name: '私人会话已锁定' })).toBeVisible();
    await expect(page.getByRole('button', { name: '保存草稿', exact: true })).not.toBeVisible();
  } finally { release(); }
});

test('区间列表与行情详情均提供私人记录动作', async ({ page }) => {
  await mockMarket(page); await mockReview(page, reviewServer());
  await page.goto(`/?view=analysis&snapshot=${ID}`);
  await expect(page.getByRole('button', { name: '记录笔记 TEST001', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'TEST001', exact: true }).click();
  await expect(page.getByRole('dialog').getByRole('button', { name: '记录笔记 TEST001', exact: true })).toBeVisible();
});

test('200字符security ID快捷笔记发送合法可辨识标题', async ({ page }) => {
  const security = `S${'X'.repeat(199)}`;
  const db = reviewServer();
  await mockMarket(page, { securityId: security }); await mockReview(page, db);
  await page.goto(`/?snapshot=${ID}`);
  await page.getByRole('button', { name: `记录笔记 ${security}`, exact: true }).first().click();
  const sent = db.calls.find(call => call.path === '/notes');
  const title = JSON.parse(sent!.body).content.title as string;
  expect([...title]).toHaveLength(99);
  expect(title.startsWith(security.slice(0, 96))).toBe(true);
  expect(title.endsWith(' 观察')).toBe(true);
});

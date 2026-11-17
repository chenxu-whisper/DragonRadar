import { expect, test, type Page, type Route } from '@playwright/test';
import { ID, mockMarket } from '../fixtures/market';
import { mockReview, NOTE, reviewServer } from '../fixtures/review';

async function delayWrite(page: Page, matches: (route: Route) => boolean) {
  let release = () => {};
  let markStarted = () => {};
  const started = new Promise<void>(resolve => { markStarted = resolve; });
  const gate = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/owner/v1/**', async route => {
    if (!matches(route)) return route.fallback();
    markStarted();
    await gate;
    await route.fallback();
  });
  return { started, release };
}

test('fork 后回到旧笔记，详情失败也不显示或保存新笔记', async ({ page }) => {
  const db = reviewServer();
  await mockMarket(page); await mockReview(page, db);
  await page.goto(`/?view=review&note=${NOTE}&snapshot=${ID}`);
  await page.getByRole('button', { name: '另存为新笔记' }).click();
  await expect(page.getByText(/另存来源修订/)).toBeVisible();
  const forked = db.notes[0].id;
  expect(forked).not.toBe(NOTE);
  await page.route(`**/api/owner/v1/notes/${NOTE}`, route => {
    if (route.request().method() === 'GET') {
      return route.fulfill({ status: 503, contentType: 'application/json', body: '{"code":"storage_unavailable"}' });
    }
    return route.fallback();
  });

  await page.locator('.note-row').filter({ hasText: '人工观察' }).last().click();

  await expect(page).toHaveURL(new RegExp(`note=${NOTE}`));
  await expect(page.getByText(/另存来源修订/)).toHaveCount(0);
  await page.getByLabel('标题', { exact: true }).fill('旧笔记仍独立');
  await page.getByRole('button', { name: '保存草稿', exact: true }).click();
  await expect.poll(() => db.calls.at(-1)?.path).toBe(`/notes/${NOTE}`);
});

test('普通新建迟到成功不覆盖已接受导航', async ({ page }) => {
  const db = reviewServer();
  await mockMarket(page); await mockReview(page, db);
  const delayed = await delayWrite(page, route => new URL(route.request().url()).pathname.endsWith('/notes') && route.request().method() === 'POST');
  await page.goto(`/?view=review&snapshot=${ID}`);
  await page.getByRole('button', { name: '新建笔记', exact: true }).click();
  await delayed.started;
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('link', { name: '市场总览', exact: true }).click();
  delayed.release();

  await expect.poll(() => db.notes.length).toBe(2);
  await page.waitForTimeout(250);
  await expect(page).toHaveURL(/view=overview/);
});

test('快捷新建迟到成功不派发打开笔记导航', async ({ page }) => {
  const db = reviewServer();
  await mockMarket(page); await mockReview(page, db);
  const delayed = await delayWrite(page, route => new URL(route.request().url()).pathname.endsWith('/notes') && route.request().method() === 'POST');
  await page.goto(`/?snapshot=${ID}`);
  await page.getByRole('button', { name: '记录笔记 TEST001', exact: true }).first().click();
  await delayed.started;
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('link', { name: '区间分析', exact: true }).click();
  delayed.release();

  await expect.poll(() => db.notes.length).toBe(2);
  await page.waitForTimeout(250);
  await expect(page).toHaveURL(/view=analysis/);
});

test('fork 迟到成功不 replace 已接受导航', async ({ page }) => {
  const db = reviewServer();
  await mockMarket(page); await mockReview(page, db);
  const delayed = await delayWrite(page, route => new URL(route.request().url()).pathname.endsWith('/fork'));
  await page.goto(`/?view=review&note=${NOTE}&snapshot=${ID}`);
  await page.getByRole('button', { name: '另存为新笔记' }).click();
  await delayed.started;
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('link', { name: '市场总览', exact: true }).click();
  delayed.release();

  await expect.poll(() => db.notes.length).toBe(2);
  await page.waitForTimeout(250);
  await expect(page).toHaveURL(/view=overview/);
});

test('取消离开后普通新建迟到成功仍打开新笔记', async ({ page }) => {
  const db = reviewServer();
  await mockMarket(page); await mockReview(page, db);
  const delayed = await delayWrite(page, route => new URL(route.request().url()).pathname.endsWith('/notes') && route.request().method() === 'POST');
  await page.goto(`/?view=review&snapshot=${ID}`);
  await page.getByRole('button', { name: '新建笔记', exact: true }).click();
  await delayed.started;
  page.once('dialog', dialog => dialog.dismiss());
  await page.getByRole('link', { name: '市场总览', exact: true }).click();
  delayed.release();

  await expect.poll(() => db.notes.length).toBe(2);
  await page.waitForTimeout(250);
  await expect(page).toHaveURL(new RegExp(`view=review.*note=${db.notes[0].id}`));
});

test('正文与确认校验关联字段并聚焦首个无效控件', async ({ page }) => {
  await mockMarket(page); await mockReview(page, reviewServer());
  await page.goto(`/?view=review&note=${NOTE}&snapshot=${ID}`);
  await page.getByLabel('标题', { exact: true }).fill('');
  await page.getByRole('button', { name: '保存草稿', exact: true }).click();
  const title = page.getByLabel('标题', { exact: true });
  await expect(title).toHaveAttribute('aria-invalid', 'true');
  const titleError = await title.getAttribute('aria-describedby');
  await expect(page.locator(`#${titleError}`)).toContainText('标题');
  await expect(title).toBeFocused();

  await title.fill('可确认草稿');
  await page.getByLabel('判断', { exact: true }).fill('');
  await page.getByLabel('依据', { exact: true }).fill('');
  await page.getByLabel('失效条件', { exact: true }).fill('');
  await page.getByLabel('验证日期').fill('');
  await page.getByRole('button', { name: '保存草稿', exact: true }).click();
  await page.getByRole('button', { name: '确认复盘', exact: true }).click();
  const judgment = page.getByLabel('判断', { exact: true });
  await expect(judgment).toHaveAttribute('aria-invalid', 'true');
  await expect(page.locator(`#${await judgment.getAttribute('aria-describedby')}`)).toBeVisible();
  await expect(judgment).toBeFocused();
});

test('人工验证校验关联快照和依据并聚焦首个无效控件', async ({ page }) => {
  const db = reviewServer();
  db.notes[0].state = 'confirmed'; db.revisions[0].state = 'confirmed';
  await mockMarket(page); await mockReview(page, db);
  await page.goto(`/?view=review&note=${NOTE}&snapshot=${ID}`);
  await page.getByRole('button', { name: '人工验证', exact: true }).click();
  await page.getByLabel('人工结论').selectOption('supported');
  await page.getByRole('button', { name: '记录验证', exact: true }).click();

  const snapshot = page.getByLabel('验证快照', { exact: true });
  const evidence = page.getByLabel('验证依据');
  await expect(snapshot).toHaveAttribute('aria-invalid', 'true');
  await expect(evidence).toHaveAttribute('aria-invalid', 'true');
  await expect(page.locator(`#${await snapshot.getAttribute('aria-describedby')}`)).toBeVisible();
  await expect(snapshot).toBeFocused();
});

test('验证快照列表失败显示脱敏状态，重试后保留草稿并可提交', async ({ page }) => {
  const db = reviewServer();
  db.notes[0].state = 'confirmed'; db.revisions[0].state = 'confirmed';
  await mockMarket(page); await mockReview(page, db);
  let mode: 'delay' | 'fail' | 'ok' = 'delay';
  let release = () => {};
  await page.route('**/api/v1/snapshots?trade_date=2026-09-10&limit=100&offset=0', async route => {
    if (mode === 'delay') await new Promise<void>(resolve => { release = resolve; });
    if (mode === 'fail') {
      return route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: '{"code":"storage_unavailable","message":"sensitive upstream snapshot detail"}',
      });
    }
    return route.fallback();
  });
  await page.goto(`/?view=review&note=${NOTE}&snapshot=${ID}`);
  await page.getByRole('button', { name: '人工验证', exact: true }).click();
  await page.getByLabel('人工结论').selectOption('supported');
  await page.getByLabel('验证依据').fill('快照读取失败期间保留的验证草稿');

  await page.getByLabel('验证快照日期').fill('2026-09-10');
  await expect(page.getByRole('status').filter({ hasText: '正在读取私人记录' })).toBeVisible();
  mode = 'fail';
  release();

  const alert = page.getByRole('alert').filter({ hasText: '数据服务暂不可用' });
  const snapshot = page.getByLabel('验证快照', { exact: true });
  await expect(alert).toBeVisible();
  await expect(alert).not.toContainText('sensitive upstream snapshot detail');
  await expect(alert.getByRole('button', { name: '重新读取' })).toBeVisible();
  await expect(snapshot).not.toHaveAttribute('aria-invalid');
  await expect(page.getByLabel('人工结论')).toHaveValue('supported');
  await expect(page.getByLabel('验证依据')).toHaveValue('快照读取失败期间保留的验证草稿');

  mode = 'ok';
  await alert.getByRole('button', { name: '重新读取' }).click();
  await snapshot.selectOption(ID);
  await page.getByRole('button', { name: '记录验证', exact: true }).click();
  await expect(page.getByText('快照读取失败期间保留的验证草稿', { exact: true })).toBeVisible();
});

test('人工验证成功后清空该笔记草稿、刷新历史且离开不再提示', async ({ page }) => {
  const db = reviewServer();
  db.notes[0].state = 'confirmed'; db.revisions[0].state = 'confirmed';
  await mockMarket(page); await mockReview(page, db);
  await page.goto(`/?view=review&note=${NOTE}&snapshot=${ID}`);
  await page.getByRole('button', { name: '人工验证', exact: true }).click();
  await page.getByLabel('验证快照日期').fill('2026-09-10');
  await page.getByLabel('验证快照', { exact: true }).selectOption(ID);
  await page.getByLabel('人工结论').selectOption('supported');
  await page.getByLabel('验证依据').fill('成功后只保留在验证历史');

  await page.getByRole('button', { name: '记录验证', exact: true }).click();

  await expect(page.getByText('成功后只保留在验证历史', { exact: true })).toBeVisible();
  await expect(page.getByLabel('确认修订', { exact: true })).toHaveValue('1');
  await expect(page.getByLabel('验证快照日期')).toHaveValue('');
  await expect(page.getByLabel('验证快照', { exact: true })).toHaveValue('');
  await expect(page.getByLabel('人工结论')).toHaveValue('');
  await expect(page.getByLabel('验证依据')).toHaveValue('');
  await page.getByRole('link', { name: '市场总览', exact: true }).click();
  await expect(page).toHaveURL(/view=overview/);
});

for (const failure of [
  { name: '确定失败', status: 409, body: { code: 'version_conflict', current: { version: 2 } } },
  { name: '结果未知', status: 503, body: { code: 'storage_unavailable' } },
]) {
  test(`人工验证${failure.name}保留全部输入`, async ({ page }) => {
    const db = reviewServer();
    db.notes[0].state = 'confirmed'; db.revisions[0].state = 'confirmed';
    const prior = crypto.randomUUID();
    db.verifications.push({
      id: prior, owner_id: db.owner, note_id: NOTE, revision_id: db.notes[0].revision_id,
      snapshot_id: ID, outcome: 'supported', evidence: '原验证依据', supersedes: null,
      created_at: db.notes[0].saved_at,
    });
    await mockMarket(page); await mockReview(page, db);
    await page.route(`**/api/owner/v1/notes/${NOTE}/verifications`, route => {
      if (route.request().method() !== 'POST') return route.fallback();
      return route.fulfill({
        status: failure.status,
        contentType: 'application/json',
        body: JSON.stringify(failure.body),
      });
    });
    await page.goto(`/?view=review&note=${NOTE}&snapshot=${ID}`);
    await page.getByRole('button', { name: '人工验证', exact: true }).click();
    await page.getByRole('button', { name: '更正此记录' }).click();
    await page.getByLabel('验证快照日期').fill('2026-09-10');
    await page.getByLabel('验证快照', { exact: true }).selectOption(ID);
    await page.getByLabel('人工结论').selectOption('refuted');
    await page.getByLabel('验证依据').fill('失败后必须保留的完整输入');

    await page.getByRole('button', { name: '记录验证', exact: true }).click();

    await expect(page.getByRole('alert')).toBeVisible();
    await expect(page.getByLabel('确认修订', { exact: true })).toHaveValue('1');
    await expect(page.getByLabel('验证快照日期')).toHaveValue('2026-09-10');
    await expect(page.getByLabel('验证快照', { exact: true })).toHaveValue(ID);
    await expect(page.getByLabel('人工结论')).toHaveValue('refuted');
    await expect(page.getByLabel('验证依据')).toHaveValue('失败后必须保留的完整输入');
    await expect(page.getByText(`更正自 ${prior}，追加记录，不覆盖旧结论。`)).toBeVisible();
  });
}

test('取消切换笔记时 URL 和当前 subtab 均保持', async ({ page }) => {
  const db = reviewServer();
  const other = crypto.randomUUID();
  db.notes.push({
    ...structuredClone(db.notes[0]), id: other, revision_id: crypto.randomUUID(),
    content: { ...db.notes[0].content, title: '另一份笔记' },
  });
  await mockMarket(page); await mockReview(page, db);
  await page.goto(`/?view=review&note=${NOTE}&snapshot=${ID}`);
  await page.getByLabel('判断', { exact: true }).fill('切换笔记前的未保存输入');
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('button', { name: '修订历史', exact: true }).click();
  await expect(page.getByRole('heading', { name: '修订历史', exact: true })).toBeVisible();
  const before = page.url();

  page.once('dialog', dialog => dialog.dismiss());
  await page.locator('.note-row').filter({ hasText: '另一份笔记' }).click();

  await expect(page).toHaveURL(before);
  await expect(page.getByRole('heading', { name: '修订历史', exact: true })).toBeVisible();
});

test('取消打开完整详情时 URL 和 SecurityDrawer 均保持', async ({ page }) => {
  const db = reviewServer();
  await mockMarket(page); await mockReview(page, db);
  await page.goto(`/?view=review&note=${NOTE}&snapshot=${ID}`);
  await page.getByLabel('判断', { exact: true }).fill('打开详情前的未保存输入');
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('link', { name: '市场总览', exact: true }).click();
  await page.getByRole('button', { name: '合成测试证券1', exact: true }).click();
  const drawer = page.getByRole('dialog');
  await expect(drawer).toBeVisible();
  const before = page.url();

  page.once('dialog', dialog => dialog.dismiss());
  await drawer.getByRole('link', { name: '打开完整详情' }).click();

  await expect(page).toHaveURL(before);
  await expect(drawer).toBeVisible();
});

test('取消主导航时 URL 和移动菜单均保持', async ({ page }) => {
  const db = reviewServer();
  await page.setViewportSize({ width: 600, height: 900 });
  await mockMarket(page); await mockReview(page, db);
  await page.goto(`/?view=review&note=${NOTE}&snapshot=${ID}`);
  await page.getByLabel('判断', { exact: true }).fill('主导航前的未保存输入');
  await page.getByRole('button', { name: '展开导航' }).click();
  const menu = page.getByRole('button', { name: '收起导航' });
  await expect(menu).toHaveAttribute('aria-expanded', 'true');
  const before = page.url();

  page.once('dialog', dialog => dialog.dismiss());
  await page.getByRole('link', { name: '市场总览', exact: true }).click();

  await expect(page).toHaveURL(before);
  await expect(menu).toHaveAttribute('aria-expanded', 'true');
});

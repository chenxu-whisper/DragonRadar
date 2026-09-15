import { expect, test, type Route } from '@playwright/test';
import { ID, mockMarket } from '../fixtures/market';
import { mockReview, NOTE, reviewServer } from '../fixtures/review';

const privateChunk = '**/src/features/review/PrivateApp.tsx*';

test('私人入口加载期间显示可访问状态', async ({ page }) => {
  const db = reviewServer();
  db.authorized = false;
  await mockMarket(page);
  await mockReview(page, db);
  let release = () => {};
  await page.route(privateChunk, async route => {
    await new Promise<void>(resolve => { release = resolve; });
    await route.continue();
  });

  const navigation = page.goto('/');
  try {
    await expect(page.getByRole('status')).toHaveText('正在加载私人复盘…');
  } finally {
    release();
  }
  await navigation;
  await expect(page.getByRole('heading', { name: '私人会话已锁定' })).toBeVisible();
});

test('私人入口 chunk 失败显示固定错误且恢复后可重试', async ({ page }) => {
  const db = reviewServer();
  db.authorized = false;
  await mockMarket(page);
  await mockReview(page, db);
  let failChunk = true;
  const intercept = async (route: Route) => {
    if (failChunk) return route.abort('failed');
    return route.continue();
  };
  await page.route(privateChunk, intercept);

  await page.goto('/');
  const alert = page.getByRole('alert');
  await expect(alert).toContainText('私人复盘加载失败');
  await expect(alert).not.toContainText('PrivateApp.tsx');
  await expect(alert).not.toContainText('/src/');

  failChunk = false;
  await page.unroute(privateChunk, intercept);
  await page.getByRole('button', { name: '重新加载并重试' }).click();
  await expect(page.getByRole('heading', { name: '私人会话已锁定' })).toBeVisible();
  await expect(page.getByLabel('一次性配对码')).toBeVisible();
});

test('配对 POST 在途时禁用撤销', async ({ page }) => {
  const db = reviewServer();
  db.authorized = false;
  await mockMarket(page);
  await mockReview(page, db);
  let releasePair = () => {};
  let markPairStarted = () => {};
  const pairStarted = new Promise<void>(resolve => { markPairStarted = resolve; });
  await page.route('**/api/owner/v1/pair', async route => {
    markPairStarted();
    await new Promise<void>(resolve => { releasePair = resolve; });
    await route.fallback();
  });
  await page.goto('/');
  await page.getByLabel('一次性配对码').fill('synthetic-fixture-code');
  await page.getByRole('button', { name: '配对', exact: true }).click();
  await pairStarted;

  try {
    await expect(page.getByRole('button', { name: '撤销当前会话并清空内存' })).toBeDisabled();
  } finally {
    releasePair();
  }
});

test('配对期间 focus 不以旧 Cookie 检查且成功后执行权威检查', async ({ page }) => {
  const db = reviewServer();
  db.authorized = false;
  await mockMarket(page);
  await mockReview(page, db);
  let sessionGets = 0;
  let releasePair = () => {};
  let markPairStarted = () => {};
  const pairStarted = new Promise<void>(resolve => { markPairStarted = resolve; });
  await page.route('**/api/owner/v1/session', async route => {
    if (route.request().method() === 'GET') sessionGets++;
    await route.fallback();
  });
  await page.route('**/api/owner/v1/pair', async route => {
    markPairStarted();
    await new Promise<void>(resolve => { releasePair = resolve; });
    await route.fallback();
  });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: '私人会话已锁定' })).toBeVisible();
  await page.waitForTimeout(100);
  const sessionGetsBeforePair = sessionGets;
  await page.getByLabel('一次性配对码').fill('synthetic-fixture-code');
  await page.getByRole('button', { name: '配对', exact: true }).click();
  await pairStarted;

  try {
    await page.evaluate(() => window.dispatchEvent(new Event('focus')));
    await page.waitForTimeout(100);
    expect(sessionGets).toBe(sessionGetsBeforePair);
  } finally {
    releasePair();
  }
  await expect(page.getByRole('region', { name: '私人会话' })).toBeVisible();
  expect(sessionGets).toBe(sessionGetsBeforePair + 1);
});

test('配对前在途的旧会话检查不会覆盖配对后的权威检查', async ({ page }) => {
  const db = reviewServer();
  db.authorized = false;
  await mockMarket(page);
  await mockReview(page, db);
  let sessionGets = 0;
  let pairingStarted = false;
  const releaseOldChecks: (() => void)[] = [];
  let markOldCheckStarted = () => {};
  const oldCheckStarted = new Promise<void>(resolve => { markOldCheckStarted = resolve; });
  await page.route('**/api/owner/v1/session', async route => {
    if (route.request().method() !== 'GET') return route.fallback();
    sessionGets++;
    if (!pairingStarted) {
      markOldCheckStarted();
      await new Promise<void>(resolve => { releaseOldChecks.push(resolve); });
      return route.fulfill({
        status: 401,
        contentType: 'application/json',
        body: '{"code":"session_unauthorized"}',
      });
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        owner_id: db.owner,
        session_id: '40000000-0000-4000-8000-000000000002',
        expires_at: '2099-01-01T00:00:00Z',
      }),
    });
  });
  await page.route('**/api/owner/v1/pair', async route => {
    pairingStarted = true;
    await route.fallback();
  });
  await page.goto('/');
  await oldCheckStarted;
  await page.getByLabel('一次性配对码').fill('synthetic-fixture-code');
  await page.getByRole('button', { name: '配对', exact: true }).click();

  try {
    await expect(page.getByRole('region', { name: '私人会话' })).toBeVisible();
    expect(sessionGets).toBeGreaterThanOrEqual(2);
    const oldResponse = page.waitForResponse(response =>
      response.request().method() === 'GET' &&
      new URL(response.url()).pathname === '/api/owner/v1/session' &&
      response.status() === 401);
    releaseOldChecks.forEach(release => release());
    const response = await oldResponse;
    await response.finished();
    await page.evaluate(() => new Promise<void>(resolve =>
      requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    await expect(page.getByRole('heading', { name: '私人会话已锁定' })).not.toBeVisible();
    await expect(page.getByRole('region', { name: '私人会话' })).toBeVisible();
  } finally {
    releaseOldChecks.forEach(release => release());
  }
});

test('卸载后在途配对成功不会启动新的会话检查', async ({ page }) => {
  const db = reviewServer();
  await mockMarket(page);
  await mockReview(page, db);
  let trackHarness = false;
  let sessionGets = 0;
  let releasePair = () => {};
  let markPairStarted = () => {};
  const pairStarted = new Promise<void>(resolve => { markPairStarted = resolve; });
  await page.route('**/api/owner/v1/session', async route => {
    if (!trackHarness || route.request().method() !== 'GET') return route.fallback();
    sessionGets++;
    return route.fulfill({
      status: 401,
      contentType: 'application/json',
      body: '{"code":"session_unauthorized"}',
    });
  });
  await page.route('**/api/owner/v1/pair', async route => {
    markPairStarted();
    await new Promise<void>(resolve => { releasePair = resolve; });
    await route.fallback();
  });
  await page.goto('/');
  await expect(page.getByRole('region', { name: '私人会话' })).toBeVisible();

  trackHarness = true;
  await page.evaluate(async () => {
    const load = (path: string) => import(/* @vite-ignore */ path);
    const [{ default: React }, { default: ReactDOM }, { SessionProvider }] = await Promise.all([
      load('/.temp/vite/deps/react.js'),
      load('/.temp/vite/deps/react-dom_client.js'),
      load('/src/features/review/SessionProvider.tsx'),
    ]);
    const host = document.createElement('div');
    host.id = 'session-provider-unmount-harness';
    document.body.append(host);
    const root = ReactDOM.createRoot(host);
    root.render(React.createElement(SessionProvider, null, React.createElement('span', null, 'harness')));
    (window as typeof window & { sessionProviderTestRoot?: { unmount(): void } }).sessionProviderTestRoot = root;
  });
  const harness = page.locator('#session-provider-unmount-harness');
  await expect(harness.getByRole('heading', { name: '私人会话已锁定' })).toBeVisible();
  expect(sessionGets).toBe(1);
  await harness.getByLabel('一次性配对码').fill('synthetic-fixture-code');
  await harness.getByRole('button', { name: '配对', exact: true }).click();
  await pairStarted;

  const pairResponse = page.waitForResponse(response =>
    response.request().method() === 'POST' &&
    new URL(response.url()).pathname === '/api/owner/v1/pair' &&
    response.status() === 200);
  try {
    await page.evaluate(() => {
      const target = window as typeof window & { sessionProviderTestRoot?: { unmount(): void } };
      target.sessionProviderTestRoot?.unmount();
      delete target.sessionProviderTestRoot;
    });
  } finally {
    releasePair();
  }
  const response = await pairResponse;
  await response.finished();
  await page.evaluate(() => new Promise<void>(resolve =>
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  expect(sessionGets).toBe(1);
});

test('配对失败后 focus 和 visibility 会话检查恢复', async ({ page }) => {
  const db = reviewServer();
  db.authorized = false;
  await mockMarket(page);
  await mockReview(page, db);
  let sessionGets = 0;
  await page.route('**/api/owner/v1/session', async route => {
    if (route.request().method() === 'GET') sessionGets++;
    await route.fallback();
  });
  await page.route('**/api/owner/v1/pair', route => route.fulfill({
    status: 401,
    contentType: 'application/json',
    body: '{"code":"invalid_pairing"}',
  }));
  await page.goto('/');
  await expect(page.getByRole('heading', { name: '私人会话已锁定' })).toBeVisible();
  const sessionGetsBeforePair = sessionGets;
  await page.getByLabel('一次性配对码').fill('expired-code');
  await page.getByRole('button', { name: '配对', exact: true }).click();
  await expect(page.getByRole('alert')).toHaveText('配对码无效或已过期。');

  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await expect.poll(() => sessionGets).toBe(sessionGetsBeforePair + 1);
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
  await expect.poll(() => sessionGets).toBe(sessionGetsBeforePair + 2);
});

test('验证详情延迟和失败属于读取状态，恢复后可提交且保留草稿', async ({ page }) => {
  const db = reviewServer();
  db.notes[0].state = 'confirmed';
  db.revisions[0].state = 'confirmed';
  await mockMarket(page);
  await mockReview(page, db);
  let mode: 'delay' | 'fail' | 'ok' = 'delay';
  let release = () => {};
  const detail = `**/api/owner/v1/notes/${NOTE}/revisions/1`;
  await page.route(detail, async route => {
    if (mode === 'delay') await new Promise<void>(resolve => { release = resolve; });
    if (mode === 'fail') {
      return route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: '{"code":"storage_unavailable","message":"sensitive upstream detail"}',
      });
    }
    return route.fallback();
  });
  await page.goto(`/?view=review&note=${NOTE}&snapshot=${ID}`);
  await page.getByRole('button', { name: '人工验证', exact: true }).click();

  const revision = page.getByLabel('确认修订', { exact: true });
  const submit = page.getByRole('button', { name: '记录验证', exact: true });
  await page.getByLabel('验证依据').fill('读取失败期间保留的验证草稿');
  await expect(page.getByRole('status')).toContainText('正在读取私人记录');
  await expect(submit).toBeDisabled();
  await expect(revision).not.toHaveAttribute('aria-invalid');

  mode = 'fail';
  release();
  const alert = page.getByRole('alert').filter({ hasText: '服务暂不可用' });
  await expect(alert).toBeVisible();
  await expect(alert).not.toContainText('sensitive upstream detail');
  await expect(submit).toBeDisabled();
  await expect(revision).not.toHaveAttribute('aria-invalid');
  await expect(page.getByLabel('验证依据')).toHaveValue('读取失败期间保留的验证草稿');

  mode = 'ok';
  await alert.getByRole('button', { name: '重新读取' }).click();
  await expect(submit).toBeEnabled();
  await page.getByLabel('验证快照日期').fill('2026-09-10');
  await page.getByLabel('验证快照', { exact: true }).selectOption(ID);
  await page.getByLabel('人工结论').selectOption('supported');
  await submit.click();
  await expect(page.getByText('读取失败期间保留的验证草稿', { exact: true })).toBeVisible();
});

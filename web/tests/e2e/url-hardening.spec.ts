import { expect, test, type Page } from '@playwright/test';
import { ID, OLD, detail, envelope, mockMarket, securities, snapshot } from '../fixtures/market';

const date = '2026-09-10';
const prefix = `/api/v1/snapshots/${ID}`;
const collection = { items: securities, total: securities.length, page: 1, page_size: 100, basis: 'raw_close' };

function observe(page: Page) {
  const errors: string[] = [], paths: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {
    const url = new URL(request.url());
    if (url.pathname.startsWith('/api/')) paths.push(url.pathname + url.search);
  });
  return { errors, paths };
}

async function expectContext(page: Page, id = ID) {
  await expect(page.getByLabel('交易日期', { exact: true })).toHaveValue(date);
  expect(new URL(page.url()).searchParams.get('snapshot')?.toLowerCase()).toBe(id);
  expect(new URL(page.url()).searchParams.get('date')).toBe(date);
}

test('real browser fetch normalizes dot securities paths to collection and summary', async ({ page }) => {
  await mockMarket(page);
  await page.route(`**/snapshots/${ID}/securities/`, route => route.fulfill({ json: envelope(collection) }));
  await page.route(`**/snapshots/${ID}/`, route => route.fulfill({ json: snapshot }));
  await page.goto(`/?snapshot=${ID}&date=${date}`);
  const normalized = await page.evaluate(async base => {
    const results = [];
    for (const id of ['.', '..']) {
      const path = `${base}/securities/${encodeURIComponent(id)}`;
      const response = await fetch(path);
      const data = await response.json();
      results.push({
        id, path: new URL(response.url).pathname, status: response.status,
        snapshot: data.snapshot_id, hasHistory: Array.isArray(data.result?.history),
      });
    }
    return results;
  }, prefix);
  expect(normalized).toEqual([
    { id: '.', path: `${prefix}/securities/`, status: 200, snapshot: ID, hasHistory: false },
    { id: '..', path: `${prefix}/`, status: 200, snapshot: ID, hasHistory: false },
  ]);
});

for (const id of ['.', '..', '%2E', '.%2e']) {
  test(`dot detail deep link ${id} never requests a normalized resource or blanks the page`, async ({ page }) => {
    await mockMarket(page);
    const observed = observe(page);
    await page.goto(`/?snapshot=${ID}&date=${date}&view=stock&security=${id}`);
    await expect(page.getByText('未指定证券，请返回研究列表选择。')).toBeVisible();
    await expect(page.getByRole('alert').filter({ hasText: '无效的网址参数' })).toBeVisible();
    await expectContext(page);
    expect(observed.paths.filter(path => path.includes('/securities'))).toEqual([]);
    expect(observed.paths.filter(path => path === `${prefix}/`)).toEqual([]);
    expect(observed.errors).toEqual([]);
  });
}

for (const count of [3, 5]) {
  test(`dot compare IDs do not consume slots alongside ${count} valid selections`, async ({ page }) => {
    await mockMarket(page);
    const observed = observe(page);
    const ids = securities.slice(0, count).map(item => item.security_id);
    await page.goto(`/?snapshot=${ID}&date=${date}&compare=.,%2e%2e,${ids.join(',')}`);
    await expect(page.getByRole('status').filter({ hasText: `已选 ${count} / 5` })).toBeVisible();
    await expect(page.getByRole('alert').filter({ hasText: '无效的网址参数' })).toBeVisible();
    for (const id of ids) await expect(page.getByLabel(`比较 ${id}`, { exact: true })).toBeChecked();
    await expect(page.getByRole('heading', { name: '同区间比较' })).toBeVisible();
    expect(observed.paths.filter(path => path === `${prefix}/` || path === `${prefix}/securities/`)).toEqual([]);
    expect(observed.paths.filter(path => path.includes('/interval?')).every(path => {
      const members = new URL(path, 'http://localhost').searchParams.get('ids')?.split(',');
      return !members || members.every(id => id !== '.' && id !== '..');
    })).toBe(true);
    for (const item of securities.slice(count, 5)) await page.getByLabel(`比较 ${item.security_id}`, { exact: true }).check();
    await expect(page.getByRole('status').filter({ hasText: '已选 5 / 5' })).toBeVisible();
    await expectContext(page);
    expect(observed.errors).toEqual([]);
  });
}

test('security request boundary rejects literal and encoded dot segments before fetch', async ({ page }) => {
  await mockMarket(page);
  await page.goto(`/?snapshot=${ID}&date=${date}`);
  await expect(page.getByRole('table', { name: '证券列表' })).toBeVisible();
  const observed = observe(page);
  const results = await page.evaluate(async ({ base, snapshotId }) => {
    const modulePath = '/src/api.ts';
    const { request } = await import(modulePath);
    const results = [];
    for (const segment of ['.', '..', '%2e', '%2E%2e', '.%2e', '%2e.']) {
      try {
        await request(`${base}/securities/${segment}?probe=boundary`, { snapshot: snapshotId });
        results.push({ segment, status: 200 });
      } catch (error) {
        results.push({ segment, status: (error as { status: number }).status });
      }
    }
    return results;
  }, { base: prefix, snapshotId: ID });
  expect(results.map(result => result.status)).toEqual([400, 400, 400, 400, 400, 400]);
  expect(observed.paths.filter(path => path.includes('probe=boundary'))).toEqual([]);
  expect(observed.errors).toEqual([]);
});

const invalidDetails: Record<string, unknown> = {
  collection,
  summary: snapshot.result,
  'missing history': { ...detail('TEST001'), history: undefined },
  'non-array history': { ...detail('TEST001'), history: {} },
  'null history point': { ...detail('TEST001'), history: [null] },
  'invalid history units': { ...detail('TEST001'), history: [{ ...detail('TEST001').history[0], currency: 123 }] },
  'missing security': { ...detail('TEST001'), security: null },
  'invalid security reasons': { ...detail('TEST001'), security: { ...detail('TEST001').security, missing_reasons: null } },
  'wrong security': detail('TEST002'),
};

for (const [name, result] of Object.entries(invalidDetails)) {
  for (const consumer of ['detail', 'membership'] as const) {
    test(`${consumer} rejects ${name} JSON and retries within the selected snapshot`, async ({ page }) => {
      await mockMarket(page);
      const observed = observe(page);
      let broken = true;
      await page.route('**/securities/TEST001', route => route.fulfill({
        json: envelope(broken ? result : detail('TEST001')),
      }));
      await page.goto(`/?snapshot=${ID}&date=${date}&${consumer === 'detail' ? 'view=stock&security=TEST001' : 'compare=TEST001'}`);
      const retry = page.getByRole('button', { name: consumer === 'detail' ? '重试证券详情' : '重试比较标的 TEST001', exact: true });
      await expect(retry).toBeVisible();
      await expectContext(page);
      if (consumer === 'detail') {
        await expect(page.getByRole('heading', { name: '合成测试证券2', exact: true })).toHaveCount(0);
        await expect(page.locator('.detail-heading')).toHaveCount(0);
      } else {
        await expect(page.getByLabel('比较 TEST001', { exact: true })).toBeChecked();
        await expect(page.getByRole('alert').filter({ hasText: '已移除' })).toHaveCount(0);
      }
      broken = false;
      const recovered = page.waitForResponse(response => response.url().endsWith('/securities/TEST001') && response.ok());
      await retry.click();
      await recovered;
      await expect(retry).toHaveCount(0);
      if (consumer === 'detail') await expect(page.getByRole('heading', { name: '合成测试证券1', exact: true })).toBeVisible();
      await expectContext(page);
      expect(observed.paths.some(path => path.startsWith('/api/v1/snapshots?limit'))).toBe(false);
      expect(observed.errors).toEqual([]);
    });
  }
}

test('drawer rejects a wrong security response and recovers without changing context', async ({ page }) => {
  await mockMarket(page);
  const observed = observe(page);
  let broken = true;
  await page.route('**/securities/TEST001', route => route.fulfill({ json: envelope(detail(broken ? 'TEST002' : 'TEST001')) }));
  await page.goto(`/?snapshot=${ID}&date=${date}`);
  await page.getByRole('button', { name: '合成测试证券1', exact: true }).click();
  const drawer = page.getByRole('dialog');
  const retry = drawer.getByRole('button', { name: '重试证券详情', exact: true });
  await expect(retry).toBeVisible();
  await expect(drawer.locator('.detail-heading')).toHaveCount(0);
  broken = false;
  await retry.click();
  await expect(drawer.getByRole('heading', { name: '合成测试证券1', exact: true })).toBeVisible();
  await expectContext(page);
  expect(observed.errors).toEqual([]);
});

for (const input of ['ABCDEFAB-1234-4ABC-8DEF-ABCDEF123456', 'aBcDeFaB-1234-4aBc-8dEf-AbCdEf123456']) {
  test(`snapshot UUID ${input} is canonicalized while real mismatches stay isolated`, async ({ page }) => {
    const canonical = input.toLowerCase();
    await mockMarket(page);
    const observed = observe(page);
    let mismatch = false;
    await page.route('**/api/v1/**', route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/api/v1/snapshots') return route.fulfill({ json: [{ ...snapshot, snapshot_id: canonical }] });
      if (url.pathname.toLowerCase() === `/api/v1/snapshots/${canonical}`) return route.fulfill({ json: { ...snapshot, snapshot_id: canonical } });
      if (url.pathname.endsWith('/securities/TEST001')) return route.fulfill({
        json: { ...envelope(detail('TEST001')), snapshot_id: mismatch ? OLD : canonical },
      });
      return route.fallback();
    });
    await page.goto(`/?snapshot=${input}&date=${date}&view=stock&security=TEST001`);
    const heading = page.getByRole('heading', { name: '合成测试证券1', exact: true });
    await expect(heading).toBeVisible();
    await expectContext(page, canonical);
    expect(observed.paths).toContain(`/api/v1/snapshots/${canonical}`);
    expect(observed.paths.some(path => path.includes(input))).toBe(false);
    mismatch = true;
    await page.getByRole('button', { name: '刷新快照' }).click();
    await expect(page.getByRole('alert').filter({ hasText: '响应快照与当前选择不匹配' })).toBeVisible();
    await expect(heading).toHaveCount(0);
    mismatch = false;
    await page.getByRole('button', { name: '重试证券详情', exact: true }).click();
    await expect(heading).toBeVisible();
    await expectContext(page, canonical);
    expect(observed.errors).toEqual([]);
  });
}

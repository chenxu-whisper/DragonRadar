import { expect, test, type Page } from '@playwright/test';
import { ID, mockMarket } from '../fixtures/market';

const NOTE = '20000000-0000-4000-8000-000000000001';
const DATE = '2026-09-10';
const SECURITY = 'TEST001';
const CANONICAL_SEARCH = `?view=overview&snapshot=${ID}&date=${DATE}&sort=security_id&order=asc&page=1&page_size=50&n=20&security=${SECURITY}&return_view=overview&role=trend_leader`;

interface HistoryObservation {
  calls: ('push' | 'replace')[];
  initialLength: number;
}

const DEEP_PATH = '/research/deep-link';
const FRAGMENT = '#fragment';

async function observeHistory(page: Page) {
  await page.addInitScript(() => {
    const observation: HistoryObservation = {
      calls: [],
      initialLength: history.length,
    };
    const pushState = history.pushState.bind(history);
    const replaceState = history.replaceState.bind(history);
    history.pushState = (...args) => {
      observation.calls.push('push');
      return pushState(...args);
    };
    history.replaceState = (...args) => {
      observation.calls.push('replace');
      return replaceState(...args);
    };
    Object.defineProperty(window, '__historyObservation', { value: observation });
  });
}

async function historyObservation(page: Page) {
  return page.evaluate(() => {
    const observation = (window as typeof window & {
      __historyObservation: HistoryObservation;
    }).__historyObservation;
    return {
      calls: [...observation.calls],
      initialLength: observation.initialLength,
      currentLength: history.length,
      pathname: location.pathname,
      search: location.search,
      hash: location.hash,
    };
  });
}

for (const [name, market] of [
  ['空快照库', { empty: true }],
  ['快照请求失败', { offline: true }],
] as const) {
  test(`默认构建在${name}时仅规范化一次私人深链并保留只读字段`, async ({ page }) => {
    await observeHistory(page);
    await mockMarket(page, market);

    await page.goto(`${DEEP_PATH}?view=review&note=${NOTE}&snapshot=${ID}&security=${SECURITY}&date=${DATE}${FRAGMENT}`);

    await expect.poll(async () => (await historyObservation(page)).calls).toEqual(['replace']);
    await expect.poll(async () => (await historyObservation(page)).search).toBe(CANONICAL_SEARCH);
    const normalized = await historyObservation(page);
    expect(normalized.currentLength).toBe(normalized.initialLength);
    expect(normalized.pathname).toBe(DEEP_PATH);
    expect(normalized.hash).toBe(FRAGMENT);

    await page.getByRole('link', { name: '区间分析' }).click();
    await expect.poll(async () => (await historyObservation(page)).calls).toEqual(['replace', 'push']);
    const patched = await historyObservation(page);
    expect(patched.pathname).toBe(DEEP_PATH);
    expect(patched.hash).toBe(FRAGMENT);

    await page.waitForTimeout(750);
    const stable = await historyObservation(page);
    expect(stable.calls).toEqual(['replace', 'push']);
    expect(stable.currentLength).toBe(stable.initialLength + 1);
    expect(stable.pathname).toBe(DEEP_PATH);
    expect(stable.hash).toBe(FRAGMENT);
  });
}

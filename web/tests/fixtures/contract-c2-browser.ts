import { chromium, expect, type BrowserContext, type Page } from '@playwright/test';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { protectSetup } from '../../scripts/contract-c2-support.mjs';

declare const chrome: {
  tabs: {
    query(options: Record<string, unknown>): Promise<{ id?: number; url?: string }[]>;
    setZoom(id: number, zoom: number): Promise<void>;
    getZoom(id: number): Promise<number>;
  };
};

export async function ownedBrowser(viewport: { width: number; height: number }, theme: 'light' | 'dark',
  postLaunchProbe?: (context: BrowserContext, profile: string) => Promise<void>) {
  // Linux profiles stay on the disposable native filesystem, not a Windows bind mount.
  const profile = mkdtempSync(resolve(process.platform === 'linux' ? tmpdir()
    : process.env.C2_EVIDENCE_DIR || '/evidence', 'dragon-c2-pair-'));
  const extension = resolve('tests/fixtures/browser-zoom-extension');
  const hostNative = process.env.C2_HOST_NATIVE === '1';
  let context: BrowserContext | undefined;
  return protectSetup(async () => {
    context = await chromium.launchPersistentContext(profile, {
      channel: hostNative ? 'chrome' : 'chromium', headless: false, viewport, locale: 'zh-CN', colorScheme: theme,
      args: hostNative ? [] : [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`],
    });
  if (postLaunchProbe) await postLaunchProbe(context, profile);
  const worker = hostNative ? undefined : context.serviceWorkers()[0] ?? await context.waitForEvent('serviceworker');
  const page = context.pages()[0] ?? await context.newPage();
  await context.addInitScript(value => localStorage.setItem('dr:theme', value), theme);
  let tabId: number | undefined;
  const zoom = async (requested: number) => {
    if (hostNative) {
      if (requested !== 1) throw new Error('Host native evidence cannot prove browser zoom');
      return { requested: 1, actual: 1, tabId: -1, scope: 'host ordinary Chrome native only, no extension API receipt' };
    }
    if (!worker) throw new Error('Extension worker missing');
    const result = await worker.evaluate(async ({ url, requested }) => {
      const tabs = await chrome.tabs.query({});
      const tab = tabs.find(tab => tab.url === url);
      if (!tab?.id) throw new Error('Actual app tab not found');
      await chrome.tabs.setZoom(tab.id, requested);
      return { requested, actual: await chrome.tabs.getZoom(tab.id), tabId: tab.id,
        scope: 'chrome.tabs', appUrl: url };
    }, { url: page.url(), requested });
    tabId = result.tabId;
    expect(result.actual).toBe(requested);
    await expect.poll(() => page.evaluate(({ width, height, requested }) =>
      Math.abs(innerWidth - width / requested) <= 0.51
        && Math.abs(innerHeight - height / requested) <= 0.51,
    { ...viewport, requested })).toBe(true);
    return { ...result, browser: context!.browser()?.version() };
  };
  const cleanup = async () => {
    const failures: unknown[] = [];
    let restored;
    try {
      if (!hostNative && (tabId === undefined || !worker)) throw new Error('Missing app zoom receipt for cleanup');
      restored = hostNative ? 'native-only, no zoom changed' : await worker!.evaluate(async id => {
        await chrome.tabs.setZoom(id, 1);
        return chrome.tabs.getZoom(id);
      }, tabId!);
      if (!hostNative) expect(restored).toBe(1);
    } catch (error) { failures.push(error); }
    let closed = false, profileRemoved = false;
    try { await context!.close(); closed = true; } catch (error) { failures.push(error); }
    try { rmSync(profile, { recursive: true }); profileRemoved = !existsSync(profile); }
    catch (error) { failures.push(error); }
    if (failures.length) throw new AggregateError(failures, 'C2 zoom/profile cleanup failed');
    return { restored, closed, profileRemoved };
  };
  return { context, page, worker, zoom, cleanup, profile };
  }, async () => { await context?.close(); }, () => rmSync(profile, { recursive: true }));
}

export async function hitTest(control: ReturnType<Page['locator']>) {
  await control.scrollIntoViewIfNeeded();
  await expect(control).toBeVisible();
  const proof = await control.evaluate(element => {
    const rect = element.getBoundingClientRect();
    const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
    const describe = (node: Element | null) => node && ({
      tag: node.tagName, id: node.id, class: node.className,
      position: getComputedStyle(node).position, zIndex: getComputedStyle(node).zIndex,
      rect: node.getBoundingClientRect().toJSON(),
    });
    return { unobscured: Boolean(hit && (element === hit || element.contains(hit))),
      target: element.textContent?.slice(0, 100), rect: { top: rect.top, left: rect.left, width: rect.width, height: rect.height },
      hit: hit?.tagName, hitText: hit?.textContent?.slice(0, 100), viewport: { width: innerWidth, height: innerHeight },
      obstruction: [describe(hit), describe(hit?.parentElement ?? null), describe(hit?.parentElement?.parentElement ?? null)],
      scroll: { x: scrollX, y: scrollY }, dialogs: document.querySelectorAll('dialog[open]').length };
  });
  expect(proof, `Hit-tested actual control: ${JSON.stringify(proof)}`).toMatchObject({ unobscured: true });
  return proof;
}

export async function keyboardFocus(page: Page, control: ReturnType<Page['locator']>) {
  await control.scrollIntoViewIfNeeded();
  await control.focus();
  await page.keyboard.press('Tab');
  await page.keyboard.press('Shift+Tab');
  await expect(control).toBeFocused();
  const style = await control.evaluate(element => {
    const css = getComputedStyle(element);
    return { visible: element.matches(':focus-visible'), outline: css.outlineStyle, width: css.outlineWidth,
      shadow: css.boxShadow };
  });
  expect(style.visible).toBe(true);
  expect(style.outline !== 'none' && parseFloat(style.width) > 0 || style.shadow !== 'none').toBe(true);
}

export async function readingMeasurements(page: Page) {
  // Closed details can retain layout metrics while refusing focus; expose their real tables first.
  for (const details of await page.locator('main details').all()) {
    if (!await details.locator('.table-scroll').count() || await details.getAttribute('open') !== null) continue;
    await details.locator(':scope > summary').click();
    await expect(details).toHaveAttribute('open', '');
  }
  const result = await page.evaluate(() => {
    const text = [...document.querySelectorAll<HTMLElement>('main p, main td, main label')]
      .find(element => element.textContent?.trim() && element.getClientRects().length
        && getComputedStyle(element).fontSize !== '0px');
    if (!text) throw new Error('Representative actual body text missing');
    const rgba = (value: string) => (value.match(/[\d.]+/g) || []).map(Number);
    let background = text as HTMLElement | null;
    while (background && (rgba(getComputedStyle(background).backgroundColor)[3] ?? 1) === 0) background = background.parentElement;
    if (!background) throw new Error('Applicable opaque body background missing');
    const luminance = (color: number[]) => {
      const channel = color.slice(0, 3).map(value => value / 255).map(value => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
      return channel[0] * 0.2126 + channel[1] * 0.7152 + channel[2] * 0.0722;
    };
    const color = getComputedStyle(text).color, bg = getComputedStyle(background).backgroundColor;
    const foreground = luminance(rgba(color)), backdrop = luminance(rgba(bg));
    const contrast = (Math.max(foreground, backdrop) + 0.05) / (Math.min(foreground, backdrop) + 0.05);
    return {
      contrast, text: text.textContent?.slice(0, 100), color, background: bg,
      rootWidth: document.documentElement.clientWidth, rootScroll: document.documentElement.scrollWidth,
      overflow: [...document.querySelectorAll<HTMLElement>('main *')].filter(element => {
        const rect = element.getBoundingClientRect();
        return rect.right > document.documentElement.clientWidth + 1
          && !element.closest('.table-scroll');
      }).slice(0, 12).map(element => ({
        tag: element.tagName, class: element.className, text: element.textContent?.slice(0, 80),
        rect: element.getBoundingClientRect().toJSON(),
      })),
      tables: [...document.querySelectorAll<HTMLElement>('.table-scroll')].map(element => ({
        label: element.getAttribute('aria-label'), width: element.clientWidth, scroll: element.scrollWidth,
      })),
    };
  });
  expect(result.contrast).toBeGreaterThanOrEqual(4.5);
  expect(result.rootScroll, `Actual body containment: ${JSON.stringify(result.overflow)}`).toBeLessThanOrEqual(result.rootWidth + 1);
  const scrollProof = [];
  for (const wrapper of await page.locator('.table-scroll').all()) {
    const size = await wrapper.evaluate(element => ({ width: element.clientWidth, scroll: element.scrollWidth }));
    if (size.scroll <= size.width + 1) continue;
    await wrapper.scrollIntoViewIfNeeded();
    await wrapper.focus();
    await expect(wrapper).toBeFocused();
    await page.keyboard.press('ArrowRight');
    const scrollContext = await wrapper.evaluate(element => ({
      label: element.getAttribute('aria-label'), focused: document.activeElement === element,
      rect: element.getBoundingClientRect().toJSON(), width: element.clientWidth, scroll: element.scrollWidth,
      details: element.closest('details')?.open, visible: Boolean(element.getClientRects().length),
      overflow: getComputedStyle(element).overflowX,
    }));
    await expect.poll(() => wrapper.evaluate(element => element.scrollLeft),
      { message: `Actual table keyboard scroll: ${JSON.stringify(scrollContext)}` }).toBeGreaterThan(0);
    scrollProof.push(await wrapper.evaluate(element => ({
      label: element.getAttribute('aria-label'), left: element.scrollLeft, width: element.clientWidth, scroll: element.scrollWidth,
    })));
  }
  return { ...result, scrollProof };
}

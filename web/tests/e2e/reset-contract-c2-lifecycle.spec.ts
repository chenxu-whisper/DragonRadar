import { expect, test, type Page } from '@playwright/test';
import { existsSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { ownedBrowser } from '../fixtures/contract-c2-browser';
import { ID, DATE, ARTICLE_A, ARTICLE_B, OWNER, mockC2, searchData, associationData, taskId, topicTaskId, citations, resolvedCitation } from '../fixtures/contract-c2-scenes';
import { articleA, articleB } from '../fixtures/news';
import { OLD, oldSnapshot } from '../fixtures/market';

async function scene(variant: 'public' | 'local' | 'owner', view: string, work: (page: Page, audit: Awaited<ReturnType<typeof mockC2>>) => Promise<void>) {
  const owned = await ownedBrowser({ width: 768, height: 1024 }, 'light');
  let failure;
  const proof: Record<string, unknown> = { variant, view, outcome: 'failed' };
  try {
    const audit = await mockC2(owned.page);
    proof.traffic = audit.traffic; proof.receipts = audit.receipts; proof.closeTrace = audit.closeTrace;
    const scrollTrace: unknown[] = [];
    if (test.info().title.includes('measured scroll')) {
      owned.page.on('console', message => {
        if (message.text().startsWith('C2_SCROLL ')) scrollTrace.push(JSON.parse(message.text().slice(10)));
      });
      await owned.context.addInitScript(() => {
        let count = 0;
        const record = (event: string) => {
          if (count++ > 100) return;
          console.info('C2_SCROLL ' + JSON.stringify({
            event, url: location.href, y: scrollY, max: document.documentElement.scrollHeight - innerHeight,
            modal: Boolean(document.querySelector('dialog[open]')),
            loading: [...document.querySelectorAll('[role="status"]')].map(element => element.textContent).filter(text => text?.includes('加载')),
          }));
        };
        for (const event of ['pointerdown', 'click', 'scroll']) window.addEventListener(event, () => record(event), true);
        window.addEventListener('DOMContentLoaded', () => {
          new MutationObserver(() => record('mutation')).observe(document.body, { childList: true, subtree: true });
        });
      });
      proof.scrollTrace = scrollTrace;
    }
    const port = { public: 4171, local: 4172, owner: 4173 }[variant];
    await owned.page.goto(`http://127.0.0.1:${port}/?view=${view}&snapshot=${ID}&date=${DATE}&page_size=20`);
    proof.receipt = await owned.zoom(1);
    await work(owned.page, audit);
    audit.assertClean();
    proof.traffic = audit.traffic;
    proof.receipts = audit.receipts;
  } catch (error) { failure = error; proof.error = String(error); }
  finally {
    try { proof.cleanup = await owned.cleanup(); }
    catch (error) { failure = new AggregateError([failure, error].filter(Boolean), 'Lifecycle cleanup failed'); }
    proof.outcome = failure ? 'failed' : 'passed';
    if (failure) proof.error = String(failure);
    writeFileSync(resolve(process.env.C2_EVIDENCE_DIR || '/evidence', `lifecycle-${test.info().title.replace(/[^a-z0-9]+/gi, '-')}.json`), JSON.stringify(proof, null, 2));
  }
  if (failure) throw failure;
}

test('C2 lifecycle postlaunch setup failure closes real context and removes exact owned profile', async () => {
  let closed = false, profile = '';
  let failure;
  try {
    await ownedBrowser({ width: 768, height: 1024 }, 'light', async (context, path) => {
      profile = path;
      context.on('close', () => { closed = true; });
      throw new Error('C2 deliberate postlaunch worker/setup failure');
    });
  } catch (error) { failure = error; }
  expect(String(failure)).toContain('C2 deliberate postlaunch worker/setup failure');
  expect(closed).toBe(true);
  expect(profile).toContain('dragon-c2-pair-');
  expect(existsSync(profile)).toBe(false);
  writeFileSync(resolve(process.env.C2_EVIDENCE_DIR || '/evidence', 'postlaunch-failure-cleanup.json'),
    JSON.stringify({ outcome: 'passed', deliberateSetupFailure: String(failure), profile, closed, profileRemoved: !existsSync(profile) }));
});

test('C2 lifecycle inline article Escape dismisses selected identity and returns actual opener focus', async () => {
  await scene('local', 'news', async page => {
    const opener = page.getByRole('region', { name: '消息列表' }).getByRole('button', { name: /机器人公司签署/ });
    await opener.focus();
    await opener.press('Enter');
    await expect(page.getByRole('region', { name: '消息详情' })).toContainText('公告主体代码 TEST001');
    expect(new URL(page.url()).searchParams.get('article')).toBe(ARTICLE_A);
    await page.keyboard.press('Escape');
    await expect(page.getByRole('region', { name: '消息详情' })).not.toContainText('公告主体代码 TEST001');
    await expect(opener).toBeFocused();
    expect(new URL(page.url()).searchParams.get('article')).toBeNull();
    await opener.press('Enter');
    await expect(page.getByRole('region', { name: '消息详情' })).toContainText('公告主体代码 TEST001');
    await expect(page.getByRole('heading', { name: '机器人公司签署合成测试订单', exact: true })).toHaveCount(1);
    await opener.click();
    await expect(page.getByRole('heading', { name: articleA.title, exact: true })).toHaveCount(1);
    expect(new URL(page.url()).searchParams.get('article')).toBe(ARTICLE_A);
    await page.keyboard.press('Escape');
    await expect(page.getByRole('heading', { name: articleA.title, exact: true })).toHaveCount(0);
    await expect(opener).toBeFocused();
  });
});

test('C2 lifecycle owner association immediate Escape closes inline section and restores trigger', async () => {
  await scene('owner', 'news', async (page, audit) => {
    await page.getByRole('region', { name: '消息列表' }).getByRole('button', { name: /机器人公司签署/ }).click();
    const trigger = page.getByRole('button', { name: '复核消息关联', exact: true });
    await trigger.focus();
    await trigger.press('Enter');
    const review = page.getByRole('region', { name: '所有者关联复核' });
    await expect(review.getByRole('button', { name: '确认关联', exact: true })).toBeEnabled();
    await audit.settled();
    expect(audit.payloads[`/api/news/${ARTICLE_A}/associations/review-state`]).toEqual(associationData());
    await expect(review.getByRole('combobox', { name: '实际关联', exact: true })).toHaveValue(articleA.topics[0].association_id);
    await expect(review).toContainText('关联修订 0');
    await trigger.click();
    await expect(review).toHaveCount(1);
    await expect(review.getByRole('button', { name: '确认关联', exact: true })).toBeEnabled();
    await page.keyboard.press('Escape');
    await expect(page.getByRole('region', { name: '所有者关联复核' })).toHaveCount(0);
    await expect(trigger).toBeFocused();
    await trigger.press('Enter');
    await expect(review.getByRole('button', { name: '确认关联', exact: true })).toBeEnabled();
    await page.keyboard.press('Escape');
    await expect(review).toHaveCount(0);
    await expect(trigger).toBeFocused();
  });
});

test('C2 lifecycle full-detail and cross-view back restore meaningful measured scroll and state', async () => {
  await scene('local', 'overview', async (page, audit) => {
    await page.getByPlaceholder('输入证券代码或名称').fill('合成');
    await page.getByRole('button', { name: '搜索', exact: true }).click();
    await page.getByRole('button', { name: /^上涨 / }).click();
    await page.getByRole('combobox', { name: '身份排序', exact: true }).selectOption('code');
    await page.getByRole('combobox', { name: '排序方向', exact: true }).selectOption('desc');
    await page.getByRole('button', { name: '下一页', exact: true }).click();
    const region = page.getByRole('region', { name: '证券筛选结果' });
    const opener = region.getByRole('button', { name: '合成测试证券23', exact: true });
    await opener.scrollIntoViewIfNeeded();
    const before = await page.evaluate(() => scrollY);
    expect(before).toBeGreaterThan(200);
    const accepted = new URL(page.url()).searchParams;
    const state = () => {
      for (const field of ['snapshot', 'date', 'q', 'market_filter', 'page', 'page_size', 'sort', 'order']) {
        expect(new URL(page.url()).searchParams.get(field), field).toBe(accepted.get(field));
      }
    };
    expect(Object.fromEntries(accepted)).toMatchObject({ q: '合成', market_filter: 'up', page: '2', page_size: '20', sort: 'code', order: 'desc' });
    audit.beginDrawerOpen();
    await opener.click();
    await expect(page.getByRole('dialog', { name: '证券详情 · 只读' })).toContainText('TEST023');
    const firstClose = audit.beginDrawerClose('TEST023', 'initial scroll round-trip Escape');
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await expect(opener).toBeFocused();
    firstClose.asserted('initial TEST023 drawer absent; actual opener focus and scroll round-trip preserved');
    await page.waitForLoadState('networkidle'); await audit.settled();
    expect(Math.abs(await page.evaluate(() => scrollY) - before)).toBeLessThan(8);
    state();
    audit.beginDrawerOpen();
    await opener.click();
    const fullBefore = await page.evaluate(() => scrollY);
    expect(fullBefore).toBeGreaterThan(200);
    await page.waitForLoadState('networkidle'); await audit.settled();
    const pending: import('@playwright/test').Request[] = [];
    let release!: () => void;
    const deferred = new Promise<void>(done => { release = done; });
    const completed: Promise<void>[] = [];
    for (const path of ['/api/news', '/api/stock/TEST023/kline']) {
      const body = audit.payloads[path];
      await page.route(url => url.pathname === path
        && (path !== '/api/news' || url.searchParams.get('security_id') === 'TEST023'), async route => {
        pending.push(route.request());
        const response = deferred.then(() => route.fulfill({ json: body })).catch(() => {});
        completed.push(response);
        await response;
      });
    }
    const closed = audit.beginDrawerClose('TEST023', 'drawer full-detail navigation and return to research list');
    await page.getByRole('link', { name: '打开完整详情', exact: true }).click();
    await expect(page.getByRole('heading', { name: '证券详情', exact: true })).toBeVisible();
    await expect.poll(() => new Set(pending.map(request => new URL(request.url()).pathname)).size).toBe(2);
    const aborted = Promise.all(pending.filter(request => !request.failure()).map(request => page.waitForEvent('requestfailed',
      { predicate: value => value === request && value.failure()?.errorText === 'net::ERR_ABORTED', timeout: 7000 })));
    await page.evaluate(() => scrollTo(0, 0));
    await page.getByRole('link', { name: '返回研究列表', exact: true }).click();
    await expect(region).toContainText('TEST023');
    await aborted;
    closed.asserted('TEST023 full-detail gone; original research list restored before old news/kline release');
    release(); await Promise.all(completed);
    await page.waitForLoadState('networkidle');
    state();
    await expect.poll(async () => Math.abs(await page.evaluate(() => scrollY) - fullBefore)).toBeLessThan(8);
    const crossView = page.getByRole('link', { name: '市场消息', exact: true });
    await crossView.scrollIntoViewIfNeeded();
    const crossBefore = await page.evaluate(() => scrollY);
    expect(crossBefore).toBeGreaterThan(200);
    await crossView.click();
    await expect(page.getByRole('heading', { name: '市场消息', exact: true })).toBeVisible();
    await page.goBack();
    await expect(region).toContainText('TEST023');
    await page.waitForLoadState('networkidle');
    state();
    await expect.poll(async () => Math.abs(await page.evaluate(() => scrollY) - crossBefore)).toBeLessThan(8);
  });
});

for (const variant of ['local', 'owner'] as const) {
  test(`C2 lifecycle ${variant} Reset review is LOCAL and dirty draft cancel then confirm`, async () => {
    await scene(variant, 'review', async page => {
      await expect(page.locator('.local-review')).toBeVisible();
      await expect(page.locator('.review-workspace')).toHaveCount(0);
      const title = page.getByRole('textbox', { name: '标题', exact: true });
      await title.fill('C2 retained dirty draft');
      const leave = page.getByRole('link', { name: '市场总览', exact: true });
      page.once('dialog', dialog => dialog.dismiss());
      await leave.click();
      await expect(title).toHaveValue('C2 retained dirty draft');
      await expect(page).toHaveURL(/view=review/);
      page.once('dialog', dialog => dialog.accept());
      await leave.click();
      await expect(page.getByRole('heading', { name: '市场总览', exact: true })).toBeVisible();
      await expect(title).toHaveCount(0);
    });
  });
}

test('C2 lifecycle closed search discards explicitly released late response', async () => {
  await scene('local', 'overview', async (page, audit) => {
    let release!: () => void, arrived!: () => void;
    const deferred = new Promise<void>(done => { release = done; });
    const requested = new Promise<void>(done => { arrived = done; });
    let receipt: ReturnType<typeof audit.expectCancel>;
    await page.route('**/api/search?**', async route => {
      receipt = audit.expectCancel(route.request(), 'Escape closes pending selected search');
      arrived();
      await deferred;
      await route.fulfill({ json: searchData(new URL(route.request().url()).searchParams) }).catch(() => {});
    });
    const opener = page.getByRole('button', { name: '全局搜索', exact: true });
    await opener.click();
    await page.getByRole('textbox', { name: '搜索发布归档' }).fill('合成');
    await page.getByRole('button', { name: '搜索归档', exact: true }).click();
    await requested;
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog', { name: '全局搜索 · 发布归档' })).toHaveCount(0);
    await expect(opener).toBeFocused();
    release();
    await page.waitForLoadState('networkidle');
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await expect(page).toHaveURL(/view=overview/);
    receipt!.asserted('Escape removed search, opener focused; explicitly released response settled without reopening/redirect');
  });
});

test('C2 local layout-equivalent narrow drawer bounds archived role selector', async () => {
  await scene('local', 'leaders', async page => {
    // Retained local-layout evidence only, not real browser zoom.
    await page.setViewportSize({ width: 188, height: 406 });
    const opener = page.getByRole('table', { name: '角色候选' }).getByRole('button', { name: '合成测试证券1', exact: true });
    await opener.click();
    const drawer = page.getByRole('dialog', { name: '证券详情 · 只读' });
    await expect(drawer.getByRole('combobox', { name: '角色证据题材' })).toBeVisible();
    const bounds = await drawer.getByRole('combobox', { name: '角色证据题材' }).evaluate(element => ({
      right: element.getBoundingClientRect().right, viewport: innerWidth,
      minWidth: getComputedStyle(element).minWidth,
    }));
    expect(bounds.right).toBeLessThanOrEqual(bounds.viewport);
    await page.keyboard.press('Escape');
    await expect(opener).toBeFocused();
  });
});

test('C2 lifecycle index and populated topic repeat uniquely, Escape and topic-stock return focus', async () => {
  await scene('local', 'overview', async (page, audit) => {
    const index = page.getByRole('button', { name: /查看指数详情.*C2 synthetic index/ });
    await index.focus(); await index.press('Enter');
    const indexPanel = page.getByRole('dialog', { name: '指数详情 · 只读' });
    await expect(indexPanel).toContainText('C2 synthetic index · 000300');
    await expect(indexPanel).toContainText('1234');
    await expect(indexPanel).toContainText(ID);
    await expect(indexPanel).toContainText(DATE);
    await index.dispatchEvent('click');
    await expect(indexPanel).toHaveCount(1);
    await expect(page.locator('dialog[open]')).toHaveCount(1);
    await page.keyboard.press('Escape');
    await expect(indexPanel).toHaveCount(0); await expect(index).toBeFocused();
    await index.press('Enter'); await expect(indexPanel).toContainText('1234');
    await page.keyboard.press('Escape'); await expect(indexPanel).toHaveCount(0); await expect(index).toBeFocused();
    expect(audit.traffic.filter(r => r.path.startsWith('/api/stock/'))).toEqual([]);
    const trigger = page.getByRole('link', { name: /C2 synthetic topic/ });
    await trigger.focus(); await trigger.press('Enter');
    const topic = page.getByRole('dialog', { name: '题材详情 · 只读' });
    await expect(topic).toContainText('membership-v1');
    await expect(topic).toContainText('C2-member-evidence');
    await expect(topic).toContainText(ID); await expect(topic).toContainText(DATE);
    await trigger.dispatchEvent('click');
    await expect(topic).toHaveCount(1); await expect(page.locator('dialog[open]')).toHaveCount(1);
    await expect(topic.getByRole('button', { name: /CSV/ })).toHaveCount(0);
    const member = topic.getByRole('button', { name: '合成测试证券1', exact: true });
    await member.click();
    const stock = page.getByRole('dialog', { name: '证券详情 · 只读' });
    await expect(stock).toContainText('TEST001'); await expect(page.locator('dialog[open]')).toHaveCount(1);
    await stock.getByRole('button', { name: '返回题材详情', exact: true }).click();
    await expect(topic).toContainText('C2-member-evidence'); await expect(member).toBeFocused();
    await page.keyboard.press('Escape'); await expect(topic).toHaveCount(0); await expect(trigger).toBeFocused();
    await trigger.press('Enter'); await expect(topic).toContainText('C2-member-evidence');
    await page.keyboard.press('Escape'); await expect(topic).toHaveCount(0); await expect(trigger).toBeFocused();
  });
});

test('C2 lifecycle search and command select real targets, repeat and restore actual openers', async () => {
  await scene('local', 'overview', async (page, audit) => {
    const opener = page.getByRole('button', { name: '全局搜索', exact: true });
    const search = page.getByRole('dialog', { name: '全局搜索 · 发布归档' });
    await opener.focus(); await opener.press('Enter');
    await search.getByRole('textbox', { name: '搜索发布归档' }).fill('合成');
    await search.getByRole('button', { name: '搜索归档', exact: true }).click();
    await expect(search.getByRole('region', { name: '证券搜索结果' })).toContainText('TEST001');
    await audit.settled(); expect(audit.payloads['/api/search'].data.query).toBe('合成');
    await expect(search).toContainText(ID); await expect(search).toContainText(DATE);
    await opener.dispatchEvent('click'); await expect(search).toHaveCount(1);
    await expect(page.locator('dialog[open]')).toHaveCount(1);
    await expect(search.getByRole('button', { name: /CSV/ })).toHaveCount(0);
    await page.keyboard.press('Escape'); await expect(search).toHaveCount(0); await expect(opener).toBeFocused();
    await opener.press('Enter');
    await search.getByRole('textbox', { name: '搜索发布归档' }).fill('合成');
    await search.getByRole('button', { name: '搜索归档', exact: true }).click();
    await search.getByRole('region', { name: '证券搜索结果' }).getByRole('button', { name: '合成测试证券1', exact: true }).click();
    const stock = page.getByRole('dialog', { name: '证券详情 · 只读' });
    await expect(stock).toContainText('TEST001'); await expect(search).toHaveCount(0);
    await page.keyboard.press('Escape'); await expect(stock).toHaveCount(0); await expect(opener).toBeFocused();
    const commandOpener = page.getByRole('button', { name: '打开命令面板（Ctrl+K）', exact: true });
    const command = page.getByRole('dialog', { name: '命令面板', exact: true });
    await commandOpener.focus(); await commandOpener.press('Enter');
    await expect(command.getByRole('combobox')).toBeVisible();
    await commandOpener.dispatchEvent('click'); await expect(command).toHaveCount(1);
    await page.keyboard.press('Escape'); await expect(command).toHaveCount(0); await expect(commandOpener).toBeFocused();
    await commandOpener.press('Enter');
    await command.getByRole('combobox').fill('市场消息');
    await command.getByRole('combobox').press('Enter');
    await expect(page.getByRole('heading', { name: '市场消息', exact: true })).toBeVisible();
    expect(new URL(page.url()).searchParams.get('snapshot')).toBe(ID);
    await expect(command).toHaveCount(0);
    await commandOpener.focus(); await commandOpener.press('Enter');
    await page.keyboard.press('Escape'); await expect(command).toHaveCount(0); await expect(commandOpener).toBeFocused();
  });
});

async function citationPanel(page: Page, label = 'AI 消息摘要', generate = '生成消息摘要') {
  await page.getByRole('region', { name: '消息列表' }).getByRole('button', { name: new RegExp(articleA.title) }).click();
  const panel = page.locator('.ai-task-panel').and(page.getByRole('region', { name: label, exact: true }));
  await panel.getByRole('button', { name: generate, exact: true }).click();
  await expect(panel).toContainText('AI 草稿');
  await panel.getByText('引用与来源', { exact: true }).click();
  return panel;
}
test('C2 lifecycle citations bind resolved fields/PUB and repeat per tool, with Escape focus', async () => {
  await scene('owner', 'news', async (page, audit) => {
    const a = await citationPanel(page);
    const b = await citationPanel(page, 'AI 题材建议', '生成题材建议');
    for (const panel of [a, b]) {
      const trigger = panel.getByRole('button', { name: '查看引用 ev-summary', exact: true });
      await trigger.focus(); await trigger.press('Enter');
      const source = panel.getByRole('region', { name: '引用来源' });
      await expect(source.locator('mark')).toHaveText(articleA.summary!);
      await expect(source).toContainText(ARTICLE_A); await expect(source).toContainText(articleA.revision_id);
      await trigger.click(); await expect(source).toHaveCount(1);
      await expect(source.locator('mark')).toHaveText(articleA.summary!);
    }
    await expect(page.getByRole('region', { name: '引用来源', exact: true })).toHaveCount(2);
    await audit.settled();
    for (const id of [taskId, topicTaskId]) {
      expect(audit.payloads[`/api/ai/task/${id}/evidence/ev-summary`]).toEqual(resolvedCitation('ev-summary', id));
    }
    await page.keyboard.press('Escape');
    await expect(page.getByRole('region', { name: '引用来源', exact: true })).toHaveCount(0);
    // Two independent listeners may restore their own trigger; final listener wins.
    await expect(b.getByRole('button', { name: '查看引用 ev-summary', exact: true })).toBeFocused();
    const trigger = a.getByRole('button', { name: '查看引用 ev-support', exact: true });
    await trigger.click();
    await expect(a.getByRole('region', { name: '引用来源' }).locator('mark')).toHaveText(String(citations[1].value));
    await page.keyboard.press('Escape');
    await expect(a.getByRole('region', { name: '引用来源' })).toHaveCount(0); await expect(trigger).toBeFocused();
  });
});

for (const kind of ['association-source', 'citation-source', 'owner-session'] as const) {
  test(`C2 revocation ${kind} clears already displayed protected targets with exact denial receipt`, async () => {
    await scene('owner', 'news', async (page, audit) => {
      const panel = await citationPanel(page);
      const cite = panel.getByRole('button', { name: '查看引用 ev-summary', exact: true });
      await cite.click(); await expect(panel.getByRole('region', { name: '引用来源' }).locator('mark')).toHaveText(articleA.summary!);
      const association = page.getByRole('button', { name: '复核消息关联', exact: true });
      await association.click();
      await expect(page.getByRole('region', { name: '所有者关联复核' }).getByRole('button', { name: '确认关联', exact: true })).toBeEnabled();
      const path = kind === 'owner-session' ? '/api/owner/v1/session' : kind === 'association-source'
        ? `/api/news/${ARTICLE_A}/associations/review-state` : `/api/ai/task/${taskId}/evidence/ev-summary`;
      let receipt: ReturnType<typeof audit.expectDenial>;
      await page.route(url => url.pathname === path, route => {
        receipt = audit.expectDenial(route.request(), kind === 'owner-session' ? 401 : 403, kind);
        return route.fulfill({ status: kind === 'owner-session' ? 401 : 403,
          json: { code: kind === 'owner-session' ? 'session_unauthorized' : 'source_forbidden' } });
      });
      await (kind === 'association-source' ? association : cite).click();
      await expect(page.getByRole('region', { name: '所有者关联复核' })).toHaveCount(0);
      await expect(page.getByRole('region', { name: '引用来源', exact: true })).toHaveCount(0);
      await expect(page.locator('mark')).toHaveCount(0);
      if (kind !== 'owner-session') {
        await expect(page.getByRole('heading', { name: '来源访问已被拒绝', exact: true })).toBeVisible();
        await expect(page.getByRole('main')).not.toContainText(articleA.summary!);
      } else {
        await expect(panel).toHaveCount(0);
        await expect(page.getByRole('main')).not.toContainText('AI 草稿');
      }
      receipt!.asserted('active association/citation sources and highlights cleared after exact protected denial');
    });
  });
}

test('C2 changed query accepts B then explicitly releases pending A without overwrite or redirect', async () => {
  await scene('local', 'overview', async (page, audit) => {
    let arrived!: () => void, release!: () => void, settled!: () => void;
    const requested = new Promise<void>(r => { arrived = r; }), deferred = new Promise<void>(r => { release = r; });
    const completed = new Promise<void>(r => { settled = r; });
    let receipt: ReturnType<typeof audit.expectCancel>;
    await page.route('**/api/search?**', async route => {
      if (new URL(route.request().url()).searchParams.get('q') !== 'A') return route.fallback();
      receipt = audit.expectCancel(route.request(), 'query B supersedes deferred query A');
      arrived(); await deferred;
      await route.fulfill({ json: searchData(new URL(route.request().url()).searchParams) }).catch(() => {});
      settled();
    });
    await page.getByRole('button', { name: '全局搜索', exact: true }).click();
    const search = page.getByRole('dialog', { name: '全局搜索 · 发布归档' });
    const query = search.getByRole('textbox', { name: '搜索发布归档' });
    await query.fill('A'); await search.getByRole('button', { name: '搜索归档', exact: true }).click(); await requested;
    await query.fill('B'); await search.getByRole('button', { name: '搜索归档', exact: true }).click();
    await expect(search.getByRole('region', { name: '证券搜索结果' })).toContainText('TEST001');
    release(); await completed; await page.waitForLoadState('networkidle'); await audit.settled();
    await expect(query).toHaveValue('B'); await expect(search).toHaveCount(1);
    await expect(search).toContainText('TEST001'); await expect(page).toHaveURL(/view=overview/);
    expect(audit.payloads['/api/search'].data.query).toBe('B');
    receipt!.asserted('B selected and settled before A released; B query/content/PUB remains, no redirect');
    await page.keyboard.press('Escape');
  });
});

for (const kind of ['article', 'association-close', 'citation-close', 'citation-change'] as const) {
  test(`C2 deferred ${kind} releases old reply only after target closed or B accepted`, async () => {
    await scene('owner', 'news', async (page, audit) => {
      let arrived!: () => void, release!: () => void, settled!: () => void;
      const requested = new Promise<void>(r => { arrived = r; }), deferred = new Promise<void>(r => { release = r; });
      const completed = new Promise<void>(r => { settled = r; });
      let panel: ReturnType<Page['locator']> | undefined;
      if (kind !== 'article') panel = await citationPanel(page);
      const path = kind === 'article' ? `/api/news/${ARTICLE_A}` : kind === 'association-close'
        ? `/api/news/${ARTICLE_A}/associations/review-state` : `/api/ai/task/${taskId}/evidence/ev-summary`;
      let receipt: ReturnType<typeof audit.expectCancel>;
      let canceled: Promise<import('@playwright/test').Request>;
      await page.route(url => url.pathname === path, async route => {
        receipt = audit.expectCancel(route.request(), `${kind}: old target canceled`);
        canceled = page.waitForEvent('requestfailed', { predicate: request =>
          request === route.request() && request.failure()?.errorText === 'net::ERR_ABORTED', timeout: 7000 });
        arrived(); await deferred;
        const body = kind === 'article' ? { ...audit.payloads['/api/news'], data: articleA } :
          kind === 'association-close' ? associationData() : resolvedCitation('ev-summary');
        await route.fulfill({ json: body }).catch(() => {}); settled();
      });
      const trigger = kind === 'article' ? page.getByRole('region', { name: '消息列表' }).getByRole('button', { name: new RegExp(articleA.title) })
        : kind === 'association-close' ? page.getByRole('button', { name: '复核消息关联', exact: true })
        : panel!.getByRole('button', { name: '查看引用 ev-summary', exact: true });
      await trigger.click(); await requested;
      if (kind === 'article') {
        await page.getByRole('region', { name: '消息列表' }).getByRole('button', { name: /行业跟踪/ }).click();
        await expect(page.getByRole('region', { name: '消息详情' })).toContainText(articleB.revision_id);
      } else if (kind === 'citation-change') {
        await panel!.getByRole('button', { name: '查看引用 ev-support', exact: true }).click();
        await expect(panel!.getByRole('region', { name: '引用来源' }).locator('mark')).toHaveText(String(citations[1].value));
      } else {
        await page.keyboard.press('Escape'); await expect(trigger).toBeFocused();
      }
      await canceled!;
      release(); await completed; await page.waitForLoadState('networkidle');
      if (kind === 'article') {
        expect(new URL(page.url()).searchParams.get('article')).toBe(ARTICLE_B);
        await expect(page.getByRole('region', { name: '消息详情' })).toContainText(articleB.revision_id);
        await expect(page.getByRole('region', { name: '消息详情' })).not.toContainText(articleA.revision_id);
      } else if (kind === 'citation-change') {
        await expect(panel!.getByRole('region', { name: '引用来源' }).locator('mark')).toHaveText(String(citations[1].value));
        await expect(panel!.getByRole('region', { name: '引用来源' })).toHaveCount(1);
        await page.keyboard.press('Escape');
      } else {
        await expect(page.getByRole('region', { name: kind === 'association-close' ? '所有者关联复核' : '引用来源', exact: true })).toHaveCount(0);
        await expect(trigger).toBeFocused();
      }
      expect(new URL(page.url()).searchParams.get('snapshot')).toBe(ID);
      expect(new URL(page.url()).searchParams.get('date')).toBe(DATE);
      receipt!.asserted('old request explicitly released after closure/B identity accepted; settled target/PUB/URL remains without reopening');
    });
  });
}

for (const kind of ['stock', 'topic', 'publication'] as const) {
  test(`C2 changed ${kind} accepts B before explicitly releasing deferred A`, async () => {
    await scene('local', 'overview', async (page, audit) => {
      const a = kind === 'stock' ? page.getByRole('region', { name: '证券筛选结果' }).getByRole('button', { name: '合成测试证券1', exact: true })
        : page.getByRole('link', { name: /C2 synthetic topic/ });
      let body: unknown;
      const path = kind === 'stock' ? '/api/stock/TEST001' : kind === 'topic' ? '/api/topics/SEC001' : `/api/snapshots/${OLD}`;
      let releaseLhb!: () => void, arrivedLhb!: () => void, settledLhb!: () => void;
      const heldLhb = new Promise<void>(done => { releaseLhb = done; });
      const requestedLhb = new Promise<void>(done => { arrivedLhb = done; });
      const completedLhb = new Promise<void>(done => { settledLhb = done; });
      let canceledLhb: Promise<import('@playwright/test').Request>;
      if (kind === 'stock') {
        await page.route(url => url.pathname === '/api/lhb' && url.searchParams.size === 5
          && url.searchParams.get('date') === DATE && url.searchParams.get('security_id') === 'TEST001'
          && url.searchParams.get('page') === '1' && url.searchParams.get('size') === '20'
          && url.searchParams.get('as_of') === '2026-09-10T08:01:00Z', async route => {
          canceledLhb = page.waitForEvent('requestfailed', { predicate: request =>
            request === route.request() && request.failure()?.errorText === 'net::ERR_ABORTED', timeout: 7000 });
          arrivedLhb(); await heldLhb;
          await route.fallback().catch(() => {});
          settledLhb();
        }, { times: 1 });
      }
      if (kind !== 'publication') {
        if (kind === 'stock') audit.beginDrawerOpen();
        await a.click();
        await expect(page.getByRole('dialog')).toContainText(kind === 'stock' ? 'TEST001' : 'C2-member-evidence');
        if (kind === 'stock') await requestedLhb;
        else await page.waitForLoadState('networkidle');
        await audit.settled();
        body = audit.payloads[path];
        const initialClose = kind === 'stock' ? audit.beginDrawerClose('TEST001', 'initial A drawer Escape') : undefined;
        await page.keyboard.press('Escape');
        await expect(page.getByRole('dialog')).toHaveCount(0);
        await expect(a).toBeFocused();
        initialClose?.asserted('initial A drawer absent and actual A opener focused');
        if (kind === 'stock') {
          await canceledLhb!; releaseLhb(); await completedLhb;
          await page.waitForLoadState('networkidle');
        }
      } else body = oldSnapshot;
      let arrived!: () => void, release!: () => void, settled!: () => void;
      const requested = new Promise<void>(r => { arrived = r; }), deferred = new Promise<void>(r => { release = r; });
      const completed = new Promise<void>(r => { settled = r; });
      let receipt: ReturnType<typeof audit.expectCancel>;
      await page.route(url => url.pathname === path, async route => {
        receipt = audit.expectCancel(route.request(), `${kind} B supersedes exact pending A`);
        arrived(); await deferred;
        await route.fulfill({ json: body }).catch(() => {}); settled();
      });
      if (kind === 'publication') {
        await page.getByLabel('交易日期', { exact: true }).fill('2026-09-09'); await requested;
        await page.getByLabel('交易日期', { exact: true }).fill(DATE);
        await expect(page.getByRole('region', { name: '证券筛选结果' })).toContainText('TEST001');
        await expect(page).toHaveURL(new RegExp(`snapshot=${ID}`));
      } else {
        if (kind === 'stock') audit.beginDrawerOpen();
        await a.click(); await requested;
        const close = kind === 'stock' ? audit.beginDrawerClose('TEST001', 'changed-stock A Escape before B') : undefined;
        await page.keyboard.press('Escape');
        await expect(a).toBeFocused();
        close?.asserted('A drawer closed and A actual opener focused before accepting B');
        const b = kind === 'stock' ? page.getByRole('region', { name: '证券筛选结果' }).getByRole('button', { name: '合成测试证券2', exact: true })
          : page.getByRole('link', { name: /C2 second topic/ });
        if (kind === 'stock') audit.beginDrawerOpen();
        await b.click();
        await expect(page.getByRole('dialog')).toContainText(kind === 'stock' ? 'TEST002' : 'C2 second topic');
      }
      release(); await completed; await page.waitForLoadState('networkidle'); await audit.settled();
      expect(new URL(page.url()).searchParams.get('snapshot')).toBe(ID);
      expect(new URL(page.url()).searchParams.get('date')).toBe(DATE);
      if (kind === 'publication') {
        await expect(page.getByLabel('交易日期', { exact: true })).toHaveValue(DATE);
        await expect(page.getByRole('dialog')).toHaveCount(0);
        await expect(page.getByRole('region', { name: '证券筛选结果' })).toContainText('TEST001');
      } else {
        await expect(page.getByRole('dialog')).toHaveCount(1);
        await expect(page.getByRole('dialog')).toContainText(kind === 'stock' ? 'TEST002' : 'C2 second topic');
        await expect(page.getByRole('dialog')).not.toContainText(kind === 'stock' ? 'TEST001' : 'C2 synthetic topic');
        const finalClose = kind === 'stock' ? audit.beginDrawerClose('TEST002', 'accepted B final Escape') : undefined;
        await page.keyboard.press('Escape');
        await expect(page.getByRole('dialog')).toHaveCount(0);
        await expect(kind === 'stock'
          ? page.getByRole('region', { name: '证券筛选结果' }).getByRole('button', { name: '合成测试证券2', exact: true })
          : page.getByRole('link', { name: /C2 second topic/ })).toBeFocused();
        finalClose?.asserted('accepted B drawer absent and actual B opener focused');
      }
      receipt!.asserted('B actual target/PUB accepted before explicit A release; settled B content/URL remains without old reopening');
    });
  });
}

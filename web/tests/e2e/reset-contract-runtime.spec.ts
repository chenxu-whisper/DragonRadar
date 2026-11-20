import { createHash } from 'node:crypto';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { expect, test, type Request } from '@playwright/test';
import { isContractLhbDenial, publishContractCheckpoint } from '../../scripts/reset-contract-runtime-support.mjs';

const channel = '/fixture';
const read = (name: string) => JSON.parse(readFileSync(`${channel}/${name}`, 'utf8'));
const sha256 = (value: Buffer) => createHash('sha256').update(value).digest('hex');
async function checkpoint(name: string, request: unknown = {}) {
  publishContractCheckpoint(`${channel}/${name}.request.json`, JSON.stringify(request));
  await expect.poll(() => existsSync(`${channel}/${name}.response.json`), { timeout: 180_000 }).toBe(true);
  return read(`${name}.response.json`);
}

test('C1 real archived targets, owner audit, citation and fail-closed revocation', async ({ page, context, browser }) => {
  const initial = read('initial.json');
  const owner = initial.mode === 'owner';
  const origin: string = initial.origin;
  const p1 = initial.publication;
  const evidence: Record<string, unknown> = { mode: initial.mode, origin, chromium: browser.version(), assets: [], api: [], visible: [] };
  const assets: { url: string; status: number; contentType: string; sha256: string }[] = [];
  let transition = 'startup';
  const knownPublications = [p1];
  const pendingRequests = new Set<Request>();
  const cancellationActions = new Map<Request, string>();
  const httpErrors: { url: string; method: string; status: number; code?: string; body?: string; transition: string }[] = [];
  const consoleErrors: { url: string; text: string }[] = [];
  const failedRequests: { url: string; error: string; method: string; transition?: string }[] = [];
  const pageErrors: string[] = [];
  const captureErrors: { url: string; error: string; diagnostic?: unknown }[] = [];
  const captures: Promise<void>[] = [];
  page.on('request', request => {
    if (new URL(request.url()).origin === origin) pendingRequests.add(request);
  });
  page.on('requestfinished', request => pendingRequests.delete(request));
  function markPendingCancellation(action: string) {
    for (const request of pendingRequests) {
      const url = new URL(request.url());
      const archivedRead = knownPublications.some(publication =>
        url.searchParams.get('snapshot_id') === publication.receipt.snapshot_id
        && url.searchParams.get('date') === '2026-09-08'
        && ['/api/news', '/api/news/wordcloud', '/api/search',
          `/api/news/${publication.archive.article_id}`,
          `/api/news/${publication.archive.article_id}/associations/review-state`,
          `/api/topics/${publication.archive.sector_id}`,
          `/api/stock/${publication.archive.security_id}`].includes(url.pathname));
      if (request.method() === 'GET' && (archivedRead || url.pathname === '/api/owner/v1/session')) {
        cancellationActions.set(request, action);
      }
    }
  }
  page.on('response', response => {
    const url = new URL(response.url());
    if (url.origin !== origin) return;
    if (response.status() >= 400) {
      const item = { url: response.url(), method: response.request().method(), status: response.status(), code: undefined as string | undefined,
        body: undefined as string | undefined, transition };
      httpErrors.push(item);
      captures.push(response.text().then(raw => {
        item.body = raw;
        if (raw) item.code = JSON.parse(raw).code;
      }).catch(error => {
        captureErrors.push({ url: response.url(), error: error.message });
      }));
    } else if (url.pathname.startsWith('/assets/') && /\.(?:js|css)$/.test(url.pathname)) {
      captures.push(response.body().then(bytes => {
        assets.push({ url: response.url(), status: response.status(),
          contentType: response.headers()['content-type'], sha256: sha256(bytes) });
      }).catch(error => {
        captureErrors.push({ url: response.url(), error: error.message, diagnostic: {
          pageUrl: page.url(), status: response.status(), headers: response.headers(),
          resourceType: response.request().resourceType(),
          redirectedFrom: response.request().redirectedFrom()?.url() ?? null,
          fromServiceWorker: response.fromServiceWorker(), timing: response.request().timing(),
          requestFailure: response.request().failure(),
        } });
      }));
    }
  });
  page.on('console', message => {
    if (message.type() === 'error') consoleErrors.push({ url: message.location().url, text: message.text() });
  });
  page.on('pageerror', error => pageErrors.push(error.message));
  page.on('requestfailed', request => {
    failedRequests.push({ url: request.url(), method: request.method(),
      error: request.failure()?.errorText ?? 'unknown', transition: cancellationActions.get(request) });
    pendingRequests.delete(request);
  });

  async function flushResponses() {
    // Include late dynamic chunks before navigation invalidates Chromium body handles.
    await page.waitForLoadState('networkidle', { timeout: 15_000 });
    let captured = 0;
    while (captured < captures.length) {
      const end = captures.length;
      await Promise.all(captures.slice(captured, end));
      captured = end;
    }
    expect(captureErrors, 'Response evidence must be captured before navigation').toEqual([]);
  }
  async function api(path: string, status = 200, method = 'GET', data?: unknown) {
    const response = await context.request.fetch(`${origin}${path}`, {
      method, data, headers: owner && method !== 'GET' ? { Origin: origin } : undefined,
    });
    expect(response.status(), path).toBe(status);
    const raw = await response.text();
    const edgeDenied = !owner && (status === 403 || status === 404);
    if (edgeDenied) expect(raw, 'Public edge denial body').toBe('');
    const body = edgeDenied ? {} : JSON.parse(raw);
    (evidence.api as unknown[]).push({ path, method, status, body });
    return body;
  }
  function bound(path: string, publication = p1) {
    return `${path}${path.includes('?') ? '&' : '?'}date=2026-09-08&snapshot_id=${publication.receipt.snapshot_id}`;
  }
  async function news(publication = p1, article = publication.archive.article_id) {
    await flushResponses();
    markPendingCancellation('archived-news-navigation');
    await page.goto(`${origin}/?view=news&snapshot=${publication.receipt.snapshot_id}&date=2026-09-08&article=${article}`,
      { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('region', { name: '消息详情', exact: true })).toContainText('Synthetic C1 archived article');
    expect(new URL(page.url()).searchParams.get('snapshot')).toBe(publication.receipt.snapshot_id);
    expect(new URL(page.url()).searchParams.get('date')).toBe('2026-09-08');
    await flushResponses();
  }
  async function search() {
    await page.getByRole('button', { name: '全局搜索', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '全局搜索 · 发布归档' });
    await expect(dialog).toBeVisible();
    await dialog.getByLabel('搜索发布归档').fill('Synthetic');
    await dialog.getByRole('button', { name: '搜索归档', exact: true }).click();
    await expect(dialog.getByRole('region', { name: '证券搜索结果' }).getByRole('button')).toBeVisible();
    return dialog;
  }
  async function visibleTargets(publication = p1) {
    const receipt = publication.receipt;
    const archive = publication.archive;
    const selection = `交易日 2026-09-08 · 发布快照 ${receipt.snapshot_id}`;
    const data = await api(bound('/api/search?q=Synthetic&page=1&size=50', publication));
    expect(data.snapshot_id).toBe(receipt.snapshot_id);
    expect(data.trade_date).toBe('2026-09-08');
    expect(data.data.groups.securities.map((item: { security_id: string }) => item.security_id)).toContain(archive.security_id);
    expect(data.data.groups.topics.map((item: { sector_id: string }) => item.sector_id)).toContain(archive.sector_id);
    expect(data.data.groups.messages.map((item: { article_id: string }) => item.article_id)).toContain(archive.article_id);
    for (const group of ['securities', 'topics', 'messages']) expect(data.data.group_status[group].status).toBe('ready');
    await news(publication);
    let dialog = await search();
    await dialog.getByRole('region', { name: '题材搜索结果' }).getByRole('button', { name: `Synthetic C1 sector ${archive.security_id.endsWith('2') ? 2 : 1}`, exact: true }).click();
    const topic = page.locator('dialog.research-drawer');
    await expect(topic).toBeVisible();
    await expect(topic).toContainText(selection);
    await expect(topic.locator(`[data-member="${archive.security_id}"]`)).toBeVisible();
    await expect(topic).toContainText(archive.source_version);
    const topicData = await api(bound(`/api/topics/${encodeURIComponent(archive.sector_id)}?page=1&size=50`, publication));
    expect(topicData.snapshot_id).toBe(receipt.snapshot_id);
    expect(topicData.data.members.map((member: { security_id: string }) => member.security_id)).toEqual(archive.members);
    markPendingCancellation('archived-stock-navigation');
    await topic.locator(`[data-member="${archive.security_id}"]`).click();
    const stock = page.getByRole('dialog', { name: '证券详情 · 只读' });
    await expect(stock).toBeVisible();
    await expect(stock).toContainText(archive.security_id);
    await expect(stock).toContainText(`2026-09-08 · 发布 ${receipt.snapshot_id}`);
    const stockData = await api(bound(`/api/stock/${archive.security_id}`, publication));
    expect(stockData.snapshot_id).toBe(receipt.snapshot_id);
    markPendingCancellation('stock-dialog-close');
    await stock.getByRole('button', { name: '关闭详情' }).click();
    dialog = await search();
    markPendingCancellation('archived-article-navigation');
    await dialog.getByRole('region', { name: '消息搜索结果' }).getByRole('button', { name: `Synthetic C1 article ${archive.security_id.endsWith('2') ? 2 : 1}`, exact: true }).click();
    await expect(page.getByRole('region', { name: '消息详情', exact: true })).toContainText(`Synthetic C1 article ${archive.security_id.endsWith('2') ? 2 : 1}`);
    expect(new URL(page.url()).searchParams.get('article')).toBe(archive.article_id);
    expect(new URL(page.url()).searchParams.get('snapshot')).toBe(receipt.snapshot_id);
    (evidence.visible as unknown[]).push({ publication: receipt.snapshot_id, sector: archive.sector_id,
      members: archive.members, security: archive.security_id, article: archive.article_id, selectedDate: '2026-09-08' });
  }

  try {
  const home = await page.goto(origin, { waitUntil: 'domcontentloaded' });
  expect(home?.status()).toBe(200);
  expect(home?.headers()['content-type']).toMatch(/text\/html/);
  evidence.homepage = { status: home!.status(), contentType: home!.headers()['content-type'], sha256: sha256(await home!.body()) };
  if (owner) {
    await expect(page.getByRole('heading', { name: '私人会话已锁定' })).toBeVisible();
    expect((await api('/api/owner/v1/session', 401)).code).toBe('session_unauthorized');
    const pairing = readFileSync(`${channel}/pair.txt`, 'utf8').trim();
    await page.getByLabel('一次性配对码', { exact: true }).fill(pairing);
    await page.getByRole('button', { name: '配对', exact: true }).click();
    await expect(page.getByRole('heading', { name: '私人会话已锁定' })).toHaveCount(0);
    const session = await api('/api/owner/v1/session');
    expect(session.owner_id).toMatch(/^[a-f0-9-]{36}$/);
    expect(session.session_id).toMatch(/^[a-f0-9-]{36}$/);
    // Only public identity fields, never the CSRF value, enter retained evidence.
    (evidence.api as unknown[]).pop();
    evidence.ownerIdentity = { owner_id: session.owner_id, session_id: session.session_id, origin };
    writeFileSync(`${channel}/owner-tls-paired.json`, JSON.stringify({
      origin, chromium: browser.version(), homepage: evidence.homepage, ownerIdentity: evidence.ownerIdentity,
    }));
    await checkpoint('paired');
    transition = 'paired';
    const forbidden = await api(`/api/news/${p1.archive.article_id}/associations/review`, 403, 'POST', {
      association_id: initial.association, decision: 'accept', expected_revision: 0, idempotency_key: 'c1-missing-csrf',
    });
    expect(forbidden).toEqual({ code: 'FORBIDDEN', message: 'Access denied' });
    const cookieNames = (await context.cookies(origin)).map(cookie => cookie.name);
    expect(cookieNames).toContain('__Host-dragon-private-session');
    expect(cookieNames).toContain('__Host-dragon-private-csrf');
    evidence.missingCsrf = { origin, method: 'POST', json: true, csrfHeaderPresent: false,
      pairedSessionVerified: true, sessionCookiePresent: true, csrfCookiePresent: true,
      status: 403, body: forbidden };
    writeFileSync(`${channel}/owner-missing-csrf.json`, JSON.stringify(evidence.missingCsrf));
  } else {
    await api('/api/snapshots/list', 403, 'POST', {});
    for (const path of ['/api/owner/v1/session', '/api/ai/task/00000000-0000-4000-8000-000000000099', '/api/analysis/jobs']) {
      await api(path, 404);
    }
    await expect(page.locator('.pairing, .ai-task-panel')).toHaveCount(0);
  }
  transition = 'archive-reads';
  await visibleTargets();

  if (owner) {
    await page.getByRole('button', { name: '复核消息关联', exact: true }).click();
    const review = page.getByRole('region', { name: '所有者关联复核', exact: true });
    await expect(review).toContainText('复核状态：unreviewed');
    const reviewResponse = page.waitForResponse(response =>
      new URL(response.url()).pathname === `/api/news/${p1.archive.article_id}/associations/review` && response.request().method() === 'POST');
    await review.getByRole('button', { name: '确认关联', exact: true }).click();
    const receipt = await (await reviewResponse).json();
    expect(receipt.audit_id).toMatch(/^[a-f0-9-]{36}$/);
    expect(receipt.association_id).toBe(initial.association);
    await expect(review).toContainText('审核已记录');
    evidence.reviewReceipt = receipt;
    const revision = await checkpoint('reviewed', { audit_id: receipt.audit_id });
    knownPublications.push(revision.publication);
    expect(new URL(page.url()).searchParams.get('snapshot')).toBe(p1.receipt.snapshot_id);
    const original = await api(bound(`/api/news/${p1.archive.article_id}`));
    expect(original.data.topics.find((item: { association_id: string }) => item.association_id === initial.association).status).toBe('unreviewed');
    await review.getByRole('button', { name: '关闭关联复核' }).click();
    await visibleTargets(revision.publication);
    const revised = await api(bound(`/api/news/${p1.archive.article_id}`, revision.publication));
    const link = revised.data.topics.find((item: { association_id: string }) => item.association_id === initial.association);
    expect(link.status).toBe('accepted');
    expect(link.effective).toBe(true);
    await news(p1);
    const ai = page.getByRole('region', { name: 'AI 消息摘要', exact: true });
    const submittedResponse = page.waitForResponse(response => new URL(response.url()).pathname === '/api/ai/summarize'
      && response.request().method() === 'POST');
    await ai.getByRole('button', { name: '生成消息摘要', exact: true }).click();
    const submitted = await (await submittedResponse).json();
    const completed = await checkpoint('task-submitted', { task_id: submitted.task_id });
    await ai.locator('summary').filter({ hasText: '引用与来源' }).click();
    await ai.getByRole('button', { name: `查看引用 ${completed.citation.evidence_id}`, exact: true }).click();
    const source = ai.getByRole('region', { name: '引用来源', exact: true });
    await expect(source).toBeVisible();
    await expect(source.locator('pre mark')).toHaveText(completed.citation.value);
    const resolved = await api(`/api/ai/task/${completed.task_id}/evidence/${encodeURIComponent(completed.citation.evidence_id)}?revision=${completed.draft_revision}&snapshot_id=${p1.receipt.snapshot_id}`);
    expect(resolved.snapshot_id).toBe(p1.receipt.snapshot_id);
    expect(resolved.status).toBe('ready');
    await checkpoint('citation-opened', { resolved });
    await source.getByRole('button', { name: '关闭引用来源' }).click();
    await ai.getByRole('button', { name: `查看引用 ${completed.citation.evidence_id}`, exact: true }).click();
    await expect(source.locator('pre mark')).toHaveText(completed.citation.value);
    await page.getByRole('button', { name: '复核消息关联', exact: true }).click();
    await expect(page.getByRole('region', { name: '所有者关联复核' })).toBeVisible();
    await expect(source.locator('pre mark')).toHaveText(completed.citation.value);
    const dialog = await search();
    markPendingCancellation('source-revocation');
    transition = 'source-revoked';
    await checkpoint('source-revoke');
    await dialog.getByRole('button', { name: '搜索归档', exact: true }).click();
    await expect(page.getByText('来源访问已被拒绝', { exact: false })).toBeVisible();
    await expect(page.locator('.global-search-results button, [data-member], .news-detail pre, .ai-draft, [aria-label="所有者关联复核"], [aria-label="引用来源"]')).toHaveCount(0);
    expect(await api(bound('/api/search?q=Synthetic&page=1&size=50'), 403))
      .toEqual({ code: 'source_forbidden', message: 'Source access denied' });
    markPendingCancellation('owner-revocation');
    transition = 'owner-revoked';
    await checkpoint('owner-revoke');
    await page.getByRole('button', { name: '检查会话', exact: true }).click();
    await expect(page.getByRole('heading', { name: '私人会话已锁定' })).toBeVisible();
    expect((await api('/api/owner/v1/session', 401)).code).toBe('session_unauthorized');
    expect((await api(`/api/ai/task/${completed.task_id}`, 403)).code).toBe('FORBIDDEN');
    await expect(page.locator('.ai-task-panel, .news-detail, dialog[open]')).toHaveCount(0);
    evidence.revocation = { sourceCleared: true, ownerCleared: true };
  }
  await flushResponses();
  writeFileSync(`${channel}/${initial.mode}-network-diagnostic.json`, JSON.stringify({
    assets, httpErrors, consoleErrors, failedRequests, pageErrors, visible: evidence.visible,
  }));
  expect(assets.filter(item => item.url.endsWith('.js')).length).toBeGreaterThan(0);
  expect(assets.filter(item => item.url.endsWith('.css')).length).toBeGreaterThan(0);
  for (const asset of assets) {
    expect(asset.status).toBe(200);
    expect(asset.contentType).toMatch(asset.url.endsWith('.js') ? /(?:javascript|ecmascript)/ : /text\/css/);
    expect(asset.sha256).toMatch(/^[a-f0-9]{64}$/);
  }
  const unexpectedHttpErrors = httpErrors.filter(error => {
    const path = new URL(error.url).pathname;
    return !((owner && ['startup', 'owner-revoked'].includes(error.transition)
        && path === '/api/owner/v1/session' && error.method === 'GET'
        && error.status === 401 && error.code === 'session_unauthorized')
      || ((!owner || initial.ownerCaddyProof?.denyLhb) && isContractLhbDenial(error, knownPublications))
      || (owner && error.transition === 'source-revoked'
        && path === '/api/search' && error.method === 'GET' && error.status === 403
        && error.body !== undefined
        && JSON.stringify(JSON.parse(error.body)) === JSON.stringify({ code: 'source_forbidden', message: 'Source access denied' })
        && new URL(error.url).searchParams.get('snapshot_id') === p1.receipt.snapshot_id
        && new URL(error.url).searchParams.get('date') === '2026-09-08'));
  });
  expect(unexpectedHttpErrors, `Unexpected HTTP errors ${JSON.stringify(unexpectedHttpErrors)}`).toEqual([]);
  for (const error of consoleErrors) {
    expect(error.text).toMatch(/^Failed to load resource: the server responded with a status of (401|403|404)/);
    const status = Number(error.text.match(/status of (\d{3})/)?.[1]);
    expect(httpErrors.some(response => response.url === error.url && response.status === status)).toBe(true);
  }
  for (const failure of failedRequests) {
    expect(failure.error).toBe('net::ERR_ABORTED');
    expect(failure.method).toBe('GET');
    expect(failure.transition, `Unclassified canceled request ${failure.url}`).toBeTruthy();
  }
  expect(pageErrors).toEqual([]);
  evidence.assets = assets;
  evidence.network = { intentionalHttpErrors: httpErrors, intentionalConsoleErrors: consoleErrors,
    navigationCanceledRequests: failedRequests, unexpectedPageErrors: pageErrors };
  writeFileSync(`${channel}/${initial.mode}-evidence.json`, JSON.stringify(evidence));
  } finally {
    let captured = 0;
    while (captured < captures.length) {
      const end = captures.length;
      await Promise.all(captures.slice(captured, end));
      captured = end;
    }
    writeFileSync(`${channel}/${initial.mode}-network-diagnostic.json`, JSON.stringify({
      assets, httpErrors, consoleErrors, failedRequests, pageErrors, captureErrors,
      visible: evidence.visible, transition,
    }));
  }
});

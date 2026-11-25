import { expect, type Page } from '@playwright/test';
import { ID, OLD, mockLeaders, resetSummary, securities, sessions, snapshot } from './market';
import { articleA, articleB, listArticleA, listArticleB, ARTICLE_A, ARTICLE_B } from './news';
import { mockRegulation } from './regulation';
import { createTrafficAudit } from '../../scripts/contract-c2-support.mjs';
import { rankingForRequest } from './ranking-direction';

export { ID, ARTICLE_A, ARTICLE_B };
export const DATE = '2026-09-10';
export const OWNER = '00000000-0000-4000-8000-000000000201';
export const SESSION = '00000000-0000-4000-8000-000000000202';
export const candidates = securities.slice(0, 25).map((s, i) => ({
  security_id: s.security_id, name: s.name, rank: i + 1, evidence_status: 'candidate', sector_id: 'SEC001', boards: 2,
}));
export const articles = [listArticleA, listArticleB, ...Array.from({ length: 24 }, (_, i) => ({
  ...listArticleA, article_id: `00000000-0000-4000-8000-${String(300 + i).padStart(12, '0')}`,
  title: `C2 announcement ${i + 1}`,
}))].map((item, index) => ({
  ...item,
  received_at: index === 1 ? articleB.received_at : articleA.received_at,
  revision_id: index === 0 ? articleA.revision_id : index === 1 ? articleB.revision_id
    : `00000000-0000-4000-8000-${String(500 + index).padStart(12, '0')}`,
}));
const coverage = { expected: 65, valid: 63, excluded: 1, missing: 1 };
const env = (data: unknown) => ({ ...resetSummary(snapshot), data });
const absent = () => ({ ...env(null), quality_flags: ['materialization_unavailable'] });
const sector = {
  sector_id: 'SEC001', name: 'C2 synthetic topic', member_count: 1, valid_returns: 1,
  median_return: 0.024, change_ratio: 0.024, limit_up_count: 1,
  up_ratio: { numerator: 1, denominator: 1, value: 1, reason: null },
  limit_up_ratio: { numerator: 1, denominator: 1, value: 1, reason: null },
  net_inflow: null, net_inflow_reason: 'source_field_unavailable', quality_flags: [],
  coverage: { expected: 1, valid: 1, excluded: 0, missing: 0 },
};
export function topicData(query: URLSearchParams, sectorId = 'SEC001') {
  return env({
    sector: sectorId === 'SEC001' ? sector : { ...sector, sector_id: sectorId, name: 'C2 second topic' },
    topic_basis: { source: 'synthetic-topic', source_version: 'c2-v1', as_of: snapshot.as_of,
      source_time: null, cutoff: snapshot.as_of, market_snapshot_id: ID, topic_snapshot_id: OLD,
      taxonomy_version: 'taxonomy-v1', membership_version: 'membership-v1' },
    definition: { effective_from: DATE, effective_to: null, available_at: snapshot.as_of, source_reference: 'C2-topic-evidence' },
    membership_coverage: { expected: 1, received: 1, known: 1, status: 'complete', reason: null, complete_as_of: snapshot.as_of },
    members: [{ security_id: 'TEST001', code: 'TEST001', name: '合成测试证券1',
      effective_from: DATE, effective_to: null, available_at: snapshot.as_of, evidence_refs: ['C2-member-evidence'] }],
    total: 1, page: Number(query.get('page') || 1), size: Number(query.get('size') || 50),
  });
}
export function searchData(query: URLSearchParams) {
  return env({
    query: query.get('q'), page: Number(query.get('page') || 1), size: Number(query.get('size') || 50),
    total: 3,
    groups: {
      securities: [{ security_id: 'TEST001', code: 'TEST001', name: '合成测试证券1', exchange: 'SSE', match_field: 'name' }],
      topics: [{ sector_id: 'SEC001', name: sector.name, match_field: 'name' }],
      messages: [{ ...listArticleA, match_field: 'title' }],
    },
    group_status: Object.fromEntries(['securities', 'topics', 'messages'].map(group =>
      [group, { status: 'ready', total: 1, reason: null }])),
  });
}
export function associationData() {
  return {
    owner_id: OWNER, article_id: ARTICLE_A, article_revision_id: articleA.revision_id,
    article_revision: articleA.revision, snapshot_id: ID, trade_date: DATE,
    associations: [...articleA.topics, ...articleA.securities].map(item => ({
      association_id: item.association_id, revision: 0, status: 'unreviewed', decision: null, own_audit: [],
    })),
  };
}
export const taskId = '00000000-0000-4000-8000-000000000195';
export const topicTaskId = '00000000-0000-4000-8000-000000000196';
export const citations = [
  { evidence_id: 'ev-summary', evidence_position: '/input/article/summary',
    source_identity: `news:${articleA.source}:${articleA.source_version}:${articleA.revision_id}`,
    available_at: articleA.available_at, value: articleA.summary },
  { evidence_id: 'ev-support', evidence_position: '/input/article/supporting_evidence/0',
    source_identity: `news:${articleA.source}:${articleA.source_version}:${articleA.revision_id}`,
    available_at: articleA.available_at, value: articleA.supporting_evidence[0] },
];
export function aiTask(type = 'summarize') {
  return { task_id: type === 'summarize' ? taskId : topicTaskId, task_type: type, snapshot_id: ID,
    as_of: snapshot.result.published_at, version: 'v1', status: 'succeeded', verification_status: 'passed',
    human_review_status: 'unreviewed', processing_overdue: false, content_draft: {
      facts: [], inferences: [{ text: 'C2 cited interpretation', claim: { kind: 'interpretation' },
        confidence: { kind: 'explanatory', value: '0.70' }, evidence_ids: ['ev-summary'] }],
      missing_evidence: [], citations },
    revision: 3, content_draft_source: 'provider', content_draft_created_at: snapshot.result.published_at,
    ai_generated: true, model_version: 'fake-v1', prompt_version: 'prompt-v1', retrieval_version: 'r1',
    provider: 'fake', token_usage: 20, cost: '0.10', latency_ms: 100, failure: null };
}
export function resolvedCitation(evidenceId: string, task = taskId) {
  const citation = citations.find(c => c.evidence_id === evidenceId)!;
  return { owner_id: OWNER, task_id: task, revision: 3, snapshot_id: ID, as_of: snapshot.result.published_at,
    version: 'v1', ...citation, status: 'ready', reason: null,
    locator: { kind: 'news_article_field', article_id: ARTICLE_A, article_revision_id: articleA.revision_id, article_revision: 1,
      field: evidenceId === 'ev-summary' ? 'summary' : 'supporting_evidence', index: evidenceId === 'ev-summary' ? null : 0 } };
}

export async function mockC2(page: Page) {
  const fulfilledBodies = new Map<import('@playwright/test').Request, unknown>();
  await mockRegulation(page);
  await mockLeaders(page, {}, (request, body) => fulfilledBodies.set(request, body));
  const errors: string[] = [];
  const traffic: { path: string; url: string; requestId: number; status?: number; fixtureBody: boolean }[] = [];
  const payloads: Record<string, any> = {};
  const reads: Promise<void>[] = [];
  let drawerRequests: Set<import('@playwright/test').Request> | undefined;
  let captureAtClose: ((request: import('@playwright/test').Request) => void) | undefined;
  const requestIds = new Map<import('@playwright/test').Request, number>();
  const closing = new Map<import('@playwright/test').Request, {
    operation: string; assertion: string | null; confirmations: { asserted(assertion: string): void }[];
  }>();
  const audit = createTrafficAudit();
  const closeTrace: Record<string, unknown>[] = [];
  page.on('request', request => {
    requestIds.set(request, requestIds.size + 1);
    drawerRequests?.add(request);
    captureAtClose?.(request);
  });
  const consoleErrors: { text: string; url: string }[] = [];
  page.on('pageerror', error => errors.push(`page: ${error.message}`));
  page.on('console', message => {
    if (message.type() === 'error') consoleErrors.push({ text: message.text(), url: message.location().url });
  });
  page.on('response', response => {
    const path = new URL(response.url()).pathname;
    if (path.startsWith('/api/')) traffic.push({ path, url: response.url(),
      requestId: requestIds.get(response.request())!, status: response.status(),
      fixtureBody: fulfilledBodies.has(response.request()) });
    if (path.startsWith('/api/') && response.status() < 400) {
      if (fulfilledBodies.has(response.request())) payloads[path] = fulfilledBodies.get(response.request());
      else reads.push(response.json().then(body => { payloads[path] = body; }).catch(error => { errors.push(`response JSON ${path}: ${error}`); }));
    }
    audit.response(response.request(), response.status());
  });
  page.on('requestfailed', request => {
    const close = closing.get(request);
    closeTrace.push({ event: 'failed', requestId: requestIds.get(request), url: request.url(),
      reason: request.failure()?.errorText, operation: close?.operation });
    if (close && request.failure()?.errorText === 'net::ERR_ABORTED') {
      const receipt = audit.expectRequest(request, { url: request.url(), method: request.method(),
        kind: 'cancel', reason: `exact drawer request at close: ${close.operation}` });
      close.confirmations.push(receipt);
      if (close.assertion) receipt.asserted(close.assertion);
    }
    audit.failed(request, request.failure()?.errorText || 'unknown');
  });
  const fallbackPaths = new Set([
    '/api/snapshots/list', `/api/snapshots/${ID}`, `/api/snapshots/${OLD}`,
    '/api/market/heatmap', '/api/market/sector-flow', '/api/market/sentiment',
    '/api/limit-up/ladder', '/api/limit-up/continuity', '/api/role/candidates', '/api/role/evidence',
    '/api/regulation/status', '/api/regulation/events',
  ]);
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const json = (body: unknown) => {
      fulfilledBodies.set(route.request(), body);
      return route.fulfill({ json: body });
    };
    if (path === '/api/market/overview') return json(env({
      topic_basis: { source: null, source_version: null, as_of: null, cutoff: snapshot.as_of },
      indexes: [{ security_id: 'IDX', code: '000300', name: 'C2 synthetic index', last: 1234, change_ratio: 0.01, amount: null, reason: 'source_field_unavailable' }],
      breadth: {
        up: 32, down: 31, flat: 0, valid_total: 63, coverage,
        members: {
          up: securities.slice(0, 63).filter(item => Number(item.change) > 0).map(item => ({ security_id: item.security_id, code: item.security_id, name: item.name })),
          down: securities.slice(0, 63).filter(item => Number(item.change) < 0).map(item => ({ security_id: item.security_id, code: item.security_id, name: item.name })),
          flat: [],
        },
        seal_rate: { numerator: 0, denominator: 63, value: 0, reason: null, coverage },
      },
      sentiment: {
        limit_up_count: 0, limit_down_count: 0, max_boards: 0,
        promote_rate: { yesterday_ge2: 0, numerator: 0, denominator: 0, value: null, excluded: 0, missing: 0, reason: 'no_yesterday_ge2_members' },
        yesterday_limit_up_median_return: null,
        yesterday_limit_up_return_coverage: { expected: 0, valid: 0, excluded: 0, missing: 0 },
        yesterday_limit_up_return_reason: 'no_yesterday_limit_up_members',
      },
      hot_sectors: [sector, { ...sector, sector_id: 'SEC002', name: 'C2 second topic' }],
      factors: {
        'FAC-M01': { advancing: { numerator: 32, denominator: 63, value: 32 / 63, reason: null, coverage },
          above_ma20: { numerator: 0, denominator: 0, value: null, reason: 'not_available', coverage }, method: 'synthetic' },
        'FAC-M02': { value: null, today_total: null, baseline_mean: null, baseline_days: 20, baseline_excludes_current: true,
          coverage, reason: 'not_available', method: 'synthetic' },
        'FAC-M03': { indexes: [], method: 'synthetic' },
      },
      historical_topic_analysis: { enabled: false, reason: 'historical_topic_coverage_unavailable' },
    }));
    if (path === '/api/topics/SEC001' || path === '/api/topics/SEC002') return json(topicData(url.searchParams, path.split('/').at(-1)));
    if (path === '/api/role/candidates') return json({
      ...env({ role: url.searchParams.get('role') || 'trend_leader', candidates,
        statuses: [{ sector_id: 'SEC001', status: 'ready', capability: 'ready', reasons: [] }],
        change_summaries: [], role_changes: [], total: candidates.length }),
      rule_version: 'role-v1', coverage: { expected: 25, valid: 25, excluded: 0, missing: 0 },
      basis: { unit: 'ratio', currency: 'CNY', price_type: 'adjusted', benchmark: 'CSI300' },
    });
    if (path === '/api/topics/rotation' || path === '/api/lhb' || path === '/api/market/sentiment/research') {
      return json(absent());
    }
    if (path.startsWith('/api/research/manifests/')) return json({
      snapshot_id: ID, domains: [{ domain: 'market', snapshot_id: ID }],
      versions: { adjustment: 'raw-v1', calendar: 'c2-calendar-v1', pool: 'c2-pool-v1' },
    });
    if (path === '/api/rank/range') return json({
      ...env({
        window_days: 20, adjusted_start: sessions[0], adjusted_end: sessions.at(-1), benchmark_return: 0.02,
        ranking: rankingForRequest(securities.slice(0, 25).map((security, index) => ({
          rank: index + 1, security_id: security.security_id, name: security.name, topics: [],
          topic_status: 'available', topic_reasons: [], reasons: [],
          return_ratio: 0.1 - index / 1000, excess_return_ratio: 0.1 - index / 1000 - 0.02,
          max_drawdown_ratio: 0.02, amount_change_ratio: 0.03,
        })), url.searchParams), exclusions: [],
      }),
      coverage: { expected: 25, valid: 25, excluded: 0, missing: 0 },
      rule_version: 'analysis-v1', basis: { unit: 'ratio', currency: 'CNY', price_type: 'raw', benchmark: 'B000300' },
    });
    if (path === '/api/compare/curves') return json({
      ...env({
      adjusted_start: sessions[0], adjusted_end: sessions.at(-1), benchmark: 'B000300',
      curves: (url.searchParams.get('symbols') || '').split(',').filter(Boolean).map(security_id => ({
        security_id, reasons: [], points: sessions.map((trade_date, index) => ({ trade_date, normalized: 100 + index, drawdown: 0 })),
      })),
      exclusions: [],
      }), basis: { unit: 'ratio', currency: 'CNY', price_type: 'raw', benchmark: 'B000300' },
    });
    if (/^\/api\/stock\/TEST\d{3}\/kline$/.test(path)) return json({
      ...env({
        security_id: path.split('/')[3], adjustment: 'raw', adjusted_start: sessions[0], adjusted_end: sessions.at(-1),
        chart_type: 'candlestick', points: sessions.map((trade_date, index) => ({
          trade_date, open: 10 + index, high: 12 + index, low: 9 + index, close: 11 + index, volume: 100, amount: 1000,
        })),
      }), basis: { unit: 'currency/share', currency: 'CNY', price_type: 'raw', benchmark: null },
    });
    if (path === '/api/news') {
      const type = url.searchParams.get('type'), q = url.searchParams.get('q') || '';
      const filtered = articles.filter(article =>
        (!type || article.article_type === type) && `${article.title} ${article.summary}`.includes(q));
      const page = Number(url.searchParams.get('page') || 1), size = Number(url.searchParams.get('size') || 20);
      return json(env({ articles: filtered.slice((page - 1) * size, page * size), page,
        size, total: filtered.length, sources: ['synthetic-wire'], event_count: 1 }));
    }
    if (path === '/api/news/wordcloud') {
      const type = url.searchParams.get('type') || 'all';
      return json(env({
        type, top_n: Number(url.searchParams.get('top_n') || 40), total: 1,
        words: [{ term: '订单', document_count: 1, article_ids: [ARTICLE_A], sentiment: null }],
        text_scope: { titles: 1, summaries: 1, contents: 0 }, tokenizer_version: 'wordcloud-tokenizer-v1',
      }));
    }
    if (path === `/api/news/${ARTICLE_A}` || path === `/api/news/${ARTICLE_B}`) {
      return json(env(path.endsWith(ARTICLE_A) ? articleA : articleB));
    }
    if (path === '/api/search') return json(searchData(url.searchParams));
    if (path === '/api/ai/summarize') return json(aiTask());
    if (path === '/api/ai/topic-suggest') return json(aiTask('topic_suggest'));
    if (path === `/api/ai/task/${taskId}`) return json(aiTask());
    if (path === `/api/ai/task/${topicTaskId}`) return json(aiTask('topic_suggest'));
    if (new RegExp(`^/api/ai/task/(${taskId}|${topicTaskId})/evidence/ev-(summary|support)$`).test(path)) {
      return json(resolvedCitation(path.split('/').at(-1)!, path.split('/')[4]));
    }
    if (path === '/api/owner/v1/session') return json({
      owner_id: OWNER, session_id: SESSION, expires_at: '2030-01-01T00:00:00Z',
    });
    if (path === `/api/news/${ARTICLE_A}/associations/review-state`) return json(associationData());
    if (/^\/api\/stock\/TEST\d{3}$/.test(path) || fallbackPaths.has(path)) return route.fallback();
    errors.push(`unmocked ${route.request().method()} ${path}`);
    return route.abort('blockedbyclient');
  });
  return {
    errors, traffic, payloads, closeTrace, settled: () => Promise.all(reads), receipts: audit.receipts,
    beginDrawerOpen: () => { drawerRequests = new Set(); captureAtClose = undefined; },
    beginDrawerClose: (securityId: string, operation: string, sector?: string) => {
      const close = { operation, assertion: null as string | null,
        confirmations: [] as { asserted(assertion: string): void }[] };
      const news = new URLSearchParams({ date: DATE, snapshot_id: ID, security_id: securityId, page: '1', size: '20' });
      const kline = new URLSearchParams({ snapshot_id: ID, start: sessions[0], end: DATE, adj: 'raw' });
      const role = sector ? new URLSearchParams({ date: DATE, snapshot_id: ID, security_id: securityId, sector }) : null;
      const lhb = new URLSearchParams({ date: DATE, security_id: securityId, page: '1', size: '20', as_of: snapshot.result.published_at });
      const capture = (request: import('@playwright/test').Request) => {
        const url = new URL(request.url());
        const expected = url.pathname === '/api/news' ? news
          : url.pathname === `/api/stock/${securityId}/kline` ? kline
          : url.pathname === '/api/lhb' ? lhb
          : url.pathname === '/api/role/evidence' ? role : null;
        if (request.method() !== 'GET' || !expected || url.searchParams.size !== expected.size
          || [...expected].some(([key, value]) => url.searchParams.get(key) !== value)) return;
        closing.set(request, close);
        closeTrace.push({ event: 'begin-close', requestId: requestIds.get(request), url: request.url(), operation });
      };
      for (const request of drawerRequests ?? []) capture(request);
      captureAtClose = capture;
      return { asserted: (assertion: string) => {
        expect(assertion).toBeTruthy();
        close.assertion = assertion;
        close.confirmations.forEach(receipt => receipt.asserted(assertion));
        captureAtClose = undefined;
        drawerRequests = undefined;
        closeTrace.push({ event: 'asserted-close', operation, assertion });
      } };
    },
    expectDenial: (request: import('@playwright/test').Request, status: number, reason: string) =>
      audit.expectRequest(request, { url: request.url(), method: request.method(), kind: 'denial', status, reason,
        allowAbortAfterDenial: reason === 'owner-session' && status === 401 && new URL(request.url()).pathname === '/api/owner/v1/session' }),
    expectCancel: (request: import('@playwright/test').Request, reason: string) =>
      audit.expectRequest(request, { url: request.url(), method: request.method(), kind: 'cancel', reason }),
    assertClean: () => {
      const remaining = [...audit.receipts.filter(r => r.kind === 'denial' && r.observed && r.assertion)];
      for (const message of consoleErrors) {
        const match = remaining.findIndex(r => r.url === message.url
          && message.text === `Failed to load resource: the server responded with a status of ${r.status} (${r.status === 403 ? 'Forbidden' : 'Unauthorized'})`);
        if (match >= 0) remaining.splice(match, 1);
        else errors.push(`console: ${message.url}: ${message.text}`);
      }
      expect(errors, 'C2 unexpected page/console/request/API errors').toEqual([]);
      audit.assertClean();
    },
  };
}

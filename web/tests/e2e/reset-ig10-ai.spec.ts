import { expect, test, type Page } from '@playwright/test';

import { ARTICLE_A, articleA, mockNews, NEWS_SNAPSHOT } from '../fixtures/news';
import { mockReview, OWNER, reviewServer } from '../fixtures/review';

// Parent overrides the AI config testMatch; do not run in public Reset config.
const taskId = '00000000-0000-4000-8000-000000000195';
const evidenceId = 'ev-summary';
const evidencePath = `/api/ai/task/${taskId}/evidence/${evidenceId}`;
const url = `/?view=news&snapshot=${NEWS_SNAPSHOT}&date=2026-09-10&article=${ARTICLE_A}`;
const citation = {
  evidence_id: evidenceId,
  evidence_position: '/input/article/summary',
  source_identity: `news:${articleA.source}:${articleA.source_version}:${articleA.revision_id}`,
  available_at: articleA.available_at,
  value: articleA.summary,
};
const result = {
  task_id: taskId, task_type: 'summarize', snapshot_id: NEWS_SNAPSHOT,
  as_of: '2026-09-10T10:05:00Z', version: 'v1', status: 'succeeded',
  verification_status: 'passed', human_review_status: 'unreviewed',
  processing_overdue: false,
  content_draft: {
    facts: [], inferences: [{
      text: 'Synthetic cited interpretation.', claim: { kind: 'interpretation' },
      confidence: { kind: 'explanatory', value: '0.70' }, evidence_ids: [evidenceId],
    }], missing_evidence: [], citations: [citation],
  },
  revision: 3, content_draft_source: 'provider',
  content_draft_created_at: '2026-09-10T10:05:02Z', ai_generated: true,
  model_version: 'fake-v1', prompt_version: 'prompt-v1', retrieval_version: 'r1',
  provider: 'fake', token_usage: 20, cost: '0.10', latency_ms: 100, failure: null,
};
const resolved = {
  owner_id: OWNER, task_id: taskId, revision: 3, snapshot_id: NEWS_SNAPSHOT,
  as_of: result.as_of, version: 'v1', ...citation,
  status: 'ready', reason: null,
  locator: {
    kind: 'news_article_field', article_id: ARTICLE_A,
    article_revision_id: articleA.revision_id, article_revision: 1,
    field: 'summary', index: null,
  },
};

async function setup(page: Page) {
  await mockNews(page);
  await mockReview(page, reviewServer());
  await page.route('**/api/ai/summarize', route => route.fulfill({ json: result }));
  await page.route(`**/api/ai/task/${taskId}`, route => route.fulfill({ json: result }));
  await page.goto(url);
  const panel = page.getByRole('region', { name: 'AI 消息摘要', exact: true });
  await panel.getByRole('button', { name: '生成消息摘要', exact: true }).click();
  await expect(panel).toContainText('AI 草稿');
  await panel.getByText('引用与来源', { exact: true }).click();
  return panel;
}

test('citation opens one same-PUB inline source panel and highlights the exact server-derived field', async ({ page }) => {
  const panel = await setup(page);
  const reads: URL[] = [];
  await page.route(`**${evidencePath}?*`, route => {
    reads.push(new URL(route.request().url()));
    return route.fulfill({ json: resolved });
  });
  expect(reads).toHaveLength(0);
  const trigger = panel.getByRole('button', { name: `查看引用 ${evidenceId}`, exact: true });
  await trigger.focus();
  await page.keyboard.press('Enter');
  const source = panel.getByRole('region', { name: '引用来源' });
  await expect(source).toBeVisible();
  await expect(source.locator('mark')).toHaveText(articleA.summary);
  expect(reads).toHaveLength(1);
  expect(Object.fromEntries(reads[0].searchParams)).toEqual({
    revision: '3', snapshot_id: NEWS_SNAPSHOT,
  });
  await expect(source).toContainText(ARTICLE_A);
  await expect(source).toContainText(articleA.revision_id);
  await expect(source).toContainText(citation.available_at);
  await expect(source).toContainText('summary');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(source.getByRole('link')).toHaveCount(0);
  await page.keyboard.press('Escape');
  await expect(source).toHaveCount(0);
  await expect(trigger).toBeFocused();
  expect(reads).toHaveLength(1);
});

for (const mismatch of ['owner_id', 'task_id', 'revision', 'snapshot_id', 'evidence_id', 'source_identity', 'value'] as const) {
  test(`citation rejects ${mismatch} mismatch before showing a field`, async ({ page }) => {
    const panel = await setup(page);
    await page.route(`**${evidencePath}?*`, route => route.fulfill({
      json: {
        ...resolved,
        [mismatch]: mismatch === 'revision' ? 4 : 'foreign synthetic evidence',
      },
    }));
    await panel.getByRole('button', { name: `查看引用 ${evidenceId}`, exact: true }).click();
    const source = panel.getByRole('region', { name: '引用来源' });
    await expect(source.getByRole('alert')).toContainText('不一致');
    await expect(source.locator('mark')).toHaveCount(0);
    await expect(source).not.toContainText('foreign synthetic evidence');
  });
}

test('legacy unresolved citation is unavailable with no guessed article, link or highlight', async ({ page }) => {
  const panel = await setup(page);
  await page.route(`**${evidencePath}?*`, route => route.fulfill({
    json: {
      ...resolved, status: 'unavailable', reason: 'legacy_locator_unavailable',
      locator: null, value: null,
    },
  }));
  await panel.getByRole('button', { name: `查看引用 ${evidenceId}`, exact: true }).click();
  const source = panel.getByRole('region', { name: '引用来源' });
  await expect(source).toContainText('不可定位');
  await expect(source.locator('mark')).toHaveCount(0);
  await expect(source.getByRole('link')).toHaveCount(0);
  await expect(source).not.toContainText(ARTICLE_A);
});

test('every reopen reauthorizes instead of caching source text after withdrawal', async ({ page }) => {
  const panel = await setup(page);
  let reads = 0;
  let restored = false;
  await page.route(`**${evidencePath}?*`, route => {
    reads++;
    return reads === 1 || restored
      ? route.fulfill({ json: resolved })
      : route.fulfill({ status: 403, json: { code: 'FORBIDDEN' } });
  });
  const trigger = panel.getByRole('button', { name: `查看引用 ${evidenceId}`, exact: true });
  await trigger.click();
  const source = panel.getByRole('region', { name: '引用来源' });
  await expect(source.locator('mark')).toHaveText(articleA.summary);
  await page.keyboard.press('Escape');
  await trigger.click();
  const refusal = page.getByRole('alert').filter({
    has: page.getByRole('heading', { name: '来源访问已被拒绝', exact: true }),
  });
  await expect(refusal).toBeVisible();
  await expect(refusal).toContainText('已清除所有受保护内容与内存中的来源数据。恢复授权后可重新请求。');
  await expect(page.getByRole('region', { name: '引用来源', exact: true })).toHaveCount(0);
  await expect(page.locator('mark')).toHaveCount(0);
  await expect(page.getByRole('button', { name: `查看引用 ${evidenceId}`, exact: true })).toHaveCount(0);
  await expect(page.getByRole('main')).not.toContainText(articleA.summary);
  expect(reads).toBe(2);
  restored = true;
  await refusal.getByRole('button', { name: '重新检查访问权限', exact: true }).click();
  await expect(page.getByRole('region', { name: '消息详情' })).toContainText(articleA.revision_id);
  await expect(source).toHaveCount(0);
  expect(reads).toBe(2);
  await panel.getByRole('button', { name: '生成消息摘要', exact: true }).click();
  await panel.getByText('引用与来源', { exact: true }).click();
  await trigger.click();
  await expect(source.locator('mark')).toHaveText(articleA.summary);
  expect(reads).toBe(3);
});

test('a denied read in panel B clears displayed citations in both AI panels and shows explicit refusal', async ({ page }) => {
  const a = await setup(page);
  const topicTaskId = '00000000-0000-4000-8000-000000000196';
  const topicResult = { ...result, task_id: topicTaskId, task_type: 'topic_suggest' };
  const topicEvidencePath = `/api/ai/task/${topicTaskId}/evidence/${evidenceId}`;
  let topicReads = 0;
  await page.route('**/api/ai/topic-suggest', route => route.fulfill({ json: topicResult }));
  await page.route(`**/api/ai/task/${topicTaskId}`, route => route.fulfill({ json: topicResult }));
  await page.route(`**${evidencePath}?*`, route => route.fulfill({ json: resolved }));
  await page.route(`**${topicEvidencePath}?*`, route => {
    topicReads++;
    return topicReads === 1
      ? route.fulfill({ json: { ...resolved, task_id: topicTaskId } })
      : route.fulfill({ status: 403, json: { code: 'FORBIDDEN' } });
  });
  const b = page.locator('section.ai-task-panel[aria-label="AI 题材建议"]');
  await b.getByRole('button', { name: '生成题材建议', exact: true }).click();
  await b.getByText('引用与来源', { exact: true }).click();
  await a.getByRole('button', { name: `查看引用 ${evidenceId}`, exact: true }).click();
  await expect(a.getByRole('region', { name: '引用来源' }).locator('mark')).toHaveText(articleA.summary);
  await b.getByRole('button', { name: `查看引用 ${evidenceId}`, exact: true }).click();
  await expect(b.getByRole('region', { name: '引用来源' }).locator('mark')).toHaveText(articleA.summary);
  await expect(page.getByRole('region', { name: '引用来源', exact: true })).toHaveCount(2);
  await b.getByRole('button', { name: `查看引用 ${evidenceId}`, exact: true }).click();
  const refusal = page.getByRole('alert').filter({
    has: page.getByRole('heading', { name: '来源访问已被拒绝', exact: true }),
  });
  await expect(refusal).toBeVisible();
  await expect(refusal).toContainText('已清除所有受保护内容与内存中的来源数据。恢复授权后可重新请求。');
  await expect(page.getByRole('region', { name: '引用来源', exact: true })).toHaveCount(0);
  await expect(page.locator('mark')).toHaveCount(0);
  await expect(page.getByRole('button', { name: `查看引用 ${evidenceId}`, exact: true })).toHaveCount(0);
  await expect(page.getByRole('main')).not.toContainText(articleA.summary);
  expect(topicReads).toBe(2);
});

test('closing during a pending citation read drops late text and never reopens', async ({ page }) => {
  const panel = await setup(page);
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  let entered!: () => void;
  const started = new Promise<void>(resolve => { entered = resolve; });
  await page.route(`**${evidencePath}?*`, async route => {
    entered();
    await pending;
    await route.fulfill({ json: resolved }).catch(() => {});
  });
  const trigger = panel.getByRole('button', { name: `查看引用 ${evidenceId}`, exact: true });
  await trigger.click();
  await started;
  await panel.getByRole('button', { name: '关闭引用来源', exact: true }).click();
  release();
  await expect(panel.getByRole('region', { name: '引用来源' })).toHaveCount(0);
  await expect(trigger).toBeFocused();
});

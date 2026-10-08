import { expect, test } from '@playwright/test';

import { ID, mockMarket } from '../fixtures/market';

const taskId = '00000000-0000-4000-8000-000000000099';

function task(overrides: Record<string, unknown> = {}) {
  return {
    task_id: taskId,
    task_type: 'daily_review',
    snapshot_id: ID,
    as_of: '2026-09-10T08:01:00Z',
    version: 'v1',
    status: 'pending',
    verification_status: 'pending',
    human_review_status: 'unreviewed',
    processing_overdue: false,
    content_draft: null,
    revision: null,
    content_draft_source: null,
    content_draft_created_at: null,
    ai_generated: null,
    model_version: null,
    prompt_version: null,
    retrieval_version: 'r1',
    provider: null,
    token_usage: null,
    cost: null,
    latency_ms: null,
    failure: null,
    ...overrides,
  };
}

const draft = {
  facts: [{
    text: '已发布快照记录上涨家数为 32。',
    evidence_id: 'ev-up',
    evidence_position: '/input/market_overview/breadth/up',
    value: 32,
    unit: 'count',
    precision: 0,
  }],
  inferences: [{
    text: '当前证据适合继续人工复核。',
    claim: { kind: 'interpretation' },
    confidence: { kind: 'explanatory', value: '0.70' },
    evidence_ids: ['ev-up'],
  }],
  missing_evidence: [{ text: '盘中逐笔证据不可用' }],
  citations: [{
    evidence_id: 'ev-up',
    evidence_position: '/input/market_overview/breadth/up',
    source_identity: `daily_review:${ID}`,
    available_at: '2026-09-10T08:01:00Z',
    value: 32,
    unit: 'count',
    precision: 0,
  }],
};

test('private AI previews selected fragments, polls, and protects edited notes on adoption', async ({ page }) => {
  await mockMarket(page);
  let polls = 0;
  let submitted: Record<string, unknown> | undefined;
  await page.route('**/api/ai/daily-review', async route => {
    submitted = route.request().postDataJSON();
    await route.fulfill({ json: task() });
  });
  await page.route(`**/api/ai/task/${taskId}`, async route => {
    polls++;
    await route.fulfill({
      json: polls === 1
        ? task({ status: 'running', processing_overdue: true })
        : task({
          status: 'succeeded',
          verification_status: 'passed',
          content_draft: draft,
          revision: 1,
          content_draft_source: 'provider',
          content_draft_created_at: '2026-09-10T08:01:02Z',
          ai_generated: true,
          model_version: 'model-v1',
          prompt_version: 'prompt-v1',
          provider: 'fake',
          token_usage: 20,
          cost: '0.10',
          latency_ms: 2000,
        }),
    });
  });

  await page.goto(`/?view=review&snapshot=${ID}&date=2026-09-10`);
  const panel = page.getByRole('region', { name: 'AI 每日复盘' });
  await expect(panel).toBeVisible();
  await expect(page.getByText('仅发送明确选择的片段')).toBeVisible();

  await page.getByRole('textbox', { name: '标题', exact: true }).fill('复盘标题');
  await page.getByRole('textbox', { name: '判断', exact: true }).fill('仅发送判断');
  await page.getByRole('textbox', { name: '依据', exact: true }).fill('仅发送依据');
  await panel.getByRole('checkbox', { name: '判断' }).check();
  await panel.getByRole('checkbox', { name: '依据' }).check();
  await expect(panel.getByText('判断：仅发送判断')).toBeVisible();
  await expect(panel.getByText('依据：仅发送依据')).toBeVisible();
  await expect(panel.getByText(/字符 · 快照/)).toBeVisible();

  await panel.getByRole('button', { name: '生成每日复盘' }).click();
  await expect(panel.getByText('仍在处理')).toBeVisible();
  await expect(panel.getByText('AI 草稿')).toBeVisible();
  await expect(panel.getByText('任务状态：succeeded')).toBeVisible();
  await expect(panel.getByText('校验状态：passed')).toBeVisible();
  await expect(panel.getByText('人工审核：unreviewed')).toBeVisible();
  expect(submitted).toMatchObject({
    date: '2026-09-10',
    notes_context: '判断：仅发送判断\n\n依据：仅发送依据',
    snapshot_id: ID,
  });
  expect(JSON.stringify(submitted)).not.toContain('复盘标题');

  await page.getByRole('textbox', { name: '正文', exact: true }).fill('提交后本地已修改');
  await panel.getByRole('button', { name: '采用草稿' }).click();
  await expect(panel.getByRole('alert')).toContainText('编辑器已在提交后变化');
  await panel.getByRole('button', { name: '另存为新笔记' }).click();
  await expect(panel.getByRole('status')).toContainText('回读验证通过');
  await expect(page.getByRole('button', { name: /AI 每日复盘/ })).toBeVisible();
  await expect(page.getByRole('textbox', { name: '正文', exact: true }))
    .toHaveValue('提交后本地已修改');
});

test('cancel and review reconcile 409 responses by reading authoritative task state', async ({ page }) => {
  await mockMarket(page);
  let submissions = 0;
  let cancelled = false;
  let accepted = false;
  await page.route('**/api/ai/daily-review', async route => {
    submissions++;
    await route.fulfill({
      json: submissions === 1
        ? task()
        : task({
          status: 'succeeded',
          verification_status: 'passed',
          content_draft: draft,
          revision: 1,
          content_draft_source: 'provider',
          content_draft_created_at: '2026-09-10T08:01:02Z',
          ai_generated: true,
          model_version: 'model-v1',
          prompt_version: 'prompt-v1',
          provider: 'fake',
          token_usage: 20,
          cost: '0.10',
          latency_ms: 2000,
        }),
    });
  });
  await page.route(`**/api/ai/task/${taskId}/cancel`, async route => {
    cancelled = true;
    await route.fulfill({ status: 409, json: { code: 'VERSION_CONFLICT' } });
  });
  await page.route(`**/api/ai/task/${taskId}/review`, async route => {
    accepted = true;
    await route.fulfill({ status: 409, json: { code: 'VERSION_CONFLICT' } });
  });
  await page.route(`**/api/ai/task/${taskId}`, async route => {
    await route.fulfill({
      json: accepted
        ? task({
          status: 'succeeded',
          verification_status: 'passed',
          human_review_status: 'accepted',
          content_draft: draft,
          revision: 1,
          content_draft_source: 'provider',
          content_draft_created_at: '2026-09-10T08:01:02Z',
          ai_generated: true,
          model_version: 'model-v1',
          prompt_version: 'prompt-v1',
          provider: 'fake',
          token_usage: 20,
          cost: '0.10',
          latency_ms: 2000,
        })
        : task({ status: cancelled ? 'cancelled' : 'running' }),
    });
  });

  await page.goto(`/?view=review&snapshot=${ID}&date=2026-09-10`);
  const panel = page.getByRole('region', { name: 'AI 每日复盘' });
  await page.getByRole('textbox', { name: '判断', exact: true }).fill('第一次提交');
  await panel.getByRole('checkbox', { name: '判断' }).check();
  await panel.getByRole('button', { name: '生成每日复盘' }).click();
  await panel.getByRole('button', { name: '取消任务' }).click();
  await expect(panel.getByText('任务状态：cancelled')).toBeVisible();

  await page.getByRole('textbox', { name: '判断', exact: true }).fill('第二次提交');
  await panel.getByRole('button', { name: '生成每日复盘' }).click();
  await expect(panel.getByText('AI 草稿')).toBeVisible();
  await panel.getByRole('button', { name: '接受校验草稿' }).click();
  await expect(panel.getByText('人工审核：accepted')).toBeVisible();
  expect(submissions).toBe(2);
});

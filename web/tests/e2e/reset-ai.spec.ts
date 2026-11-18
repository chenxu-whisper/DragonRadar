import { expect, test } from '@playwright/test';

import { ID, OLD, mockMarket } from '../fixtures/market';
import { mockReview, reviewServer } from '../fixtures/review';

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

test('private Reset keeps the workbench and AI panel behind the session lock', async ({ page }) => {
  await mockMarket(page);
  const owner = reviewServer();
  owner.authorized = false;
  await mockReview(page, owner);

  await page.goto(`/?view=review&snapshot=${ID}&date=2026-09-10`);

  await expect(page.getByRole('heading', { name: '私人会话已锁定' })).toBeVisible();
  await expect(page.getByRole('heading', { name: '我的复盘' })).toHaveCount(0);
  await expect(page.getByRole('region', { name: 'AI 每日复盘' })).toHaveCount(0);
});

test('private AI previews selected fragments, polls, and protects edited notes on adoption', async ({ page }) => {
  await mockMarket(page);
  await mockReview(page, reviewServer());
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
  await mockReview(page, reviewServer());
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

test('cross-snapshot adoption preserves the selected note and saves task provenance as new', async ({ page }) => {
  await mockMarket(page);
  await mockReview(page, reviewServer());
  await page.route('**/api/ai/daily-review', route => route.fulfill({
    json: task({
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
      latency_ms: 100,
    }),
  }));

  await page.goto(`/?view=review&snapshot=${OLD}&date=2026-09-09`);
  await page.getByRole('textbox', { name: '标题', exact: true }).fill('旧快照笔记');
  await page.getByRole('textbox', { name: '正文', exact: true }).fill('必须保留的旧正文');
  await page.getByRole('textbox', { name: '判断', exact: true }).fill('发送给新任务');
  await page.getByRole('button', { name: '保存笔记' }).click();
  await expect(page.locator('.local-note-editor').getByRole('status'))
    .toContainText('回读验证通过');

  await page.getByLabel('交易日期').fill('2026-09-10');
  await expect(page).toHaveURL(new RegExp(`snapshot=${ID}`));
  const panel = page.getByRole('region', { name: 'AI 每日复盘' });
  await panel.getByRole('checkbox', { name: '判断' }).check();
  await panel.getByRole('button', { name: '生成每日复盘' }).click();
  await expect(panel.getByText('AI 草稿')).toBeVisible();

  await panel.getByRole('button', { name: '采用草稿' }).click();
  await expect(panel.getByRole('alert')).toContainText('快照不一致');
  await expect(page.getByRole('textbox', { name: '正文', exact: true }))
    .toHaveValue('必须保留的旧正文');
  await panel.getByRole('button', { name: '另存为新笔记' }).click();
  await expect(panel.getByRole('status')).toContainText('回读验证通过');

  const bindings = await page.evaluate(async () => {
    const database = await new Promise<IDBDatabase>((resolve, reject) => {
      const opening = indexedDB.open('dragon-radar-local-v3');
      opening.onsuccess = () => resolve(opening.result);
      opening.onerror = () => reject(opening.error);
    });
    const notes = await new Promise<Array<{
      snapshot_id: string;
      trade_date?: string;
      content: { title: string };
    }>>((resolve, reject) => {
      const request = database.transaction('notes').objectStore('notes').getAll();
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    database.close();
    return notes.map(note => ({
      title: note.content.title,
      snapshot: note.snapshot_id,
      date: note.trade_date,
    }));
  });
  expect(bindings).toEqual(expect.arrayContaining([
    { title: '旧快照笔记', snapshot: OLD, date: '2026-09-09' },
    { title: 'AI 每日复盘 2026-09-10', snapshot: ID, date: '2026-09-10' },
  ]));
});

test('overview daily AI expands snapshot facts without sending private notes and saves a separate review', async ({ page }) => {
  await mockMarket(page);
  await mockReview(page, reviewServer());
  let submitted: Record<string, unknown> | undefined;
  let accepted = false;
  const result = () => task({
    status: 'succeeded',
    verification_status: 'passed',
    human_review_status: accepted ? 'accepted' : 'unreviewed',
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
    latency_ms: 100,
  });
  await page.route('**/api/ai/daily-review', route => {
    submitted = route.request().postDataJSON();
    return route.fulfill({ json: result() });
  });
  await page.route(`**/api/ai/task/${taskId}`, route => route.fulfill({ json: result() }));
  await page.route(`**/api/ai/task/${taskId}/review`, route => {
    accepted = true;
    return route.fulfill({ json: {
      task_id: taskId, human_review_status: 'accepted', revision: 1,
      idempotency_key: route.request().postDataJSON().idempotency_key,
      verification_status: 'passed', note: 'Accepted synthetic review',
    } });
  });
  await page.goto(`/?view=review&snapshot=${ID}&date=2026-09-10`);
  await page.getByRole('textbox', { name: '标题', exact: true }).fill('私人原始笔记');
  await page.getByRole('textbox', { name: '正文', exact: true }).fill('私人内容不能发送到总览任务');
  await page.getByRole('button', { name: '保存笔记', exact: true }).click();
  await expect(page.locator('.local-note-editor').getByRole('status')).toContainText('回读验证通过');
  await page.getByRole('navigation', { name: '主导航' }).getByRole('link', { name: '市场总览', exact: true }).click();
  const daily = page.getByRole('region', { name: 'AI 每日速览', exact: true });
  await expect(daily).toContainText('快照事实模板');
  expect(submitted).toBeUndefined();
  await daily.getByRole('button', { name: '展开完整 AI 分析', exact: true }).click();
  const panel = daily.getByRole('region', { name: 'AI 每日复盘', exact: true });
  await expect(panel.getByLabel('发送内容预览')).toContainText('上涨 32');
  await expect(panel.getByLabel('发送内容预览')).not.toContainText('私人内容不能发送');
  expect(submitted).toBeUndefined();
  await panel.getByRole('button', { name: '生成每日复盘', exact: true }).click();
  await expect(panel).toContainText('AI 草稿');
  expect(submitted).toMatchObject({ snapshot_id: ID, date: '2026-09-10', version: 'v1' });
  expect(String(submitted?.notes_context)).toContain('上涨 32');
  expect(String(submitted?.notes_context)).not.toContain('私人内容不能发送');
  await panel.getByRole('button', { name: '接受校验草稿', exact: true }).click();
  await expect(panel).toContainText('人工审核：accepted');
  await expect(panel.getByRole('heading', { name: '事实', exact: true })).toBeVisible();
  await expect(panel.getByRole('heading', { name: '推断', exact: true })).toBeVisible();
  await expect(panel.getByRole('heading', { name: '缺失证据', exact: true })).toBeVisible();
  await panel.getByRole('button', { name: '采用草稿', exact: true }).click();
  await expect(panel.getByRole('status')).toContainText('回读验证通过');
  await page.getByRole('navigation', { name: '主导航' }).getByRole('link', { name: '我的复盘', exact: true }).click();
  await expect(page.getByRole('button', { name: '私人原始笔记', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'AI 每日速览 2026-09-10', exact: true }).click();
  await expect(page.getByRole('textbox', { name: '正文', exact: true })).toHaveValue(/事实/);
});

function completedTask(overrides: Record<string, unknown> = {}) {
  return task({
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
    latency_ms: 100,
    ...overrides,
  });
}

const mismatches = [
  { field: 'snapshot_id', value: OLD },
  { field: 'as_of', value: '2026-09-10T08:02:00Z' },
  { field: 'version', value: 'v2' },
  { field: 'task_type', value: 'topic_suggest' },
  { field: 'task_id', value: '00000000-0000-4000-8000-000000000098' },
] as const;

for (const source of ['submit', 'poll', 'cancel409', 'review', 'review409'] as const) {
  for (const mismatch of mismatches) {
    if (source === 'submit' && mismatch.field === 'task_id') continue;
    test(`AI rejects ${mismatch.field} mismatch from ${source} before displaying or adopting it`, async ({ page }) => {
      await mockMarket(page);
      const owner = reviewServer();
      await mockReview(page, owner);
      const foreignText = 'Foreign task content must never be displayed or saved';
      const foreignTask = completedTask({
        [mismatch.field]: mismatch.value,
        human_review_status: 'accepted',
        content_draft: {
          ...draft,
          inferences: [{ ...draft.inferences[0], text: foreignText }],
        },
      });
      let refreshing = source === 'poll';
      let reads = 0;
      const reviewing = source === 'review' || source === 'review409';
      await page.route('**/api/ai/daily-review', route => route.fulfill({
        json: source === 'submit' ? foreignTask : reviewing ? completedTask() : task(),
      }));
      await page.route(`**/api/ai/task/${taskId}`, route => {
        reads++;
        return route.fulfill({ json: refreshing ? foreignTask : task() });
      });
      await page.route(`**/api/ai/task/${taskId}/cancel`, route => {
        refreshing = true;
        return route.fulfill({ status: 409, json: { code: 'VERSION_CONFLICT' } });
      });
      await page.route(`**/api/ai/task/${taskId}/review`, route => {
        refreshing = true;
        return source === 'review409'
          ? route.fulfill({ status: 409, json: { code: 'VERSION_CONFLICT' } })
          : route.fulfill({ json: {
            task_id: taskId, human_review_status: 'accepted', revision: 1,
            idempotency_key: route.request().postDataJSON().idempotency_key,
            verification_status: 'passed', note: 'Accepted synthetic review',
          } });
      });

      await page.goto(`/?view=overview&snapshot=${ID}&date=2026-09-10`);
      const daily = page.getByRole('region', { name: 'AI 每日速览', exact: true });
      await daily.getByRole('button', { name: '展开完整 AI 分析', exact: true }).click();
      const panel = daily.getByRole('region', { name: 'AI 每日复盘', exact: true });
      await panel.getByRole('button', { name: '生成每日复盘', exact: true }).click();
      if (source === 'cancel409') {
        await panel.getByRole('button', { name: '取消任务', exact: true }).click();
      } else if (reviewing) {
        await expect(panel).toContainText('人工审核：unreviewed');
        await panel.getByRole('button', { name: '接受校验草稿', exact: true }).click();
      }
      await expect(panel.getByRole('alert')).toContainText('不一致');
      await expect(panel).not.toContainText(foreignText);
      await expect(panel).not.toContainText('人工审核：accepted');
      await expect(panel.getByRole('status')).not.toContainText('人工审核已记录');
      if (!reviewing) {
        await expect(panel.getByRole('button', { name: '采用草稿', exact: true })).toHaveCount(0);
      }
      if (source !== 'submit') expect(reads).toBeGreaterThan(0);
      expect(owner.calls).toEqual([]);
      const noteCount = await page.evaluate(async () => {
        if (!(await indexedDB.databases()).some(item => item.name === 'dragon-radar-local-v3')) {
          return 0;
        }
        const database = await new Promise<IDBDatabase>((resolve, reject) => {
          const opening = indexedDB.open('dragon-radar-local-v3');
          opening.onsuccess = () => resolve(opening.result);
          opening.onerror = () => reject(opening.error);
        });
        const count = await new Promise<number>((resolve, reject) => {
          const request = database.transaction('notes').objectStore('notes').count();
          request.onsuccess = () => resolve(request.result);
          request.onerror = () => reject(request.error);
        });
        database.close();
        return count;
      });
      expect(noteCount).toBe(0);
    });
  }
}

test('overview retains the accepted AI result across collapse without another POST', async ({ page }) => {
  await mockMarket(page);
  await mockReview(page, reviewServer());
  let submissions = 0;
  let reviews = 0;
  let accepted = false;
  const result = () => completedTask({
    human_review_status: accepted ? 'accepted' : 'unreviewed',
  });
  await page.route('**/api/ai/daily-review', route => {
    submissions++;
    return route.fulfill({ json: result() });
  });
  await page.route(`**/api/ai/task/${taskId}`, route => route.fulfill({ json: result() }));
  await page.route(`**/api/ai/task/${taskId}/review`, route => {
    reviews++;
    accepted = true;
    return route.fulfill({ json: {
      task_id: taskId, human_review_status: 'accepted', revision: 1,
      idempotency_key: route.request().postDataJSON().idempotency_key,
      verification_status: 'passed', note: 'Accepted synthetic review',
    } });
  });
  await page.goto(`/?view=overview&snapshot=${ID}&date=2026-09-10`);
  const daily = page.getByRole('region', { name: 'AI 每日速览', exact: true });
  const panel = daily.getByRole('region', { name: 'AI 每日复盘', exact: true });
  await expect(panel).toHaveCount(0);
  await daily.getByRole('button', { name: '展开完整 AI 分析', exact: true }).click();
  expect(submissions).toBe(0);
  await panel.getByRole('button', { name: '生成每日复盘', exact: true }).click();
  await panel.getByRole('button', { name: '接受校验草稿', exact: true }).click();
  await expect(panel).toContainText('人工审核：accepted');
  await expect(daily).toContainText('当前发布未归档已审核的 AI 每日速览');
  await daily.getByRole('button', { name: '收起完整 AI 分析', exact: true }).click();
  await expect(panel).toBeHidden();
  await expect(daily.locator('.ai-task-panel')).toHaveCount(1);
  await daily.getByRole('button', { name: '展开完整 AI 分析', exact: true }).click();
  await expect(panel).toBeVisible();
  await expect(panel).toContainText('人工审核：accepted');
  await expect(panel).toContainText(draft.inferences[0].text);
  expect(submissions).toBe(1);
  expect(reviews).toBe(1);
  await panel.getByRole('button', { name: '采用草稿', exact: true }).click();
  await expect(panel.getByRole('status')).toContainText('回读验证通过');
});

test('AI accepts normalized UTC publication instants on submit, poll and review reads', async ({ page }) => {
  await mockMarket(page);
  await mockReview(page, reviewServer());
  const asOf = '2026-09-10T08:01:00+00:00';
  let accepted = false;
  await page.route('**/api/ai/daily-review', route => route.fulfill({
    json: task({ as_of: asOf }),
  }));
  await page.route(`**/api/ai/task/${taskId}`, route => route.fulfill({
    json: completedTask({
      as_of: asOf,
      human_review_status: accepted ? 'accepted' : 'unreviewed',
    }),
  }));
  await page.route(`**/api/ai/task/${taskId}/review`, route => {
    accepted = true;
    return route.fulfill({ json: {
      task_id: taskId, human_review_status: 'accepted', revision: 1,
      idempotency_key: route.request().postDataJSON().idempotency_key,
      verification_status: 'passed', note: 'Accepted synthetic review',
    } });
  });
  await page.goto(`/?view=overview&snapshot=${ID}&date=2026-09-10`);
  const daily = page.getByRole('region', { name: 'AI 每日速览', exact: true });
  await daily.getByRole('button', { name: '展开完整 AI 分析', exact: true }).click();
  const panel = daily.getByRole('region', { name: 'AI 每日复盘', exact: true });
  await panel.getByRole('button', { name: '生成每日复盘', exact: true }).click();
  await expect(panel).toContainText('人工审核：unreviewed');
  await panel.getByRole('button', { name: '接受校验草稿', exact: true }).click();
  await expect(panel).toContainText('人工审核：accepted');
  await panel.getByRole('button', { name: '采用草稿', exact: true }).click();
  await expect(panel.getByRole('status')).toContainText('回读验证通过');
  await expect(panel.getByRole('alert')).toHaveCount(0);
});

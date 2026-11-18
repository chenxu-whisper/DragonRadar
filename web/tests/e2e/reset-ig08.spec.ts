import { expect, test, type Page } from '@playwright/test';

import {
  ARTICLE_A, articleA, mockNews, NEWS_SNAPSHOT,
} from '../fixtures/news';
import { mockReview, OWNER, reviewServer } from '../fixtures/review';

// Parent runs this with Reset + owner enabled, AI optional, after unified source freeze.
const associationId = articleA.topics[0].association_id;
const statePath = `/api/news/${ARTICLE_A}/associations/review-state`;
const commandPath = `/api/news/${ARTICLE_A}/associations/review`;
const url = `/?view=news&snapshot=${NEWS_SNAPSHOT}&date=2026-09-10&article=${ARTICLE_A}`;

function state(revision = 7, decision: 'accept' | 'reject' | null = null) {
  return {
    owner_id: OWNER,
    article_id: ARTICLE_A,
    article_revision_id: articleA.revision_id,
    article_revision: 1,
    snapshot_id: NEWS_SNAPSHOT,
    trade_date: '2026-09-10',
    associations: [{
      association_id: associationId,
      revision,
      status: decision === 'accept' ? 'accepted' : decision === 'reject' ? 'rejected' : 'unreviewed',
      decision,
      own_audit: [],
    }],
  };
}

async function setup(page: Page) {
  await mockNews(page);
  await mockReview(page, reviewServer());
  const reads: URL[] = [];
  await page.route(`**${statePath}?*`, route => {
    reads.push(new URL(route.request().url()));
    return route.fulfill({ json: state() });
  });
  return reads;
}

test('review lazily reads association revision, uses exact EX03, and leaves published association unchanged', async ({ page }) => {
  const reads = await setup(page);
  let body: Record<string, unknown> | undefined;
  await page.route(`**${commandPath}`, route => {
    body = route.request().postDataJSON();
    return route.fulfill({ json: {
      association_id: associationId, decision: 'accept', revision: 8,
      audit_id: '00000000-0000-4000-8000-000000000191',
      effective: false, pending_topic_publication: true,
    } });
  });
  await page.goto(url);
  const published = page.getByRole('region', { name: '关联证券与题材' });
  await expect(published).toContainText('AI 建议 · 待审核');
  expect(reads).toHaveLength(0);
  await page.getByRole('button', { name: '复核消息关联', exact: true }).click();
  const review = page.getByRole('region', { name: '所有者关联复核' });
  await expect(review).toContainText('关联修订 7');
  expect(reads).toHaveLength(1);
  expect(Object.fromEntries(reads[0].searchParams)).toEqual({
    date: '2026-09-10', snapshot_id: NEWS_SNAPSHOT,
  });
  await review.getByRole('button', { name: '确认关联', exact: true }).click();
  await expect(review.getByRole('status')).toContainText('审核已记录');
  expect(Object.keys(body!).sort()).toEqual([
    'association_id', 'decision', 'expected_revision', 'idempotency_key',
  ]);
  expect(body).toMatchObject({
    association_id: associationId, decision: 'accept', expected_revision: 7,
  });
  expect(body!.idempotency_key).toEqual(expect.any(String));
  await expect(published).toContainText('AI 建议 · 待审核');
  await expect(review).toContainText('等待新题材快照发布');
  await expect(review).toContainText('审核决定：确认');
});

test('lost response retries the identical payload and idempotency key', async ({ page }) => {
  await setup(page);
  const commands: Record<string, unknown>[] = [];
  await page.route(`**${commandPath}`, route => {
    commands.push(route.request().postDataJSON());
    return commands.length === 1
      ? route.abort('failed')
      : route.fulfill({ json: {
        association_id: associationId, decision: 'reject', revision: 8,
        audit_id: '00000000-0000-4000-8000-000000000191',
        effective: false, pending_topic_publication: false,
      } });
  });
  await page.goto(url);
  await page.getByRole('button', { name: '复核消息关联', exact: true }).click();
  const review = page.getByRole('region', { name: '所有者关联复核' });
  await review.getByRole('button', { name: '驳回关联', exact: true }).click();
  await expect(review.getByRole('alert')).toBeVisible();
  await review.getByRole('button', { name: '使用原命令重试', exact: true }).click();
  await expect(review.getByRole('status')).toContainText('审核已记录');
  expect(commands).toHaveLength(2);
  expect(commands[1]).toEqual(commands[0]);
});

test('409 keeps decision and notice after reread until explicit reconfirm with a new key', async ({ page }) => {
  await setup(page);
  let conflicted = false;
  let reads = 0;
  const commands: Record<string, unknown>[] = [];
  await page.route(`**${statePath}?*`, route => {
    reads++;
    return route.fulfill({ json: state(conflicted ? 8 : 7, conflicted ? 'reject' : null) });
  });
  await page.route(`**${commandPath}`, route => {
    commands.push(route.request().postDataJSON());
    if (commands.length === 1) {
      conflicted = true;
      return route.fulfill({ status: 409, json: { code: 'VERSION_CONFLICT' } });
    }
    return route.fulfill({ json: {
      association_id: associationId, decision: 'accept', revision: 9,
      audit_id: '00000000-0000-4000-8000-000000000192',
      effective: false, pending_topic_publication: true,
    } });
  });
  await page.goto(url);
  await page.getByRole('button', { name: '复核消息关联', exact: true }).click();
  const review = page.getByRole('region', { name: '所有者关联复核' });
  await review.getByRole('button', { name: '确认关联', exact: true }).click();
  await expect(review.getByRole('alert')).toContainText('冲突');
  await expect(review).toContainText('待提交决定：确认');
  await expect(review.getByRole('button', { name: '使用原命令重试', exact: true })).toHaveCount(0);
  await review.getByRole('button', { name: '重新读取复核状态', exact: true }).click();
  await expect(review).toContainText('关联修订 8');
  await expect(review.getByRole('alert')).toContainText('冲突');
  expect(commands).toHaveLength(1);
  expect(reads).toBeGreaterThanOrEqual(2);
  await review.getByRole('button', { name: '重新确认决定', exact: true }).click();
  await expect(review.getByRole('status')).toContainText('审核已记录');
  expect(commands[1]).toMatchObject({ decision: 'accept', expected_revision: 8 });
  expect(commands[1].idempotency_key).not.toBe(commands[0].idempotency_key);
});

for (const mismatch of ['owner_id', 'snapshot_id', 'article_id', 'article_revision_id'] as const) {
  test(`review rejects ${mismatch} mismatch and never posts`, async ({ page }) => {
    await setup(page);
    let writes = 0;
    await page.route(`**${statePath}?*`, route => route.fulfill({
      json: { ...state(), [mismatch]: '00000000-0000-4000-8000-000000000199' },
    }));
    await page.route(`**${commandPath}`, route => {
      writes++;
      return route.fulfill({ status: 500 });
    });
    await page.goto(url);
    await page.getByRole('button', { name: '复核消息关联', exact: true }).click();
    const review = page.getByRole('region', { name: '所有者关联复核' });
    await expect(review.getByRole('alert')).toContainText('不一致');
    await expect(review.getByRole('button', { name: '确认关联', exact: true })).toBeDisabled();
    expect(writes).toBe(0);
  });
}

test('revoked source shows explicit refusal and clears review controls without mutating the PUB article', async ({ page }) => {
  await setup(page);
  let restored = false;
  await page.route(`**${statePath}?*`, route => route.fulfill({
    status: restored ? 200 : 403, json: restored ? state() : { code: 'FORBIDDEN' },
  }));
  const commands: unknown[] = [];
  await page.route(`**${commandPath}`, route => {
    commands.push(route.request().postDataJSON());
    return route.fulfill({ status: 500 });
  });
  await page.goto(url);
  const article = page.getByRole('region', { name: '消息详情' });
  await expect(article).toContainText(articleA.title);
  await expect(article).toContainText(articleA.revision_id);
  await page.getByRole('button', { name: '复核消息关联', exact: true }).click();
  const review = page.getByRole('region', { name: '所有者关联复核' });
  const refusal = page.getByRole('alert').filter({
    has: page.getByRole('heading', { name: '来源访问已被拒绝', exact: true }),
  });
  await expect(refusal).toBeVisible();
  await expect(refusal).toContainText('已清除所有受保护内容与内存中的来源数据。恢复授权后可重新请求。');
  await expect(review).toHaveCount(0);
  await expect(page.getByRole('button', { name: '确认关联', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '驳回关联', exact: true })).toHaveCount(0);
  await expect(article).toHaveCount(0);
  expect(commands).toEqual([]);
  restored = true;
  await refusal.getByRole('button', { name: '重新检查访问权限', exact: true }).click();
  await expect(article).toContainText(articleA.title);
  await expect(article).toContainText(articleA.revision_id);
  await expect(review).toHaveCount(0);
  await page.getByRole('button', { name: '复核消息关联', exact: true }).click();
  await expect(review).toContainText('关联修订 7');
  expect(commands).toEqual([]);
});

test('closing review drops a late read and restores keyboard focus', async ({ page }) => {
  await setup(page);
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  let entered!: () => void;
  const started = new Promise<void>(resolve => { entered = resolve; });
  await page.route(`**${statePath}?*`, async route => {
    entered();
    await pending;
    await route.fulfill({ json: state() }).catch(() => {});
  });
  await page.goto(url);
  const trigger = page.getByRole('button', { name: '复核消息关联', exact: true });
  await trigger.focus();
  await page.keyboard.press('Enter');
  await started;
  await page.getByRole('button', { name: '关闭关联复核', exact: true }).click();
  release();
  await expect(page.getByRole('region', { name: '所有者关联复核' })).toHaveCount(0);
  await expect(trigger).toBeFocused();
  await expect(page.getByRole('region', { name: '关联证券与题材' })).toContainText('待审核');
});

test('foreign association entry cannot authorize a command for the displayed association', async ({ page }) => {
  await setup(page);
  await page.route(`**${statePath}?*`, route => route.fulfill({ json: {
    ...state(),
    associations: [{ ...state().associations[0], association_id: articleA.securities[0].association_id }],
  } }));
  await page.goto(url);
  await page.getByRole('button', { name: '复核消息关联', exact: true }).click();
  const review = page.getByRole('region', { name: '所有者关联复核' });
  await expect(review.getByRole('button', { name: '确认关联', exact: true })).toBeDisabled();
});

if (process.env.DRAGON_IG08_READONLY === '1') test('read-only build has no review controls or private requests', async ({ page }) => {
  // Parent selects this test only for its separate read-only Reset build.
  await mockNews(page);
  const privateRequests: string[] = [];
  page.on('request', request => {
    if (request.url().includes('/associations/')) privateRequests.push(request.url());
  });
  await page.goto(url);
  await expect(page.getByRole('region', { name: '消息详情' })).toContainText(articleA.title);
  await expect(page.getByRole('button', { name: '复核消息关联', exact: true })).toHaveCount(0);
  expect(privateRequests).toEqual([]);
});

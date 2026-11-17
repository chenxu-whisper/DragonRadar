import { expect, test } from '@playwright/test';
import {
  ARTICLE_A,
  ARTICLE_B,
  mockNews,
  NEWS_SNAPSHOT,
  type NewsMockState,
} from '../fixtures/news';

const newsUrl = `/?view=news&snapshot=${NEWS_SNAPSHOT}&date=2026-09-10`;

test('renders fixed-snapshot list, detail, source/type/query filters and pager', async ({ page }) => {
  const requests: URL[] = [];
  page.on('request', request => {
    const url = new URL(request.url());
    if (url.pathname === '/api/news') requests.push(url);
  });
  await mockNews(page);
  await page.goto(newsUrl);

  await expect(page.getByRole('heading', { name: '消息', exact: true })).toBeVisible();
  const list = page.getByRole('region', { name: '消息列表' });
  await expect(list.getByRole('button', { name: /机器人公司签署/ })).toBeVisible();
  await expect(list.getByRole('button', { name: /行业跟踪/ })).toBeVisible();
  await expect(page.getByText('2 篇文章 · 1 个事件', { exact: true })).toBeVisible();

  await page.getByLabel('消息类型').selectOption('announcement');
  await expect(page).toHaveURL(/news_type=announcement/);
  await expect(list.getByRole('button', { name: /行业跟踪/ })).toHaveCount(0);
  await page.getByLabel('消息来源').selectOption('synthetic-wire');
  await expect(page).toHaveURL(/news_source=synthetic-wire/);
  await page.getByLabel('搜索消息').fill('机器人');
  await page.getByRole('button', { name: '搜索消息', exact: true }).click();
  await expect(page).toHaveURL(/q=%E6%9C%BA%E5%99%A8%E4%BA%BA/);

  await list.getByRole('button', { name: /机器人公司签署/ }).click();
  await expect(page).toHaveURL(new RegExp(`article=${ARTICLE_A}`));
  const detail = page.getByRole('region', { name: '消息详情' });
  await expect(detail.getByRole('heading', { name: '机器人公司签署合成测试订单' })).toBeVisible();
  await expect(detail.getByText('仅获摘要展示许可的合成消息。')).toBeVisible();
  await expect(detail.getByRole('link', { name: '查看原文' })).toHaveAttribute('href', /example\.test/);
  await expect(detail.getByText('全文解读完成')).toHaveCount(0);
  await expect(detail.getByText('AI 摘要：')).toHaveCount(0);
  await expect(detail.getByText('未获正文展示许可', { exact: true })).toBeVisible();
  await expect(detail.getByRole('heading', { name: '事件新颖性：新颖事件' })).toBeVisible();
  await expect(detail.getByText('首次披露合成测试订单。')).toBeVisible();
  await expect(detail.getByText('订单仍需满足交割条件。')).toBeVisible();
  await expect(detail.getByRole('button', { name: /确认|驳回/ })).toHaveCount(0);

  const latest = requests.at(-1)!;
  expect(latest.searchParams.get('snapshot_id')).toBe(NEWS_SNAPSHOT);
  expect(latest.searchParams.get('date')).toBe('2026-09-10');
  expect(latest.searchParams.get('type')).toBe('announcement');
  expect(latest.searchParams.get('source')).toBe('synthetic-wire');
  expect(latest.searchParams.get('q')).toBe('机器人');
  expect(latest.searchParams.get('page')).toBe('1');
  expect(latest.searchParams.get('size')).toBe('50');
});

test('unlicensed article still shows title and source link with explicit limitation', async ({ page }) => {
  await mockNews(page);
  await page.goto(`${newsUrl}&article=${ARTICLE_B}`);

  const detail = page.getByRole('region', { name: '消息详情' });
  await expect(detail.getByRole('heading', { name: /行业跟踪/ })).toBeVisible();
  await expect(detail.getByRole('link', { name: '查看原文' })).toBeVisible();
  await expect(detail.getByText('未获正文展示许可', { exact: true })).toBeVisible();
  await expect(detail.getByText('全文解读完成')).toHaveCount(0);
});

test('article security uses the single shared drawer and returns with news state intact', async ({ page }) => {
  await mockNews(page);
  await page.goto(`${newsUrl}&article=${ARTICLE_A}&news_type=announcement&news_source=synthetic-wire&q=robot&page=2`);

  await page.getByRole('region', { name: '消息详情' })
    .getByRole('button', { name: '合成测试证券1' }).click();
  await expect(page.getByRole('dialog')).toHaveCount(1);
  await expect(page.getByRole('dialog').getByRole('heading', { name: '合成测试证券1' })).toBeVisible();
  await page.getByRole('dialog').getByRole('link', { name: '打开完整详情' }).click();
  await expect(page).toHaveURL(/view=stock/);
  await expect(page).toHaveURL(/return_view=news/);

  await page.getByRole('link', { name: '返回研究列表' }).click();
  await expect(page).toHaveURL(/view=news/);
  await expect(page).toHaveURL(new RegExp(`article=${ARTICLE_A}`));
  await expect(page).toHaveURL(/news_type=announcement/);
  await expect(page).toHaveURL(/news_source=synthetic-wire/);
  await expect(page).toHaveURL(/q=robot/);
  await expect(page).toHaveURL(/page=2/);
});

test('security detail links back to associated news without losing snapshot context', async ({ page }) => {
  await mockNews(page);
  await page.goto(`/?view=stock&snapshot=${NEWS_SNAPSHOT}&date=2026-09-10&security=TEST001&return_view=news&article=${ARTICLE_A}&news_type=announcement`);

  const related = page.getByRole('region', { name: '关联消息' });
  await expect(related.getByRole('link', { name: '机器人公司签署合成测试订单' })).toBeVisible();
  await related.getByRole('link', { name: '机器人公司签署合成测试订单' }).click();
  await expect(page).toHaveURL(/view=news/);
  await expect(page).toHaveURL(new RegExp(`article=${ARTICLE_A}`));
  await expect(page).toHaveURL(new RegExp(`snapshot=${NEWS_SNAPSHOT}`));
  await expect(page).toHaveURL(/news_type=announcement/);
});

test('malformed or mismatched responses clear stale article data', async ({ page }) => {
  const state: NewsMockState = {};
  await mockNews(page, state);
  await page.goto(`${newsUrl}&article=${ARTICLE_A}`);
  await expect(page.getByRole('heading', { name: '机器人公司签署合成测试订单' })).toBeVisible();

  state.wrongArticle = true;
  await page.getByRole('button', { name: '刷新快照' }).click();
  await expect(page.getByRole('alert')).toContainText('响应消息与当前选择不匹配');
  await expect(page.getByRole('heading', { name: '机器人公司签署合成测试订单' })).toHaveCount(0);

  state.wrongArticle = false;
  state.malformed = true;
  await page.getByRole('button', { name: '刷新快照' }).click();
  await expect(page.getByRole('alert')).toContainText('消息列表响应格式无效');
  await expect(page.getByText('stale malformed news')).toHaveCount(0);
});

test('desktop uses two columns and 375px reflows without horizontal overflow', async ({ page }) => {
  await mockNews(page);
  await page.goto(`${newsUrl}&article=${ARTICLE_B}`);

  await expect(page.locator('.news-layout')).toHaveCSS('grid-template-columns', /.+ .+/);
  await page.setViewportSize({ width: 375, height: 812 });
  const dimensions = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    page: document.documentElement.scrollWidth,
  }));
  expect(dimensions.page).toBeLessThanOrEqual(dimensions.viewport);
  await expect(page.locator('.news-layout')).toHaveCSS('grid-template-columns', /355px|343px|1fr/);
  await expect(page.getByRole('heading', { name: /同一事件的第二篇报道/ })).toBeVisible();
});

import { expect, test, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

import { mockNews, NEWS_SNAPSHOT } from '../fixtures/news';

const newsUrl = `/?view=news&snapshot=${NEWS_SNAPSHOT}&date=2026-09-10`;

function trackWordcloudRequests(page: Page): URL[] {
  const requests: URL[] = [];
  page.on('request', request => {
    const url = new URL(request.url());
    if (url.pathname === '/api/news/wordcloud') requests.push(url);
  });
  return requests;
}

test('renders the licensed word cloud with provenance, decorative svg and the accessible ledger', async ({ page }) => {
  const cloudRequests = trackWordcloudRequests(page);
  const consoleErrors: string[] = [];
  page.on('console', message => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });

  await mockNews(page);
  await page.goto(newsUrl);

  // 词云面板（region）在获许可数据返回后可见
  const panel = page.getByRole('region', { name: '新闻词云' });
  await expect(panel).toBeVisible();

  // 溯源行：tokenizer 版本 + 三类许可文本计数 + 词总数
  const meta = panel.locator('.wordcloud-meta');
  await expect(meta).toContainText('wordcloud-tokenizer-v1');
  await expect(meta).toContainText('标题 3');
  await expect(meta).toContainText('摘要 2');
  await expect(meta).toContainText('正文 0');
  await expect(meta).toContainText('共 5 个词');

  // 装饰 svg：每个词渲染一个 text，整体 aria-hidden 不进辅助树
  await expect(panel.locator('.wordcloud-svg text')).toHaveCount(5);

  // 可交互账本：5 个词条按钮，可访问名由显式 aria-label「词条，N 篇」提供，
  // 不依赖 flex 布局在词与计数之间产生的边界
  const terms = panel.locator('ul.wordcloud-words button.wordcloud-word');
  await expect(terms).toHaveCount(5);
  await expect(panel.getByRole('button', { name: '机器人，2 篇' })).toBeVisible();

  // DOM 顺序即归档契约：count 降序，同频按 Unicode 码位升序
  // （机 U+673A < 订 U+8BA2；r U+0072 < 涨 U+6DA8 < 行 U+884C）
  const termOrder = await terms.evaluateAll(nodes =>
    nodes.map(node => node.getAttribute('data-term')));
  expect(termOrder).toEqual(['机器人', '订单', 'robot', '涨停', '行业']);

  // 首个词云请求：默认 top_n=40、类型回显 all、快照与日期绑定
  expect(cloudRequests.length).toBeGreaterThan(0);
  const first = cloudRequests[0];
  expect(first.searchParams.get('top_n')).toBe('40');
  expect(first.searchParams.get('type')).toBe('all');
  expect(first.searchParams.get('snapshot_id')).toBe(NEWS_SNAPSHOT);
  expect(first.searchParams.get('date')).toBe('2026-09-10');

  // 无日期渲染事故，且不泄露后端已禁用、前端不渲染的情感字段
  const body = page.locator('body');
  await expect(body).not.toContainText('Invalid Date');
  await expect(body).not.toContainText('情感');
  await expect(body).not.toContainText('正面');
  await expect(body).not.toContainText('负面');

  expect(consoleErrors).toEqual([]);
});

test('selects a term with the keyboard, filters the title/summary list and toggles off', async ({ page }) => {
  await mockNews(page);
  await page.goto(newsUrl);

  const panel = page.getByRole('region', { name: '新闻词云' });
  await expect(panel).toBeVisible();
  const term = panel.getByRole('button', { name: '机器人，2 篇' });

  // 键盘 Enter 激活词条，效果与点击一致：aria-pressed 翻转
  await term.focus();
  await expect(term).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(term).toHaveAttribute('aria-pressed', 'true');

  // URL（UTF-8 百分号编码）与搜索框都同步为该词条
  await expect(page).toHaveURL(/q=%E6%9C%BA%E5%99%A8%E4%BA%BA/);
  await expect(page.getByLabel('搜索消息')).toHaveValue('机器人');

  // 列表按“标题 + 摘要”过滤：只剩标题含“机器人”的公告行，
  // 另一篇研报行（行业跟踪，ARTICLE_B）消失；两篇共享同一事件故事件数仍为 1
  const list = page.getByRole('region', { name: '消息列表' });
  await expect(list.getByRole('button', { name: /机器人公司签署/ })).toBeVisible();
  await expect(list.getByRole('button', { name: /行业跟踪/ })).toHaveCount(0);
  await expect(page.getByText('1 篇文章 · 1 个事件', { exact: true })).toBeVisible();

  // 按钮仍持有焦点，Space 再次激活同一词条即取消：URL 去 q、列表复原
  await expect(term).toBeFocused();
  await page.keyboard.press('Space');
  await expect(term).toHaveAttribute('aria-pressed', 'false');
  await expect(page).not.toHaveURL(/q=/);
  await expect(page.getByText('2 篇文章 · 1 个事件', { exact: true })).toBeVisible();
});

test('refetches by group when news type changes', async ({ page }) => {
  const cloudRequests = trackWordcloudRequests(page);
  await mockNews(page);
  await page.goto(newsUrl);

  const panel = page.getByRole('region', { name: '新闻词云' });
  await expect(panel).toBeVisible();

  // 切到公告：词云按 type=announcement 重新请求，面板只剩公告许可词
  await page.getByLabel('消息类型').selectOption('announcement');
  await expect
    .poll(() => cloudRequests.at(-1)?.searchParams.get('type'))
    .toBe('announcement');
  await expect(panel.getByRole('button', { name: '订单，1 篇' })).toBeVisible();
  await expect(panel.locator('.wordcloud-meta')).toContainText('共 1 个词');
  await expect(panel.getByRole('button', { name: '机器人，2 篇' })).toHaveCount(0);

  // 切到新闻：词云按 type=news 重新请求，面板换成新闻许可词
  await page.getByLabel('消息类型').selectOption('news');
  await expect
    .poll(() => cloudRequests.at(-1)?.searchParams.get('type'))
    .toBe('news');
  await expect(panel.getByRole('button', { name: '机器人，1 篇' })).toBeVisible();
  await expect(panel.getByRole('button', { name: '订单，1 篇' })).toHaveCount(0);

  // 选回全部类型：词云重新请求 type=all，面板恢复 5 个词
  await page.getByLabel('消息类型').selectOption('');
  await expect
    .poll(() => cloudRequests.at(-1)?.searchParams.get('type'))
    .toBe('all');
  await expect(panel.locator('.wordcloud-meta')).toContainText('共 5 个词');
  await expect(panel.getByRole('button', { name: '机器人，2 篇' })).toBeVisible();
});

test('renders the zero-licensed-text empty state for a group without words', async ({ page }) => {
  const cloudRequests = trackWordcloudRequests(page);
  await mockNews(page);
  await page.goto(newsUrl);

  const panel = page.getByRole('region', { name: '新闻词云' });
  await expect(panel).toBeVisible();

  // 研报分组无获许可文本（text_scope 全 0）：显示空态且不渲染词云 section
  await page.getByLabel('消息类型').selectOption('research');
  await expect
    .poll(() => cloudRequests.at(-1)?.searchParams.get('type'))
    .toBe('research');
  await expect(page.getByText('当前范围没有获许可文本可生成词语。')).toBeVisible();
  await expect(page.locator('.wordcloud-panel')).toHaveCount(0);
});

test('renders the no-publication empty state', async ({ page }) => {
  // data=null：快照存在但该范围没有任何可发布词云
  await mockNews(page, { wordcloud: 'null' });
  await page.goto(newsUrl);

  await expect(page.getByText('当前快照暂无可发布的词云数据。')).toBeVisible();
  await expect(page.locator('.wordcloud-panel')).toHaveCount(0);
});

test('rejects a malformed word cloud block without stale content', async ({ page }) => {
  // 坏块声明 2 篇文档却只给 1 个 article_id，fail-closed 校验必须拒绝
  await mockNews(page, { wordcloud: 'malformed' });
  await page.goto(newsUrl);

  await expect(page.getByRole('alert')).toContainText('词云响应格式无效');
  await expect(page.locator('.wordcloud-panel')).toHaveCount(0);
  await expect(page.locator('body')).not.toContainText('坏块');
});

test('is axe-clean without horizontal overflow at 375px', async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on('console', message => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });
  await page.setViewportSize({ width: 375, height: 900 });
  await page.emulateMedia({ reducedMotion: 'reduce' });

  await mockNews(page);
  await page.goto(newsUrl);

  const panel = page.getByRole('region', { name: '新闻词云' });
  await expect(panel).toBeVisible();

  // 面板级扫描：词云区域自身无违规
  const panelAxe = await new AxeBuilder({ page }).include('[aria-label="新闻词云"]').analyze();
  expect(panelAxe.violations).toEqual([]);

  // 整页扫描：标题层级（h1 消息 → h2 词云，不得跳级）是面板 include 扫描
  // 测不到的页面级契约，作为 h3→h2 修复的回归护栏
  const pageAxe = await new AxeBuilder({ page }).analyze();
  expect(pageAxe.violations).toEqual([]);

  // 页面与词云面板均不得横向溢出（面板按自身盒宽判定，不借用视口宽度）
  const metrics = await page.evaluate(() => {
    const node = document.querySelector('.wordcloud-panel') as HTMLElement | null;
    return {
      pageFits: document.documentElement.scrollWidth <= document.documentElement.clientWidth,
      panelFits: node ? node.scrollWidth <= node.clientWidth : false,
    };
  });
  expect(metrics).toEqual({ pageFits: true, panelFits: true });

  expect(consoleErrors).toEqual([]);
});

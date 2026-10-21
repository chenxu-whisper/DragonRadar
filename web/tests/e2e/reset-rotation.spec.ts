import { expect, test, type Page, type Locator } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

import { ID, OLD, ANCHOR_DATE, SECTOR_A, SECTOR_B, mockRotation } from '../fixtures/rotation';

const leadersUrl = `/?view=leaders&snapshot=${ID}&date=${ANCHOR_DATE}&role=trend_leader`;

function trackRotationRequests(page: Page): URL[] {
  const requests: URL[] = [];
  page.on('request', request => {
    const url = new URL(request.url());
    if (url.pathname === '/api/topics/rotation') requests.push(url);
  });
  return requests;
}

function trackEvidenceRequests(page: Page): URL[] {
  const requests: URL[] = [];
  page.on('request', request => {
    const url = new URL(request.url());
    if (url.pathname === '/api/role/evidence') requests.push(url);
  });
  return requests;
}

const rotationSection = (page: Page): Locator => page.locator('section.rotation-section');

function lane(page: Page, sector: string): Locator {
  return rotationSection(page).locator('.rotation-lane-block').filter({
    has: page.locator('h3.rotation-sector', { hasText: sector }),
  });
}

function nodeAt(page: Page, sector: string, at: string): Locator {
  return lane(page, sector).locator('li.rotation-node').filter({
    has: page.locator(`time[datetime="${at}"]`),
  });
}

const evidenceRegion = (page: Page, at: string, sector: string, security: string): Locator =>
  page.locator(`#rotation-evidence-${at}-${sector}-${security}`);

const coreButton = (page: Page, security: string, at: string): Locator =>
  rotationSection(page).getByRole('button', {
    name: `查看 ${security} 于 ${at} 的角色证据`,
    exact: true,
  });

test('renders the deterministic two-sector rotation timeline pinned to serving snapshots', async ({ page }) => {
  const rotationRequests = trackRotationRequests(page);
  const consoleErrors: string[] = [];
  page.on('console', message => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });

  await mockRotation(page);
  await page.goto(leadersUrl);

  const section = rotationSection(page);
  await expect(section.getByRole('heading', { name: '题材轮动时间轴', level: 2 })).toBeVisible();

  // 窗口选择器：默认 30 个交易日，仅四个固定档位
  const daysSelect = section.getByLabel('轮动窗口交易日数');
  await expect(daysSelect).toHaveValue('30');
  const dayOptions = await daysSelect.locator('option').evaluateAll(
    options => options.map(option => option.getAttribute('value')),
  );
  expect(dayOptions).toEqual(['10', '30', '60', '120']);

  // 题材泳道按词典序固定为 SEC001 / SEC002
  const sectorHeadings = await section.locator('h3.rotation-sector').evaluateAll(
    nodes => nodes.map(node => node.textContent),
  );
  expect(sectorHeadings).toEqual([SECTOR_A, SECTOR_B]);
  await expect(lane(page, SECTOR_A).locator('li.rotation-node')).toHaveCount(4);
  await expect(lane(page, SECTOR_B).locator('li.rotation-node')).toHaveCount(3);

  // 七个节点都带机器可读时间戳
  await expect(section.locator('time[datetime]')).toHaveCount(7);

  // 五种变化文案及次数由七事件确定性决定（新观察段/核心未变各 2，其余各 1）
  const changeCounts: ReadonlyArray<readonly [string, number]> = [
    ['新观察段', 2],
    ['核心未变', 2],
    ['核心扩大', 1],
    ['核心缩小', 1],
    ['核心重组', 1],
  ];
  for (const [label, count] of changeCounts) {
    await expect(section.getByText(label, { exact: true })).toHaveCount(count);
  }

  // 可比 / 不可比徽章：5 个可比节点，2 个新观察段
  await expect(section.getByText('可比', { exact: true })).toHaveCount(5);
  await expect(section.getByText('不可比 · 新观察段', { exact: true })).toHaveCount(2);

  // 冻结原因 token 只渲染中文文案
  await expect(nodeAt(page, SECTOR_A, '2026-09-08').locator('p.rotation-reason'))
    .toHaveText('首次观察');
  await expect(nodeAt(page, SECTOR_B, '2026-09-09').locator('p.rotation-reason'))
    .toHaveText('规则版本已变化');

  // 无核心候选节点只展示说明，不提供可点击核心
  const emptyCoreNode = nodeAt(page, SECTOR_B, ANCHOR_DATE);
  await expect(emptyCoreNode.locator('span.rotation-no-core')).toHaveText('无核心候选');
  await expect(emptyCoreNode.locator('button')).toHaveCount(0);

  // 窗口行：30 个自然日窗口（含锚点当日）
  await expect(section.locator('p.rotation-meta'))
    .toContainText('窗口 2026-08-12–2026-09-10');

  // 泳道区域不得泄露任何后端原始 token
  const laneText = await section.locator('.rotation-lane-block').evaluateAll(
    blocks => blocks.map(block => (block as HTMLElement).innerText).join('\n'),
  );
  for (const token of ['first_observation', 'no_core_candidate', 'segment_start', 'rule_version_changed']) {
    expect(laneText).not.toContain(token);
  }

  // 首个请求：days=30、锚点日期/快照、不带 sector
  expect(rotationRequests.length).toBeGreaterThan(0);
  const first = rotationRequests[0];
  expect(first.searchParams.get('days')).toBe('30');
  expect(first.searchParams.get('date')).toBe(ANCHOR_DATE);
  expect(first.searchParams.get('snapshot_id')).toBe(ID);
  expect(first.searchParams.get('sector')).toBeNull();

  // 节点按钮钉在各自发布快照上：锚点用当前发布 ID，09-09 用历史发布 OLD
  await expect(coreButton(page, 'TEST002', ANCHOR_DATE))
    .toHaveAttribute('data-snapshot', ID);
  await expect(coreButton(page, 'TEST001', '2026-09-09'))
    .toHaveAttribute('data-snapshot', OLD);

  expect(consoleErrors).toEqual([]);
});

test('refetches and re-paginates the window when the day selector changes', async ({ page }) => {
  const rotationRequests = trackRotationRequests(page);
  await mockRotation(page);
  await page.goto(leadersUrl);

  const section = rotationSection(page);
  const meta = section.locator('p.rotation-meta');
  await expect(section.locator('time[datetime="2026-08-20"]')).toBeVisible();

  await section.getByLabel('轮动窗口交易日数').selectOption('10');
  await expect
    .poll(() => rotationRequests.at(-1)?.searchParams.get('days'))
    .toBe('10');
  await expect(meta).toContainText('窗口 2026-09-01–2026-09-10');
  await expect(lane(page, SECTOR_A).locator('li.rotation-node')).toHaveCount(3);
  await expect(section.locator('time[datetime="2026-08-20"]')).toHaveCount(0);

  await section.getByLabel('轮动窗口交易日数').selectOption('60');
  await expect
    .poll(() => rotationRequests.at(-1)?.searchParams.get('days'))
    .toBe('60');
  await expect(section.locator('time[datetime="2026-08-20"]')).toBeVisible();

  await section.getByLabel('轮动窗口交易日数').selectOption('30');
  await expect
    .poll(() => rotationRequests.at(-1)?.searchParams.get('days'))
    .toBe('30');

  // 最大固定档位 120 同样触发重新请求与重分页：七个事件全部落在窗口内
  await section.getByLabel('轮动窗口交易日数').selectOption('120');
  await expect
    .poll(() => rotationRequests.at(-1)?.searchParams.get('days'))
    .toBe('120');
  await expect(meta).toContainText('窗口 2026-05-14–2026-09-10');
  await expect(lane(page, SECTOR_A).locator('li.rotation-node')).toHaveCount(4);
  await expect(lane(page, SECTOR_B).locator('li.rotation-node')).toHaveCount(3);
});

test('filters lanes by sector and restores both lanes when the filter is cleared', async ({ page }) => {
  const rotationRequests = trackRotationRequests(page);
  const sectorUrl = `/?view=leaders&snapshot=${ID}&date=${ANCHOR_DATE}&role=trend_leader&sector=${SECTOR_A}`;
  await mockRotation(page);
  await page.goto(sectorUrl);

  // 等待 lazy 区块挂载并发出首请求（goto 的 load 可能早于动态 chunk）
  const section = rotationSection(page);
  await expect(section.getByRole('heading', { name: '题材轮动时间轴', level: 2 })).toBeVisible();
  expect(rotationRequests.length).toBeGreaterThan(0);
  expect(rotationRequests[0].searchParams.get('sector')).toBe(SECTOR_A);
  await expect(section.locator('h3.rotation-sector')).toHaveText([SECTOR_A]);

  await page.getByLabel('题材筛选').selectOption('');
  await expect
    .poll(() => rotationRequests.at(-1)?.searchParams.get('sector'))
    .toBeNull();
  await expect(section.locator('h3.rotation-sector')).toHaveText([SECTOR_A, SECTOR_B]);
});

test('opens historical evidence pinned to the serving snapshot and switches to the anchor node', async ({ page }) => {
  const evidenceRequests = trackEvidenceRequests(page);
  await mockRotation(page);
  await page.goto(leadersUrl);

  // 强制跟踪断言 A：09-09 TEST001 的证据必须全部由历史快照 OLD 提供
  await coreButton(page, 'TEST001', '2026-09-09').click();
  const historicalRegion = evidenceRegion(page, '2026-09-09', SECTOR_A, 'TEST001');
  await expect(historicalRegion).toBeVisible();
  await expect(historicalRegion).toHaveAttribute('role', 'region');
  await expect(historicalRegion).toHaveAttribute('aria-label', 'TEST001 2026-09-09 角色证据');

  const historicalButton = coreButton(page, 'TEST001', '2026-09-09');
  await expect(historicalButton).toHaveAttribute('aria-pressed', 'true');
  await expect(historicalButton).toHaveAttribute(
    'aria-controls',
    'rotation-evidence-2026-09-09-SEC001-TEST001',
  );
  await expect(historicalRegion.getByRole('button', { name: '返回时间轴' })).toBeFocused();

  await expect(historicalRegion.getByRole('heading', { name: '六类角色证据', level: 3 })).toBeVisible();
  await expect(historicalRegion.getByRole('row', { name: /FAC-R01/ })).toBeVisible();

  await expect.poll(() => evidenceRequests.some(
    url => url.searchParams.get('security_id') === 'TEST001'
      && url.searchParams.get('date') === '2026-09-09'
      && url.searchParams.get('snapshot_id') === OLD
      && url.searchParams.get('sector') === SECTOR_A,
  )).toBe(true);
  const historical = evidenceRequests.filter(
    url => url.searchParams.get('security_id') === 'TEST001'
      && url.searchParams.get('date') === '2026-09-09',
  );
  expect(historical.length).toBeGreaterThan(0);
  for (const url of historical) {
    expect(url.searchParams.get('snapshot_id')).toBe(OLD);
    expect(url.searchParams.get('snapshot_id')).not.toBe(ID);
    expect(url.searchParams.get('sector')).toBe(SECTOR_A);
  }

  // 再开锚点 09-10 TEST002：旧 region 卸载，证据改由当前发布 ID 提供
  await coreButton(page, 'TEST002', ANCHOR_DATE).click();
  await expect(evidenceRegion(page, '2026-09-09', SECTOR_A, 'TEST001')).toHaveCount(0);
  const anchorRegion = evidenceRegion(page, ANCHOR_DATE, SECTOR_A, 'TEST002');
  await expect(anchorRegion).toBeVisible();
  await expect(anchorRegion.getByRole('heading', { name: '六类角色证据', level: 3 })).toBeVisible();
  await expect.poll(() => evidenceRequests.some(
    url => url.searchParams.get('security_id') === 'TEST002'
      && url.searchParams.get('date') === ANCHOR_DATE
      && url.searchParams.get('snapshot_id') === ID
      && url.searchParams.get('sector') === SECTOR_A,
  )).toBe(true);
});

test('clears the selected node on close, window change, sector change, in-app date change and anchor navigation', async ({ page }) => {
  const rotationRequests = trackRotationRequests(page);
  const evidenceRequests = trackEvidenceRequests(page);
  await mockRotation(page);
  await page.goto(leadersUrl);

  const section = rotationSection(page);
  const historicalButton = coreButton(page, 'TEST001', '2026-09-09');
  const regionId = 'rotation-evidence-2026-09-09-SEC001-TEST001';

  await historicalButton.click();
  await expect(page.locator(`#${regionId}`)).toBeVisible();
  await page.locator(`#${regionId}`).getByRole('button', { name: '返回时间轴' }).click();
  await expect(page.locator(`#${regionId}`)).toHaveCount(0);
  await expect(historicalButton).toHaveAttribute('aria-pressed', 'false');

  // 切窗口天数立即收起已展开的历史节点
  await historicalButton.click();
  await expect(page.locator(`#${regionId}`)).toBeVisible();
  await section.getByLabel('轮动窗口交易日数').selectOption('60');
  await expect(page.locator(`#${regionId}`)).toHaveCount(0);

  // 题材筛选变化同样收起，且轮动请求带上 sector
  await historicalButton.click();
  await expect(page.locator(`#${regionId}`)).toBeVisible();
  await page.getByLabel('题材筛选').selectOption(SECTOR_A);
  await expect(page.locator(`#${regionId}`)).toHaveCount(0);
  await expect
    .poll(() => rotationRequests.at(-1)?.searchParams.get('sector'))
    .toBe(SECTOR_A);

  // 整页导航到 09-09 / OLD：新页面无残留 region，窗口止于 09-09，无 09-10 节点
  await coreButton(page, 'TEST001', '2026-09-09').click();
  await expect(page.locator(`#${regionId}`)).toBeVisible();
  const oldUrl = `/?view=leaders&snapshot=${OLD}&date=2026-09-09&role=trend_leader`;
  await page.goto(oldUrl);
  await expect(page.locator(`#${regionId}`)).toHaveCount(0);
  await expect(section.locator('p.rotation-meta')).toContainText('窗口');
  await expect(section.locator('p.rotation-meta')).toContainText('–2026-09-09');
  await expect(section.locator('time[datetime="2026-09-10"]')).toHaveCount(0);

  await coreButton(page, 'TEST001', '2026-09-09').click();
  await expect(page.locator(`#${regionId}`)).toBeVisible();
  await expect.poll(() => evidenceRequests.some(
    url => url.searchParams.get('security_id') === 'TEST001'
      && url.searchParams.get('date') === '2026-09-09'
      && url.searchParams.get('snapshot_id') === OLD,
  )).toBe(true);

  // 应用内修改交易日期（同时清空待核验快照）不经过整页重载，也必须立即收起
  // 已展开的证据：覆盖清空 effect 的 date/snapshot 依赖。快照重新核验前
  // 轮动资源禁用，不得携带未核验日期发出请求。
  await page.locator('.leaders-controls').getByLabel('交易日期').fill('2026-09-08');
  await expect(page.locator(`#${regionId}`)).toHaveCount(0);
  expect(rotationRequests.filter(
    url => url.searchParams.get('date') === '2026-09-08',
  )).toHaveLength(0);
});

test('opens and closes the evidence region entirely from the keyboard', async ({ page }) => {
  await mockRotation(page);
  await page.goto(leadersUrl);

  const button = coreButton(page, 'TEST001', '2026-09-09');
  await button.focus();
  await page.keyboard.press('Enter');

  const region = evidenceRegion(page, '2026-09-09', SECTOR_A, 'TEST001');
  await expect(region).toBeVisible();
  await expect(region.getByRole('button', { name: '返回时间轴' })).toBeFocused();

  await page.keyboard.press('Enter');
  await expect(region).toHaveCount(0);
});

test('renders the no-publication empty state without lanes', async ({ page }) => {
  await mockRotation(page, { rotation: 'null' });
  await page.goto(leadersUrl);

  const section = rotationSection(page);
  await expect(section.getByText('当前快照暂无可发布的题材轮动数据。', { exact: true })).toBeVisible();
  await expect(section.locator('.rotation-lane')).toHaveCount(0);
  await expect(section.getByLabel('轮动窗口交易日数')).toBeVisible();
});

test('renders the empty-window state with the window line intact', async ({ page }) => {
  await mockRotation(page, { rotation: 'empty' });
  await page.goto(leadersUrl);

  const section = rotationSection(page);
  await expect(section.locator('p.rotation-meta')).toBeVisible();
  await expect(section.getByText('窗口内没有可展示的轮动记录。', { exact: true })).toBeVisible();
});

test('fail-closes a malformed rotation response without rendering lanes', async ({ page }) => {
  await mockRotation(page, { rotation: 'malformed' });
  await page.goto(leadersUrl);

  // RotationTimeline.tsx is the sole selector/copy authority: on a validation
  // failure it renders the alert from the feature root and does not mount
  // RotationTimelineView, so no lanes (or their stale content) remain.
  await expect(
    page.locator('main').getByRole('alert').filter({ hasText: '题材轮动响应格式无效' }),
  ).toBeVisible();
  await expect(page.locator('.rotation-lane')).toHaveCount(0);
});

test('isolates a lazy rotation chunk failure inside the section boundary', async ({ page }) => {
  await mockRotation(page);
  await page.route(/\/src\/features\/roles\/RotationTimeline\.[jt]sx/, route => route.abort('failed'));
  await page.goto(leadersUrl);

  const loadError = page.locator('main').getByRole('alert')
    .filter({ hasText: '题材轮动时间轴加载失败' });
  await expect(loadError).toBeVisible();
  await expect(loadError.getByRole('button', { name: '重新加载并重试' })).toBeVisible();

  await expect(page.locator('body')).not.toContainText('私人复盘');
  await expect(page.locator('body')).not.toContainText('私人功能');
  await expect(page.locator('main')).toHaveCount(1);
  await expect(page.getByRole('heading', { name: '梯队', level: 1 })).toBeVisible();
});

test('is axe-clean without horizontal overflow at 375px with evidence open', async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on('console', message => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });
  await page.setViewportSize({ width: 375, height: 812 });
  await page.emulateMedia({ reducedMotion: 'reduce' });

  await mockRotation(page);
  await page.goto(leadersUrl);

  await coreButton(page, 'TEST002', ANCHOR_DATE).click();
  const region = evidenceRegion(page, ANCHOR_DATE, SECTOR_A, 'TEST002');
  await expect(region).toBeVisible();
  await expect(region.getByRole('heading', { name: '六类角色证据', level: 3 })).toBeVisible();

  const sectionAxe = await new AxeBuilder({ page }).include('.rotation-section').analyze();
  expect(sectionAxe.violations).toEqual([]);
  const pageAxe = await new AxeBuilder({ page }).analyze();
  expect(pageAxe.violations).toEqual([]);

  const metrics = await page.evaluate(() => {
    const section = document.querySelector('.rotation-section') as HTMLElement | null;
    const evidence = document.querySelector('.rotation-evidence') as HTMLElement | null;
    return {
      pageFits: document.documentElement.scrollWidth <= document.documentElement.clientWidth,
      sectionFits: section ? section.scrollWidth <= section.clientWidth : false,
      evidenceFits: evidence ? evidence.scrollWidth <= evidence.clientWidth : false,
    };
  });
  expect(metrics).toEqual({ pageFits: true, sectionFits: true, evidenceFits: true });

  // 七个节点逐个做左右双缘 containment，右缘越界不能只靠滚动宽指标间接发现
  const nodeEdges = await rotationSection(page).locator('li.rotation-node').evaluateAll(
    nodes => nodes.map(node => {
      const rect = node.getBoundingClientRect();
      return { left: rect.left, right: rect.right };
    }),
  );
  expect(nodeEdges).toHaveLength(7);
  for (const edge of nodeEdges) {
    expect(edge.left).toBeGreaterThanOrEqual(0);
    expect(edge.right).toBeLessThanOrEqual(375);
  }

  expect(consoleErrors).toEqual([]);
});

import { mkdirSync, writeFileSync } from 'node:fs';
import { expect, test, type Page } from '@playwright/test';
import { ID, mockLeaders, mockMarket, resetSummary, sessions, snapshot } from '../fixtures/market';

const DATE = '2026-09-10';
const OVERVIEW_URL = `/?view=overview&snapshot=${ID}&date=${DATE}`;
const LEADERS_URL = `/?view=leaders&snapshot=${ID}&date=${DATE}`;
const REVIEW_URL = `/?view=review&snapshot=${ID}&date=${DATE}`;
const TRIGGER_NAME = '打开命令面板（Ctrl+K）';

function paletteDialog(page: Page) {
  return page.getByRole('dialog', { name: '命令面板' });
}
function paletteCombobox(page: Page) {
  return paletteDialog(page).getByRole('combobox', { name: '搜索命令或已加载结果' });
}
function paletteListbox(page: Page) {
  return page.getByRole('listbox', { name: '可用命令' });
}
function paletteOption(page: Page, name: string, exact = false) {
  return paletteListbox(page).getByRole('option', { name, exact });
}
function paletteGroup(page: Page, name: string) {
  return paletteListbox(page).getByRole('group', { name });
}
async function openPaletteViaTrigger(page: Page) {
  await page.getByRole('button', { name: TRIGGER_NAME }).click();
  await expect(paletteDialog(page)).toBeVisible();
  await expect(paletteCombobox(page)).toBeFocused();
}
async function openPaletteViaShortcut(page: Page) {
  await page.keyboard.press('Control+k');
  await expect(paletteDialog(page)).toBeVisible();
  await expect(paletteCombobox(page)).toBeFocused();
}
async function waitForOverview(page: Page) {
  await expect(page.getByRole('heading', { name: '市场总览', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '上涨 32' })).toBeVisible();
  await expect(page.getByRole('button', { name: '下跌 31' })).toBeVisible();
}
async function waitForLeaders(page: Page) {
  await expect(page.getByRole('heading', { name: '梯队', exact: true })).toBeVisible();
  await expect(page.getByRole('table', { name: '日期与板数矩阵' })).toBeVisible();
  await expect(page.getByRole('table', { name: '日期与板数矩阵' })
    .getByRole('button', { name: '合成测试证券1', exact: true })).toBeVisible();
}
async function noHorizontalOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
}

test.describe('主题偏好', () => {
  test('深色主题切换后落盘并在刷新后保留，URL、快照选择与业务数值不变，可再切回浅色', async ({ page }) => {
    await mockMarket(page);
    await page.goto(OVERVIEW_URL);
    await waitForOverview(page);

    const html = page.locator('html');
    const colorScheme = page.locator('meta[name="color-scheme"]');
    await expect(html).toHaveAttribute('data-theme', 'light');
    await expect(colorScheme).toHaveAttribute('content', 'light');
    const themeToggle = page.getByRole('button', { name: '切换到深色主题' });
    await expect(themeToggle).toHaveAttribute('aria-pressed', 'false');
    await expect(page.getByLabel('快照版本')).toHaveValue(ID);
    const urlBefore = page.url();

    await themeToggle.click();
    await expect(html).toHaveAttribute('data-theme', 'dark');
    await expect(colorScheme).toHaveAttribute('content', 'dark');
    await expect(page.getByRole('button', { name: '切换到浅色主题' })).toHaveAttribute('aria-pressed', 'true');
    expect(await page.evaluate(() => window.localStorage.getItem('dr:theme'))).toBe('dark');
    expect(page.url()).toBe(urlBefore);
    await expect(page.getByLabel('快照版本')).toHaveValue(ID);
    await expect(page.getByRole('button', { name: '上涨 32' })).toBeVisible();
    await expect(page.getByRole('button', { name: '下跌 31' })).toBeVisible();

    await page.reload();
    await waitForOverview(page);
    await expect(html).toHaveAttribute('data-theme', 'dark');
    await expect(colorScheme).toHaveAttribute('content', 'dark');
    expect(page.url()).toBe(urlBefore);
    await expect(page.getByLabel('快照版本')).toHaveValue(ID);
    await expect(page.getByRole('button', { name: '上涨 32' })).toBeVisible();
    await expect(page.getByRole('button', { name: '下跌 31' })).toBeVisible();

    await page.getByRole('button', { name: '切换到浅色主题' }).click();
    await expect(html).toHaveAttribute('data-theme', 'light');
    await expect(colorScheme).toHaveAttribute('content', 'light');
    expect(await page.evaluate(() => window.localStorage.getItem('dr:theme'))).toBe('light');
  });
});

test.describe('损坏偏好回退', () => {
  test('JSON 污染的主题值与非法密度值在首绘即回退浅色/舒适，坏值保留不被覆写', async ({ page }) => {
    await page.addInitScript(() => {
      window.localStorage.setItem('dr:theme', '"dark"');
      window.localStorage.setItem('dr:density', 'midnight');
    });
    await mockMarket(page);
    await page.goto(OVERVIEW_URL);
    await waitForOverview(page);

    const html = page.locator('html');
    await expect(html).toHaveAttribute('data-theme', 'light');
    await expect(html).toHaveAttribute('data-density', 'comfortable');
    await expect(page.locator('meta[name="color-scheme"]')).toHaveAttribute('content', 'light');
    await expect(page.getByRole('button', { name: '切换到深色主题' })).toHaveAttribute('aria-pressed', 'false');
    await expect(page.getByRole('button', { name: '切换为紧凑密度' })).toHaveAttribute('aria-pressed', 'false');
    // 只读回退：bootstrap 不清除存储中的坏值。
    expect(await page.evaluate(() => window.localStorage.getItem('dr:theme'))).toBe('"dark"');
    expect(await page.evaluate(() => window.localStorage.getItem('dr:density'))).toBe('midnight');
  });
});

test.describe('密度偏好', () => {
  test('表格行高在 36/44 之间切换且容差 ±1px，不改动查询/排序/比较，刷新后恢复', async ({ page }) => {
    // 梯队页依赖 /api/limit-up/** 与 /api/role/**，必须用 mockLeaders（其内部已包含 mockMarket）。
    await mockLeaders(page);
    await page.goto(`/?view=overview&snapshot=${ID}&date=${DATE}&q=TEST001&sort=close&order=desc&market_filter=up&compare=TEST001`);
    await waitForOverview(page);
    await expect(page.getByRole('region', { name: '证券筛选结果' })).toContainText('TEST001');
    await expect(page.getByRole('status').filter({ hasText: '已选 1 / 5 个标的' })).toBeVisible();
    const urlBefore = page.url();
    for (const param of ['q=TEST001', 'sort=close', 'order=desc', 'market_filter=up', 'compare=TEST001']) {
      expect(urlBefore).toContain(param);
    }
    await expect(page.locator('html')).toHaveAttribute('data-density', 'comfortable');

    await page.getByRole('button', { name: '切换为紧凑密度' }).click();
    await expect(page.locator('html')).toHaveAttribute('data-density', 'compact');
    expect(await page.evaluate(() => window.localStorage.getItem('dr:density'))).toBe('compact');
    expect(page.url()).toBe(urlBefore);
    await expect(page.getByRole('region', { name: '证券筛选结果' })).toContainText('TEST001');
    await expect(page.getByRole('status').filter({ hasText: '已选 1 / 5 个标的' })).toBeVisible();
    await expect(page.getByRole('button', { name: '上涨 32' })).toBeVisible();
    await expect(page.getByRole('button', { name: '下跌 31' })).toBeVisible();

    await page.getByRole('link', { name: '梯队', exact: true }).click();
    await expect(page).toHaveURL(/view=leaders/);
    await waitForLeaders(page);
    const compactHeight = await page.getByRole('table', { name: '日期与板数矩阵' })
      .locator('thead th', { hasText: '1板' })
      .evaluate(el => el.getBoundingClientRect().height);
    expect(Math.abs(compactHeight - 36)).toBeLessThanOrEqual(1);

    await page.reload();
    await waitForLeaders(page);
    await expect(page.locator('html')).toHaveAttribute('data-density', 'compact');
    const reloadedHeight = await page.getByRole('table', { name: '日期与板数矩阵' })
      .locator('thead th', { hasText: '1板' })
      .evaluate(el => el.getBoundingClientRect().height);
    expect(Math.abs(reloadedHeight - 36)).toBeLessThanOrEqual(1);

    await page.getByRole('button', { name: '切换为舒适密度' }).click();
    await expect(page.locator('html')).toHaveAttribute('data-density', 'comfortable');
    const comfortableHeight = await page.getByRole('table', { name: '日期与板数矩阵' })
      .locator('thead th', { hasText: '1板' })
      .evaluate(el => el.getBoundingClientRect().height);
    expect(Math.abs(comfortableHeight - 44)).toBeLessThanOrEqual(1);
  });

  test('375px 紧凑密度下关键控件命中区不小于 44px 且关键状态不被横向裁切', async ({ page }) => {
    await page.addInitScript(() => {
      window.localStorage.setItem('dr:density', 'compact');
    });
    await page.setViewportSize({ width: 375, height: 800 });
    await mockMarket(page);
    await page.goto(OVERVIEW_URL);
    await waitForOverview(page);
    await expect(page.locator('html')).toHaveAttribute('data-density', 'compact');
    // 偏好控件是懒加载 chunk：Suspense 占位只有等尺寸 span.theme-toggle，必须等真实按钮挂载再测量。
    await expect(page.locator('button.theme-toggle')).toBeVisible();
    await expect(page.locator('button.density-toggle')).toBeVisible();

    const measured = await page.evaluate(() => {
      const measure = (selector: string) => {
        const el = document.querySelector<HTMLElement>(selector);
        if (!el) throw new Error(`missing target ${selector}`);
        const rect = el.getBoundingClientRect();
        return { width: rect.width, height: rect.height, left: rect.left, right: rect.right };
      };
      return {
        viewport: window.innerWidth,
        boxes: {
          date: measure('#trade-date-input'),
          snapshot: measure('select[aria-label="快照版本"]'),
          refresh: measure('button[aria-label="刷新快照"]'),
          theme: measure('button.theme-toggle'),
          density: measure('button.density-toggle'),
          palette: measure('button.command-palette-trigger'),
        },
      };
    });
    for (const [name, box] of Object.entries(measured.boxes)) {
      expect(box.height, `${name} 高度`).toBeGreaterThanOrEqual(44);
      expect(box.width, `${name} 宽度`).toBeGreaterThanOrEqual(44);
      expect(box.left, `${name} 左界`).toBeGreaterThanOrEqual(0);
      expect(box.right, `${name} 右界`).toBeLessThanOrEqual(measured.viewport);
    }

    await page.getByRole('button', { name: '展开导航' }).click();
    const anchors = await page.evaluate(() => {
      return [...document.querySelectorAll<HTMLAnchorElement>('#primary-nav a')].map(a => {
        const rect = a.getBoundingClientRect();
        return { name: a.textContent?.trim() ?? '', height: rect.height, left: rect.left, right: rect.right };
      });
    });
    expect(anchors.length).toBe(6); // resetNavigation 固定 6 项：总览/消息/梯队/涨幅/监管/我的复盘（App.tsx）
    for (const anchor of anchors) {
      expect(anchor.height, `导航锚点 ${anchor.name} 高度`).toBeGreaterThanOrEqual(44);
      expect(anchor.left).toBeGreaterThanOrEqual(0);
      expect(anchor.right).toBeLessThanOrEqual(375);
    }

    await noHorizontalOverflow(page);
    const breadth = await page.evaluate(() => {
      const pick = (text: string) => {
        const el = [...document.querySelectorAll('button')].find(b => b.textContent?.replace(/\s+/g, '') === text);
        if (!el) throw new Error(`missing ${text}`);
        const rect = el.getBoundingClientRect();
        return { left: rect.left, right: rect.right, visible: rect.width > 0 && rect.height > 0 };
      };
      return { up: pick('上涨32'), down: pick('下跌31'), viewport: window.innerWidth };
    });
    expect(breadth.up.visible).toBe(true);
    expect(breadth.down.visible).toBe(true);
    expect(breadth.up.left).toBeGreaterThanOrEqual(0);
    expect(breadth.down.right).toBeLessThanOrEqual(breadth.viewport);
  });
});

test.describe('双主题对比度', () => {
  test('正文/次要文字/实底蓝按钮/焦点环在浅深两主题均达到 WCAG 门槛', async ({ page }) => {
    await mockMarket(page);
    await page.goto(`/?view=overview&snapshot=${ID}&date=${DATE}&compare=TEST001`);
    await waitForOverview(page);
    await expect(page.getByRole('status').filter({ hasText: '已选 1 / 5 个标的' })).toBeVisible();

    const collectSamples = () => page.evaluate(() => {
      type RGBA = { r: number; g: number; b: number; a: number };
      const parse = (raw: string): RGBA => {
        const match = raw.match(/rgba?\(([^)]+)\)/);
        if (!match) throw new Error(`无法解析颜色 ${raw}`);
        const parts = match[1].split(',').map(value => Number(value.trim()));
        return { r: parts[0], g: parts[1], b: parts[2], a: parts[3] ?? 1 };
      };
      const over = (top: RGBA, bottom: RGBA): RGBA => ({
        r: top.r * top.a + bottom.r * (1 - top.a),
        g: top.g * top.a + bottom.g * (1 - top.a),
        b: top.b * top.a + bottom.b * (1 - top.a),
        a: top.a + bottom.a * (1 - top.a),
      });
      type ChainStep = { tag: string; className: string; backgroundColor: string };
      // 沿父级链收集非透明背景层：最近祖先在最前；遇到不透明层即停止（其后的背景被完全遮挡）。
      // 注意不能以不透明白色作为种子再提前 break——元素自身透明时会在第一层就误退出，
      // 浅色下因种子白恰好等于表面色而被掩盖，深色下会把表面误判为白底。
      const backgroundOf = (element: Element) => {
        const chain: ChainStep[] = [];
        const layers: RGBA[] = [];
        let node: Element | null = element;
        while (node) {
          const raw = getComputedStyle(node).backgroundColor;
          chain.push({
            tag: node.tagName.toLowerCase(),
            className: typeof node.className === 'string' ? node.className : '',
            backgroundColor: raw,
          });
          const bg = parse(raw);
          if (bg.a !== 0) {
            layers.push(bg);
            if (bg.a === 1) break;
          }
          node = node.parentElement;
        }
        // 从最外层（链尾）向元素方向逐层 alpha 合成；整条链全透明时兜底为浏览器画布白底。
        let current: RGBA = { r: 255, g: 255, b: 255, a: 1 };
        for (let i = layers.length - 1; i >= 0; i -= 1) current = over(layers[i], current);
        return { bg: current, chain };
      };
      const channel = (value: number) => {
        const c = value / 255;
        return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
      };
      const luminance = (color: RGBA) => 0.2126 * channel(color.r)
        + 0.7152 * channel(color.g) + 0.0722 * channel(color.b);
      const ratio = (fg: RGBA, bg: RGBA) => {
        const lighter = Math.max(luminance(fg), luminance(bg));
        const darker = Math.min(luminance(fg), luminance(bg));
        return (lighter + 0.05) / (darker + 0.05);
      };
      type Sample = { fg: RGBA; bg: RGBA; fgRaw: string; bgRaw: string; chain: ChainStep[] };
      const sample = (selector: string): Sample => {
        const el = document.querySelector<HTMLElement>(selector);
        if (!el) throw new Error(`缺少采样元素 ${selector}`);
        const style = getComputedStyle(el);
        const { bg, chain } = backgroundOf(el);
        return {
          fg: parse(style.color),
          bg,
          fgRaw: style.color,
          bgRaw: getComputedStyle(el).backgroundColor,
          chain,
        };
      };
      const body = sample('body');
      const secondary = sample('.snapshot-meta');
      const primary = sample('.compare-shortcut button.primary');
      const focusEl = document.activeElement;
      if (!(focusEl instanceof HTMLElement)) throw new Error('焦点元素缺失');
      const focusStyle = getComputedStyle(focusEl);
      const focusBackground = backgroundOf(focusEl);
      const focus: Sample = {
        fg: parse(focusStyle.outlineColor),
        bg: focusBackground.bg,
        fgRaw: focusStyle.outlineColor,
        bgRaw: getComputedStyle(focusEl).backgroundColor,
        chain: focusBackground.chain,
      };
      const summarize = (item: Sample) => ({
        foreground: item.fgRaw,
        background: item.bgRaw,
        effectiveBackground: item.bg,
        ratio: Number(ratio(item.fg, item.bg).toFixed(2)),
        chain: item.chain,
      });
      return {
        body: summarize(body),
        secondary: summarize(secondary),
        primaryButton: summarize(primary),
        focusRing: summarize(focus),
      };
    });

    // 受信任的 Tab 让 skip-link 命中 :focus-visible，读到全局焦点环真实计算色。
    await page.keyboard.press('Tab');
    await expect(page.getByRole('link', { name: '跳至研究内容' })).toBeFocused();
    const light = await collectSamples();

    await page.evaluate(() => window.localStorage.setItem('dr:theme', 'dark'));
    await page.reload();
    await waitForOverview(page);
    await expect(page.getByRole('status').filter({ hasText: '已选 1 / 5 个标的' })).toBeVisible();
    await page.keyboard.press('Tab');
    await expect(page.getByRole('link', { name: '跳至研究内容' })).toBeFocused();
    const dark = await collectSamples();

    // 先落盘再断言：即使比值不合格也保留完整采样链证据。
    mkdirSync('.temp', { recursive: true });
    writeFileSync('.temp/slice4-contrast.json', JSON.stringify({ light, dark }, null, 2));

    expect(light.body.ratio).toBeGreaterThanOrEqual(4.5);
    expect(light.secondary.ratio).toBeGreaterThanOrEqual(4.5);
    expect(light.primaryButton.ratio).toBeGreaterThanOrEqual(4.5);
    expect(light.focusRing.ratio).toBeGreaterThanOrEqual(3);
    expect(dark.body.ratio).toBeGreaterThanOrEqual(4.5);
    expect(dark.secondary.ratio).toBeGreaterThanOrEqual(4.5);
    expect(dark.primaryButton.ratio).toBeGreaterThanOrEqual(4.5);
    expect(dark.focusRing.ratio).toBeGreaterThanOrEqual(3);
  });
});

test.describe('命令面板', () => {
  test('按钮与 Ctrl+K 均可打开面板，分组/静态命令/当前标记完整，Esc 与 Tab 后 Esc 都把焦点还给触发按钮', async ({ page }) => {
    await mockLeaders(page);
    await page.goto(LEADERS_URL);
    await waitForLeaders(page);

    await openPaletteViaTrigger(page);
    await expect(paletteListbox(page)).toBeVisible();
    await expect(paletteGroup(page, '页面')).toBeVisible();
    await expect(paletteGroup(page, '股票')).toBeVisible();
    await expect(paletteGroup(page, '题材')).toBeVisible();
    await expect(paletteGroup(page, '主题')).toBeVisible();
    await expect(paletteGroup(page, '密度')).toBeVisible();
    await expect(paletteGroup(page, '日期')).toBeVisible();
    await expect(paletteListbox(page).locator('.command-palette-group-label'))
      .toHaveText(['页面', '股票', '题材', '主题', '密度', '日期']);
    await expect(paletteGroup(page, '页面').getByRole('option'))
      .toHaveText(['总览', '我的复盘', '梯队', '消息', '涨幅', '监管']);
    for (const label of ['切换为浅色主题', '切换为深色主题', '切换为紧凑密度', '切换为舒适密度', '聚焦日期选择']) {
      await expect(paletteOption(page, label)).toBeVisible();
    }
    await expect(paletteOption(page, 'SEC001', true)).toBeVisible();
    await expect(paletteOption(page, '合成测试证券1', true)).toBeVisible();
    await expect(paletteOption(page, '合成测试证券2', true)).toBeVisible();
    await expect(paletteOption(page, '切换为浅色主题')).toHaveAttribute('aria-current', 'true');
    await expect(paletteOption(page, '切换为舒适密度')).toHaveAttribute('aria-current', 'true');
    expect(await paletteOption(page, '切换为深色主题').getAttribute('aria-current')).toBeNull();

    await page.keyboard.press('Escape');
    await expect(paletteDialog(page)).toHaveCount(0);
    await expect(page.getByRole('button', { name: TRIGGER_NAME })).toBeFocused();

    // 先把焦点放在触发按钮上再用快捷键打开：Tab 移出输入框后，window bubble 的 Esc 兜底仍能关闭并还焦。
    await page.getByRole('button', { name: TRIGGER_NAME }).focus();
    await page.keyboard.press('Control+k');
    await expect(paletteDialog(page)).toBeVisible();
    await expect(paletteCombobox(page)).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(paletteCombobox(page)).not.toBeFocused();
    await page.keyboard.press('Escape');
    await expect(paletteDialog(page)).toHaveCount(0);
    await expect(page.getByRole('button', { name: TRIGGER_NAME })).toBeFocused();
  });

  test('全键盘路径：方向键/Home/End 循环，Enter 执行主题后保持打开且更新当前标记，聚焦日期命令关闭并聚焦日期输入框', async ({ page }) => {
    await mockLeaders(page);
    await page.goto(LEADERS_URL);
    await waitForLeaders(page);
    await openPaletteViaShortcut(page);
    const combobox = paletteCombobox(page);

    const activeId = () => combobox.getAttribute('aria-activedescendant');
    const optionCount = await paletteListbox(page).getByRole('option').count();
    // mockLeaders 下：6 页面 + 2 证券（ladder cells/candidates 去重后 TEST001/TEST002）
    // + 1 题材（SEC001，RoleStatus 无 name 以 id 展示）+ 5 静态命令 = 14（fixtures/market.ts）
    expect(optionCount).toBe(14);
    expect(await activeId()).toBe('command-palette-option-0');
    await page.keyboard.press('ArrowDown');
    expect(await activeId()).toBe('command-palette-option-1');
    await page.keyboard.press('End');
    expect(await activeId()).toBe(`command-palette-option-${optionCount - 1}`);
    await page.keyboard.press('ArrowDown');
    expect(await activeId()).toBe('command-palette-option-0');
    await page.keyboard.press('ArrowUp');
    expect(await activeId()).toBe(`command-palette-option-${optionCount - 1}`);
    await page.keyboard.press('Home');
    expect(await activeId()).toBe('command-palette-option-0');

    await combobox.fill('深色主题');
    await expect(paletteListbox(page).getByRole('option')).toHaveCount(1);
    expect(await activeId()).toBe('command-palette-option-0');
    await page.keyboard.press('Enter');
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await expect(paletteDialog(page)).toBeVisible();
    await expect(paletteCombobox(page)).toBeFocused();
    await expect(paletteOption(page, '切换为深色主题')).toHaveAttribute('aria-current', 'true');
    await page.keyboard.press('Escape');
    await expect(paletteDialog(page)).toHaveCount(0);

    await openPaletteViaShortcut(page);
    await paletteCombobox(page).fill('聚焦日期');
    await page.keyboard.press('Enter');
    await expect(paletteDialog(page)).toHaveCount(0);
    expect(await page.evaluate(() => document.activeElement?.id)).toBe('trade-date-input');
  });

  test('无匹配时展示空态且 Enter 不触发任何动作', async ({ page }) => {
    await mockLeaders(page);
    await page.goto(LEADERS_URL);
    await waitForLeaders(page);
    await openPaletteViaTrigger(page);
    const urlBefore = page.url();
    await paletteCombobox(page).fill('zzzz-no-match-不可能命中');
    await expect(paletteListbox(page).locator('li.command-palette-empty'))
      .toHaveText('没有匹配的命令或结果');
    await expect(paletteListbox(page).getByRole('option')).toHaveCount(0);
    await page.keyboard.press('Enter');
    await expect(paletteDialog(page)).toBeVisible();
    expect(page.url()).toBe(urlBefore);
    await paletteCombobox(page).fill('');
    await expect(paletteListbox(page).getByRole('option')).not.toHaveCount(0);
  });

  test('IME 组合态：keyCode 229/isComposing 的 Ctrl+K 不开面板，组合中 Enter/Esc 放行，组合结束后恢复', async ({ page }) => {
    await mockLeaders(page);
    await page.goto(LEADERS_URL);
    await waitForLeaders(page);
    // 先聚焦触发按钮：面板挂载时记录的 trigger 用于关闭后还焦。
    await page.getByRole('button', { name: TRIGGER_NAME }).focus();

    // 组合态合成 Ctrl+K 被守卫拦截。
    await page.evaluate(() => {
      const event = new KeyboardEvent('keydown', { key: 'k', code: 'KeyK', ctrlKey: true, bubbles: true, cancelable: true });
      Object.defineProperty(event, 'keyCode', { get: () => 229 });
      Object.defineProperty(event, 'isComposing', { get: () => true });
      window.dispatchEvent(event);
    });
    await expect(paletteDialog(page)).toHaveCount(0);

    // 阳性对照：普通合成 Ctrl+K 仍能打开（证明事件链路有效，拦截确实来自 IME 判定）。
    await page.evaluate(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', code: 'KeyK', ctrlKey: true, bubbles: true, cancelable: true }));
    });
    await expect(paletteDialog(page)).toBeVisible();
    await expect(paletteCombobox(page)).toBeFocused();
    const activeBefore = await paletteCombobox(page).getAttribute('aria-activedescendant');
    const urlBefore = page.url();

    await page.evaluate(() => {
      const input = document.querySelector<HTMLInputElement>('.command-palette-input');
      if (!input) throw new Error('命令面板输入框缺失');
      input.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true }));
      const enter = new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', bubbles: true, cancelable: true });
      Object.defineProperty(enter, 'keyCode', { get: () => 13 });
      input.dispatchEvent(enter);
      const escape = new KeyboardEvent('keydown', { key: 'Escape', code: 'Escape', bubbles: true, cancelable: true });
      Object.defineProperty(escape, 'keyCode', { get: () => 27 });
      input.dispatchEvent(escape);
    });
    await expect(paletteDialog(page)).toBeVisible();
    expect(page.url()).toBe(urlBefore);
    expect(await paletteCombobox(page).getAttribute('aria-activedescendant')).toBe(activeBefore);

    await page.evaluate(() => {
      const input = document.querySelector<HTMLInputElement>('.command-palette-input');
      if (!input) throw new Error('命令面板输入框缺失');
      input.dispatchEvent(new CompositionEvent('compositionend', { bubbles: true }));
    });
    await page.keyboard.press('Escape');
    await expect(paletteDialog(page)).toHaveCount(0);
    await expect(page.getByRole('button', { name: TRIGGER_NAME })).toBeFocused();
  });

  test('导航类命令保留 snapshot/date：页面切换、证券进入详情、题材清空 security，执行后关闭并回焦', async ({ page }) => {
    await mockLeaders(page);
    await page.goto(LEADERS_URL);
    await waitForLeaders(page);

    await openPaletteViaTrigger(page);
    await paletteOption(page, '总览', true).click();
    await expect(paletteDialog(page)).toHaveCount(0);
    await expect(page.getByRole('button', { name: TRIGGER_NAME })).toBeFocused();
    await expect(page).toHaveURL(url => url.searchParams.get('view') === 'overview'
      && url.searchParams.get('snapshot') === ID
      && url.searchParams.get('date') === DATE);
    await waitForOverview(page);

    await openPaletteViaTrigger(page);
    await paletteCombobox(page).fill('TEST001');
    await expect(paletteOption(page, '合成测试证券1', true)).toBeVisible();
    await paletteOption(page, '合成测试证券1', true).click();
    await expect(paletteDialog(page)).toHaveCount(0);
    await expect(page.getByRole('button', { name: TRIGGER_NAME })).toBeFocused();
    await expect(page).toHaveURL(url => url.pathname === '/stock/TEST001'
      && url.searchParams.get('view') === 'stock'
      && url.searchParams.get('snapshot') === ID
      && url.searchParams.get('date') === DATE);
    await expect(page.getByRole('heading', { name: '证券详情' })).toBeVisible();

    await page.goto(LEADERS_URL);
    await waitForLeaders(page);
    await openPaletteViaTrigger(page);
    await paletteCombobox(page).fill('SEC001');
    await expect(paletteOption(page, 'SEC001', true)).toBeVisible();
    await paletteOption(page, 'SEC001', true).click();
    await expect(paletteDialog(page)).toHaveCount(0);
    await expect(page.getByRole('button', { name: TRIGGER_NAME })).toBeFocused();
    await expect(page).toHaveURL(url => url.searchParams.get('view') === 'leaders'
      && url.searchParams.get('sector') === 'SEC001'
      && url.searchParams.get('security') === null
      && url.searchParams.get('snapshot') === ID
      && url.searchParams.get('date') === DATE);
  });

  test('来源被拒时不渲染触发按钮，Ctrl+K 仍可打开但无复盘入口且股票/题材分组为空', async ({ page }) => {
    await mockMarket(page, { denied: true });
    await page.goto(OVERVIEW_URL);
    await expect(page.getByRole('alert').filter({ hasText: '来源访问已被拒绝' })).toBeVisible();
    await expect(page.getByRole('button', { name: '重新检查访问权限' })).toBeVisible();
    await expect(page.getByRole('button', { name: TRIGGER_NAME })).toHaveCount(0);

    await openPaletteViaShortcut(page);
    await expect(paletteOption(page, '我的复盘')).toHaveCount(0);
    await expect(paletteGroup(page, '股票')).toHaveCount(0);
    await expect(paletteGroup(page, '题材')).toHaveCount(0);
    await expect(paletteGroup(page, '页面').getByRole('option'))
      .toHaveText(['总览', '梯队', '消息', '涨幅', '监管']);
    await expect(paletteOption(page, '切换为深色主题')).toBeVisible();
    await expect(paletteOption(page, '切换为紧凑密度')).toBeVisible();
    await expect(paletteOption(page, '聚焦日期选择')).toBeVisible();
  });

  test('打开/过滤/键盘导航期间不发起 /api/ 请求，且 localStorage 只允许出现两个偏好键', async ({ page }) => {
    await mockLeaders(page);
    // 题材轮动时间轴是梯队矩阵之下的 lazy() 分区，且不在 mockLeaders 的拦截范围内。
    // 所有 reset spec 共享同一个 dev server 并以 workers=2 运行，忙时该懒 chunk 与其
    // /api/topics/rotation 请求可能晚于矩阵与主题按钮 settle，落入下面的零请求观察窗。
    // 监听器必须在 goto 前注册；404 与代理 502 都触发 requestfinished，abort 触发 failed。
    let rotationSettled = false;
    const markRotationSettled = (url: string) => {
      if (/\/api\/topics\/rotation/.test(url)) rotationSettled = true;
    };
    page.on('requestfinished', request => markRotationSettled(request.url()));
    page.on('requestfailed', request => markRotationSettled(request.url()));
    await page.goto(LEADERS_URL);
    await waitForLeaders(page);
    await expect.poll(() => rotationSettled, { timeout: 15000 }).toBe(true);
    // header 按钮可访问名为“切换到…”，面板内命令文案才是“切换为…”。
    // 不使用 networkidle（dev HMR 长连接耦合、Playwright 明确不推荐用于断言等待）。
    await page.getByRole('button', { name: '切换到深色主题' }).waitFor();
    await expect.poll(() => page.evaluate(() => Object.keys(window.localStorage))).toEqual([]);

    const apiRequests: string[] = [];
    page.on('request', request => {
      if (/\/api\//.test(request.url())) apiRequests.push(request.url());
    });

    await openPaletteViaTrigger(page);
    await expect(paletteOption(page, 'SEC001', true)).toBeVisible();
    await paletteCombobox(page).fill('SEC');
    await expect(paletteOption(page, 'SEC001', true)).toBeVisible();
    await paletteCombobox(page).fill('合成');
    await expect(paletteOption(page, '合成测试证券1', true)).toBeVisible();
    await page.keyboard.press('ArrowDown');
    await page.keyboard.press('ArrowDown');
    await page.keyboard.press('ArrowUp');
    await page.keyboard.press('Escape');
    await expect(paletteDialog(page)).toHaveCount(0);

    await openPaletteViaShortcut(page);
    await paletteCombobox(page).fill('日期');
    await expect(paletteOption(page, '聚焦日期选择')).toBeVisible();
    expect(apiRequests).toEqual([]);
    // 面板交互本身不隐式写存储；仅显式执行主题/密度命令后允许出现两个偏好键。
    expect(await page.evaluate(() => Object.keys(window.localStorage))).toEqual([]);
    await paletteCombobox(page).fill('');
    await paletteOption(page, '切换为深色主题').click();
    await paletteOption(page, '切换为紧凑密度').click();
    expect(await page.evaluate(() => Object.keys(window.localStorage).sort())).toEqual(['dr:density', 'dr:theme']);
    expect(apiRequests).toEqual([]);
  });
});

test.describe('脏草稿守卫', () => {
  test('本地复盘未保存时面板导航被拦截，显示内联提示且 URL 不变、面板保持打开', async ({ page }) => {
    await mockMarket(page);
    page.on('dialog', dialog => dialog.dismiss());
    await page.goto(REVIEW_URL);
    await expect(page.getByRole('heading', { name: '我的复盘', exact: true })).toBeVisible();
    await page.getByLabel('标题', { exact: true }).fill('未保存的脏草稿');

    await openPaletteViaTrigger(page);
    const urlBefore = page.url();
    await paletteOption(page, '总览', true).click();
    await expect(paletteDialog(page).locator('p.command-palette-alert'))
      .toHaveText('有未保存的修改，请先完成或放弃当前修改');
    expect(page.url()).toBe(urlBefore);
    await expect(paletteDialog(page)).toBeVisible();
  });
});

test.describe('视口与缩放', () => {
  for (const viewport of [{ width: 375, height: 800 }, { width: 1440, height: 900 }] as const) {
    test(`${viewport.width}×${viewport.height} 在 200% 缩放下面板开关均无横向溢出且交互可用`, async ({ page }) => {
      const pageErrors: Error[] = [];
      page.on('pageerror', error => pageErrors.push(error));
      const session = await page.context().newCDPSession(page);
      await session.send('Emulation.setDeviceMetricsOverride', {
        width: viewport.width,
        height: viewport.height,
        deviceScaleFactor: 2,
        mobile: false,
      });
      await mockMarket(page);
      await page.goto(OVERVIEW_URL);
      await waitForOverview(page);
      expect(await page.evaluate(() => ({
        width: window.innerWidth,
        height: window.innerHeight,
        dpr: window.devicePixelRatio,
      }))).toEqual({ width: viewport.width, height: viewport.height, dpr: 2 });
      await noHorizontalOverflow(page);

      await openPaletteViaShortcut(page);
      await paletteCombobox(page).fill('TEST001');
      await expect(paletteOption(page, '合成测试证券1', true)).toBeVisible();
      await noHorizontalOverflow(page);
      await page.keyboard.press('Escape');
      await expect(paletteDialog(page)).toHaveCount(0);
      await noHorizontalOverflow(page);
      expect(pageErrors).toEqual([]);
      await session.detach();
    });
  }
});

test.describe('减弱动效', () => {
  test('reduced-motion 下 K 线页面与命令面板交互无 pageerror', async ({ page }) => {
    const pageErrors: Error[] = [];
    page.on('pageerror', error => pageErrors.push(error));
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await mockMarket(page);
    // mockMarket 把 kline 路径末段当成证券 id 返回 404：在其后注册同名路由覆盖（后注册先匹配）。
    await page.route('**/api/stock/TEST001/kline?**', route => route.fulfill({
      json: {
        ...resetSummary(snapshot),
        basis: { unit: 'currency/share', currency: 'CNY', price_type: 'raw', benchmark: null },
        data: {
          security_id: 'TEST001',
          adjustment: 'raw',
          adjusted_start: sessions[0],
          adjusted_end: sessions.at(-1),
          chart_type: 'candlestick',
          points: sessions.map((trade_date, index) => ({
            trade_date,
            open: 10 + index,
            high: 12 + index,
            low: 9 + index,
            close: 11 + index,
            volume: 100,
            amount: 1000,
          })),
        },
      },
    }));
    await page.goto(`/stock/TEST001?snapshot=${ID}&date=${DATE}`);
    await expect(page.getByRole('heading', { name: '证券详情' })).toBeVisible();
    await expect.poll(async () => {
      const sizes = await page.evaluate(() => [...document.querySelectorAll('canvas')]
        .map(canvas => canvas.getBoundingClientRect())
        .filter(rect => rect.width > 100 && rect.height > 100).length);
      return sizes;
    }).toBeGreaterThanOrEqual(1);
    expect(await page.evaluate(() => window.matchMedia('(prefers-reduced-motion: reduce)').matches)).toBe(true);

    await openPaletteViaShortcut(page);
    await page.keyboard.press('ArrowDown');
    await page.keyboard.press('ArrowDown');
    await paletteCombobox(page).fill('聚焦日期');
    await page.keyboard.press('Enter');
    expect(await page.evaluate(() => document.activeElement?.id)).toBe('trade-date-input');
    await expect(paletteDialog(page)).toHaveCount(0);

    await openPaletteViaShortcut(page);
    await expect(paletteOption(page, '切换为深色主题')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(paletteDialog(page)).toHaveCount(0);
    expect(pageErrors).toEqual([]);
    const transition = await page.locator('button.command-palette-trigger')
      .evaluate(el => getComputedStyle(el).transitionDuration);
    expect(transition).toBe('0s');
  });
});

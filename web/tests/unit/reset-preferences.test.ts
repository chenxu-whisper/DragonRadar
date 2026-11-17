import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  DENSITY_KEY,
  THEME_KEY,
  applyPreferences,
  bootstrapPreferences,
  chartThemeColors,
  isPaletteShortcut,
  isTypingBlocked,
  parseStoredDensity,
  parseStoredTheme,
  readPreferences,
  systemTheme,
  writeDensity,
  writeTheme,
} from '../../src/features/preferences/settings';
import {
  GROUPS,
  INDEX_ITEMS_EVENT,
  bindIndexItems,
  buildCommands,
  executeCommand,
  filterCommands,
  reportIndexItems,
  type Command,
  type CommandContext,
  type SearchItem,
} from '../../src/features/preferences/commands';
import {
  mergeIndexItems,
  sanitizeIndexPayload,
  type SearchIndex,
} from '../../src/features/preferences/search-index';

const storageKey = (key: string) => (values: Record<string, string | null>) =>
  (k: string): string | null => (k === key ? values[key] ?? null : null);

describe('preferences settings', () => {
  it('exposes only the two dr: preference storage keys', () => {
    expect(THEME_KEY).toBe('dr:theme');
    expect(DENSITY_KEY).toBe('dr:density');
  });

  it('derives the system theme from the prefers-color-scheme signal', () => {
    expect(systemTheme(true)).toBe('dark');
    expect(systemTheme(false)).toBe('light');
  });

  it.each([
    ['light', 'dark', 'light'],
    ['dark', 'light', 'dark'],
  ])('explicit stored theme %s wins over fallback %s', (raw, fallback, expected) => {
    expect(parseStoredTheme(raw, fallback as 'light' | 'dark')).toBe(expected);
  });

  it.each([null, '', 'midnight', '"dark"', 'DARK', 'dark '])('falls back to system for corrupted theme %j', raw => {
    expect(parseStoredTheme(raw, 'light')).toBe('light');
    expect(parseStoredTheme(raw, 'dark')).toBe('dark');
  });

  it('follows the fallback theme when no explicit dr:theme exists', () => {
    const lightPrefs = readPreferences(storageKey(THEME_KEY)({}), 'light');
    expect(lightPrefs.theme).toBe('light');
    expect(lightPrefs.density).toBe('comfortable');
    const darkPrefs = readPreferences(storageKey(THEME_KEY)({}), 'dark');
    expect(darkPrefs.theme).toBe('dark');
  });

  it('reads explicit theme and density preferences', () => {
    const getItem = (key: string) =>
      key === THEME_KEY ? 'dark' : key === DENSITY_KEY ? 'compact' : null;
    expect(readPreferences(getItem, 'light')).toEqual({ theme: 'dark', density: 'compact' });
  });

  it.each([
    { theme: 'midnight', density: 'spacious' },
    { theme: '"dark"', density: '"compact"' },
    { theme: '', density: '' },
  ])('falls back safely on corrupted storage %j', values => {
    const getItem = (key: string) =>
      key === THEME_KEY ? values.theme : key === DENSITY_KEY ? values.density : null;
    expect(readPreferences(getItem, 'dark')).toEqual({ theme: 'dark', density: 'comfortable' });
  });

  it('reads preferences through the exact storage keys', () => {
    const seen: string[] = [];
    const getItem = (key: string) => { seen.push(key); return null; };
    readPreferences(getItem, 'light');
    expect(seen).toEqual([THEME_KEY, DENSITY_KEY]);
  });

  it.each(['compact', 'comfortable'] as const)('parses density %s', value => {
    expect(parseStoredDensity(value)).toBe(value);
  });

  it.each([null, '', 'spacious', '"compact"', 'Compact', 'comfortable '])('falls back density to comfortable for %j', raw => {
    expect(parseStoredDensity(raw)).toBe('comfortable');
  });

  it('writes valid theme/density values to the whitelisted keys and reports success', () => {
    const setItem = vi.fn(() => undefined as void);
    expect(writeTheme(setItem, 'dark')).toBe(true);
    expect(writeTheme(setItem, 'light')).toBe(true);
    expect(writeDensity(setItem, 'compact')).toBe(true);
    expect(writeDensity(setItem, 'comfortable')).toBe(true);
    expect(setItem.mock.calls).toEqual([
      [THEME_KEY, 'dark'],
      [THEME_KEY, 'light'],
      [DENSITY_KEY, 'compact'],
      [DENSITY_KEY, 'comfortable'],
    ]);
  });

  it.each(['midnight', '"dark"', '', 'DARK'])('never writes invalid theme %j, returns false and never throws', invalid => {
    const setItem = vi.fn();
    let result = true;
    expect(() => { result = writeTheme(setItem, invalid as 'light'); }).not.toThrow();
    expect(result).toBe(false);
    expect(setItem).not.toHaveBeenCalled();
  });

  it.each(['spacious', '"compact"', '', 'Compact'])('never writes invalid density %j, returns false and never throws', invalid => {
    const setItem = vi.fn();
    let result = true;
    expect(() => { result = writeDensity(setItem, invalid as 'compact'); }).not.toThrow();
    expect(result).toBe(false);
    expect(setItem).not.toHaveBeenCalled();
  });

  it('returns false (without throwing) when storage setItem throws', () => {
    const setItem = vi.fn(() => { throw new Error('storage quota exceeded'); });
    let themeResult = true;
    let densityResult = true;
    expect(() => { themeResult = writeTheme(setItem, 'dark'); }).not.toThrow();
    expect(() => { densityResult = writeDensity(setItem, 'compact'); }).not.toThrow();
    expect(themeResult).toBe(false);
    expect(densityResult).toBe(false);
  });

  it('applies preferences onto the injected root dataset', () => {
    const root = { dataset: {} as Record<string, string> };
    applyPreferences(root, { theme: 'dark', density: 'compact' });
    expect(root.dataset).toEqual({ theme: 'dark', density: 'compact' });
    applyPreferences(root, { theme: 'light', density: 'comfortable' });
    expect(root.dataset).toEqual({ theme: 'light', density: 'comfortable' });
  });

  it('maps chart theme colors for both themes with the same frozen key set', () => {
    const light = chartThemeColors(false);
    const dark = chartThemeColors(true);
    const scalarKeys = [
      'text', 'axisLabel', 'axisLine', 'splitLine',
      'tooltipBg', 'tooltipBorder', 'tooltipText',
      'up', 'down', 'accent', 'muted',
    ] as const;
    const keys = [...scalarKeys, 'palette'] as const;
    expect(Object.keys(light).sort()).toEqual([...keys].sort());
    expect(Object.keys(dark).sort()).toEqual([...keys].sort());
    for (const key of scalarKeys) {
      expect(light[key]).toMatch(/^#[0-9a-f]{6}$/i);
      expect(dark[key]).toMatch(/^#[0-9a-f]{6}$/i);
      expect(dark[key]).not.toBe(light[key]);
    }
    // 两张表（含 palette 数组）均冻结，避免运行期被改色
    expect(Object.isFrozen(light)).toBe(true);
    expect(Object.isFrozen(dark)).toBe(true);
    expect(Object.isFrozen(light.palette)).toBe(true);
    expect(Object.isFrozen(dark.palette)).toBe(true);
    expect(light.palette).toHaveLength(5);
    expect(dark.palette).toHaveLength(5);
    // 同一表重复取用须返回同一引用；布尔入参而非主题字符串
    expect(chartThemeColors(false)).toBe(light);
    expect(chartThemeColors(true)).toBe(dark);
    // 浅色映射当前 Chart.tsx / echarts v6 现状色（零回归），palette 保持原五色
    expect(light).toEqual({
      text: '#4b5563',
      axisLabel: '#54555a',
      axisLine: '#54555a',
      splitLine: '#dbdee4',
      tooltipBg: '#ffffff',
      tooltipBorder: '#b7b9be',
      tooltipText: '#6d6e73',
      up: '#b42332',
      down: '#087347',
      accent: '#1759ad',
      muted: '#64748b',
      palette: ['#1759ad', '#a64d00', '#733eac', '#007d76', '#a33360'],
    });
    // 深色采用计划 PRD 深色档位，palette 为深色底上同语义档
    expect(dark).toEqual({
      text: '#f3f6fa',
      axisLabel: '#aab6c4',
      axisLine: '#283442',
      splitLine: '#283442',
      tooltipBg: '#1b2430',
      tooltipBorder: '#283442',
      tooltipText: '#f3f6fa',
      up: '#e5484d',
      down: '#18a058',
      accent: '#3b82f6',
      muted: '#aab6c4',
      palette: ['#3b82f6', '#f59e0b', '#a78bfa', '#2dd4bf', '#f472b6'],
    });
  });
});

describe('palette shortcut guards', () => {
  it('accepts Ctrl+K and Cmd+K variants', () => {
    expect(isPaletteShortcut({ key: 'k', ctrlKey: true })).toBe(true);
    expect(isPaletteShortcut({ key: 'k', metaKey: true })).toBe(true);
    expect(isPaletteShortcut({ key: 'k', ctrlKey: true, metaKey: true })).toBe(true);
  });

  it('rejects non-shortcut and Shift/CapsLock/IME variants', () => {
    expect(isPaletteShortcut({ key: 'k' })).toBe(false);
    // 'K' 同时覆盖 Shift+K 与 CapsLock 大写 K
    expect(isPaletteShortcut({ key: 'K', ctrlKey: true })).toBe(false);
    expect(isPaletteShortcut({ key: 'l', ctrlKey: true })).toBe(false);
    expect(isPaletteShortcut({ key: 'k', ctrlKey: true, isComposing: true })).toBe(false);
    expect(isPaletteShortcut({ key: 'k', ctrlKey: true, keyCode: 229 })).toBe(false);
    expect(isPaletteShortcut({ key: 'Process', ctrlKey: true, keyCode: 229 })).toBe(false);
  });

  it('detects IME composition through either signal', () => {
    expect(isTypingBlocked({})).toBe(false);
    expect(isTypingBlocked({ isComposing: false, keyCode: 65 })).toBe(false);
    expect(isTypingBlocked({ isComposing: true })).toBe(true);
    expect(isTypingBlocked({ keyCode: 229 })).toBe(true);
  });
});

const baseLabels: Record<string, string> = {
  overview: '总览',
  analysis: '数据分析',
  news: '新闻资讯',
  leaders: '龙头分析',
  regulation: '监管追踪',
  review: '私人复盘',
};

function context(overrides: Partial<CommandContext> = {}): CommandContext {
  return {
    // 默认上下文包含 review；公开构建收窄由用例显式传入
    allowedViews: ['overview', 'analysis', 'news', 'leaders', 'regulation', 'review'],
    viewLabels: baseLabels,
    securities: [
      { id: 'SH600000', name: '浦发银行' },
      { id: 'SZ000001', name: '平安银行' },
      { id: 'UNKNOWN', name: null },
    ],
    sectors: [
      { id: 'SEC001', name: '机器人' },
      { id: 'SEC002', name: '半导体' },
    ],
    theme: 'light',
    density: 'comfortable',
    ...overrides,
  };
}

const expectedKindOf = (id: string) => {
  if (id.startsWith('goto:')) return 'goto' as const;
  if (id.startsWith('security:')) return 'security' as const;
  if (id.startsWith('sector:')) return 'sector' as const;
  if (id.startsWith('theme:')) return 'theme' as const;
  if (id.startsWith('density:')) return 'density' as const;
  if (id === 'date:focus') return 'focus-date' as const;
  throw new Error(`unexpected command id ${id}`);
};

describe('buildCommands whitelist', () => {
  it('builds page commands narrowed by allowedViews with viewLabels text', () => {
    const commands = buildCommands(context());
    const pages = commands.filter(command => command.group === '页面');
    // 组内按 label 字典序：总览 < 数据分析 < 新闻资讯 < 监管追踪 < 私人复盘 < 龙头分析
    expect(pages.map(command => command.id)).toEqual([
      'goto:overview',
      'goto:analysis',
      'goto:news',
      'goto:regulation',
      'goto:review',
      'goto:leaders',
    ]);
    expect(pages.find(command => command.id === 'goto:overview')?.label).toBe('总览');
  });

  it('narrows review out for a public sharing build compared with the default context', () => {
    // 默认上下文（reset 全功能）包含 review
    expect(buildCommands(context()).some(command => command.id === 'goto:review')).toBe(true);
    // 公开分享构建显式收窄 allowedViews，不再产出 goto:review
    const publicCtx = context({
      allowedViews: ['overview', 'analysis', 'news', 'leaders', 'regulation'],
    });
    const commands = buildCommands(publicCtx);
    expect(commands.some(command => command.id === 'goto:review')).toBe(false);
    expect(commands.some(command => command.label.includes('复盘'))).toBe(false);
  });

  it('includes review only when allowed', () => {
    const commands = buildCommands(context({ allowedViews: ['overview', 'review'] }));
    const review = commands.find(command => command.id === 'goto:review');
    expect(review).toMatchObject({ group: '页面', label: '私人复盘' });
  });

  it('emits an empty page group when allowedViews is empty', () => {
    const commands = buildCommands(context({ allowedViews: [] }));
    expect(commands.some(command => command.group === '页面')).toBe(false);
    expect(commands.length).toBeGreaterThan(0);
  });

  it('skips views whose label is missing or blank and deduplicates repeated views', () => {
    const commands = buildCommands(context({
      allowedViews: ['overview', 'ghost', 'blank', 'overview'],
      viewLabels: { ...baseLabels, blank: '   ' },
    }));
    expect(commands.filter(command => command.group === '页面').map(command => command.id))
      .toEqual(['goto:overview']);
    expect(commands.some(command => command.id === 'goto:ghost')).toBe(false);
    expect(commands.some(command => command.id === 'goto:blank')).toBe(false);
  });

  it('builds the fixed theme, density and date commands', () => {
    const commands = buildCommands(context({ theme: 'dark', density: 'compact' }));
    expect(commands.filter(command => command.group === '主题').map(command => command.id))
      .toEqual(['theme:light', 'theme:dark']);
    expect(commands.filter(command => command.group === '密度').map(command => command.id))
      .toEqual(['density:compact', 'density:comfortable']);
    expect(commands.filter(command => command.group === '日期').map(command => command.id))
      .toEqual(['date:focus']);
  });

  it('builds security and sector commands from loaded index items', () => {
    const commands = buildCommands(context());
    const stock = commands.find(command => command.id === 'security:SH600000');
    expect(stock).toMatchObject({ group: '股票', label: '浦发银行' });
    expect(commands.find(command => command.id === 'security:UNKNOWN')?.label).toBe('UNKNOWN');
    expect(commands.find(command => command.id === 'sector:SEC001')).toMatchObject({
      group: '题材', label: '机器人',
    });
  });

  it('skips null items and falls back to the id when name is missing, non-string or blank', () => {
    const commands = buildCommands(context({
      securities: [
        null,
        { id: 'S1', name: '正常名' },
        { id: 'S2', name: '   ' },
        { id: 'S3', name: null },
        { id: 'S4', name: 42 as unknown as string },
      ] as unknown as SearchItem[],
      sectors: [
        null,
        { id: 'T1', name: '' },
        { id: 'T2', name: false as unknown as string },
      ] as unknown as SearchItem[],
    }));
    expect(commands.find(command => command.id === 'security:S1')?.label).toBe('正常名');
    for (const id of ['S2', 'S3', 'S4']) {
      expect(commands.find(command => command.id === `security:${id}`)?.label).toBe(id);
    }
    expect(commands.find(command => command.id === 'sector:T1')?.label).toBe('T1');
    expect(commands.find(command => command.id === 'sector:T2')?.label).toBe('T2');
  });

  it.each([
    { kind: 'securities' as const, id: '' },
    { kind: 'securities' as const, id: '.' },
    { kind: 'securities' as const, id: '..' },
    { kind: 'securities' as const, id: 'x'.repeat(201) },
    { kind: 'sectors' as const, id: '' },
    { kind: 'sectors' as const, id: '.' },
    { kind: 'sectors' as const, id: '..' },
    { kind: 'sectors' as const, id: 'y'.repeat(201) },
  ])('rejects dynamic id %j from the whitelist', ({ kind, id }) => {
    const commands = buildCommands(context({ [kind]: [{ id, name: '脏数据' }] }));
    expect(commands.some(command => command.id.endsWith(`:${id}`))).toBe(false);
  });

  it('accepts dynamic ids at the 200 character boundary', () => {
    const id = 'z'.repeat(200);
    const commands = buildCommands(context({
      securities: [{ id, name: '边界证券' }],
      sectors: [{ id, name: '边界题材' }],
    }));
    expect(commands.some(command => command.id === `security:${id}`)).toBe(true);
    expect(commands.some(command => command.id === `sector:${id}`)).toBe(true);
  });

  it('deduplicates security and sector items sharing the same id', () => {
    const commands = buildCommands(context({
      securities: [{ id: 'DUP', name: '甲' }, { id: 'DUP', name: '乙' }],
      sectors: [{ id: 'TDUP', name: '题材甲' }, { id: 'TDUP', name: '题材乙' }],
    }));
    expect(commands.filter(command => command.id === 'security:DUP')).toHaveLength(1);
    expect(commands.filter(command => command.id === 'sector:TDUP')).toHaveLength(1);
  });

  it.each([
    ['默认上下文（含 review 与已加载证券题材）', context()],
    ['公开分享构建（无 review）', context({ allowedViews: ['overview', 'analysis', 'news', 'leaders', 'regulation'] })],
    ['空 allowedViews', context({ allowedViews: [] })],
    ['仅 review 页面', context({ allowedViews: ['review'], securities: [], sectors: [] })],
    ['仅动态证券与题材', context({
      allowedViews: [],
      securities: [{ id: 'AAA', name: '证券甲' }, { id: 'BBB', name: null }],
      sectors: [{ id: 'T1', name: '题材甲' }],
    })],
  ])('build/execute 单一数据源同构：%s 的每条命令都可执行且 kind 与 id 前缀对应', (_label, ctx) => {
    const commands = buildCommands(ctx);
    expect(commands.length).toBeGreaterThan(0);
    for (const command of commands) {
      const result = executeCommand(command, ctx);
      expect(result.ok, `${command.id} 应执行成功`).toBe(true);
      if (result.ok) expect(result.action.kind).toBe(expectedKindOf(command.id));
    }
  });
});

describe('filterCommands', () => {
  it('returns the full ordered list for an empty or whitespace query', () => {
    const commands = buildCommands(context());
    expect(filterCommands(commands, '')).toEqual(commands);
    expect(filterCommands(commands, '   ')).toEqual(commands);
  });

  it('matches label, id and keywords with trimmed case-insensitive substrings', () => {
    const commands = buildCommands(context());
    expect(filterCommands(commands, '  银行 ').map(command => command.id))
      .toContain('security:SH600000');
    expect(filterCommands(commands, 'GOTO:NEWS').map(command => command.id))
      .toEqual(['goto:news']);
    expect(filterCommands(commands, 'snapshot').map(command => command.id))
      .toEqual(['date:focus']);
  });

  it('matches keywords written in upper case', () => {
    const commands = buildCommands(context());
    expect(filterCommands(commands, 'DARK').map(command => command.id)).toContain('theme:dark');
    expect(filterCommands(commands, 'COMPACT').map(command => command.id)).toContain('density:compact');
    expect(filterCommands(commands, 'SNAPSHOT').map(command => command.id)).toEqual(['date:focus']);
  });

  it('keeps the fixed group order and sorts labels lexicographically inside groups', () => {
    const commands = filterCommands(buildCommands(context({
      allowedViews: ['overview', 'news'],
      viewLabels: { overview: 'Zeta', news: 'Alpha' },
      securities: [{ id: 'S1', name: 'Zebra' }, { id: 'S2', name: 'Alpha' }],
      sectors: [],
    })), '');
    const groups = commands.map(command => GROUPS.indexOf(command.group));
    expect(groups).toEqual([...groups].sort((a, b) => a - b));
    expect(commands.filter(command => command.group === '页面').map(command => command.label))
      .toEqual(['Alpha', 'Zeta']);
    expect(commands.filter(command => command.group === '股票').map(command => command.label))
      .toEqual(['Alpha', 'Zebra']);
  });

  it('caps results at 50 while preserving group/label order', () => {
    const securities = Array.from({ length: 60 }, (_, index) => ({
      id: `S${index}`, name: `股票${String(index).padStart(2, '0')}`,
    }));
    const commands = filterCommands(buildCommands(context({ securities, sectors: [] })), '');
    expect(commands).toHaveLength(50);
    const groups = commands.map(command => GROUPS.indexOf(command.group));
    expect(groups).toEqual([...groups].sort((a, b) => a - b));
  });

  it('returns an empty list when nothing matches', () => {
    expect(filterCommands(buildCommands(context()), '不可能匹配的关键字xyz')).toEqual([]);
  });
});

describe('executeCommand', () => {
  it('executes every whitelisted branch', () => {
    const ctx = context();
    const byId = (id: string): Command => {
      const command = buildCommands(ctx).find(item => item.id === id);
      if (!command) throw new Error(`missing command ${id}`);
      return command;
    };
    expect(executeCommand(byId('goto:news'), ctx)).toEqual({ ok: true, action: { kind: 'goto', view: 'news' } });
    expect(executeCommand(byId('security:SH600000'), ctx)).toEqual({
      ok: true, action: { kind: 'security', id: 'SH600000' },
    });
    expect(executeCommand(byId('sector:SEC002'), ctx)).toEqual({
      ok: true, action: { kind: 'sector', id: 'SEC002' },
    });
    expect(executeCommand(byId('theme:dark'), ctx)).toEqual({
      ok: true, action: { kind: 'theme', theme: 'dark' },
    });
    expect(executeCommand(byId('density:compact'), ctx)).toEqual({
      ok: true, action: { kind: 'density', density: 'compact' },
    });
    expect(executeCommand(byId('date:focus'), ctx)).toEqual({
      ok: true, action: { kind: 'focus-date' },
    });
  });

  it.each([
    { id: 'goto:evil', label: '伪造页面', group: '页面' },
    { id: 'theme:hack', label: '伪造主题', group: '主题' },
    { id: 'density:ultra', label: '伪造密度', group: '密度' },
    { id: 'security:../../../etc/passwd', label: '伪造证券', group: '股票' },
    { id: 'totally-random', label: '随机串', group: '页面' },
  ])('rejects forged command %j', forged => {
    const result = executeCommand(forged as Command, context());
    expect(result).toEqual({ ok: false, reason: '命令不在当前可用列表中。' });
  });

  it('rejects structurally tampered commands that borrow a whitelisted id', () => {
    const ctx = context();
    expect(executeCommand({ id: 'goto:overview', label: '总览', group: '主题' }, ctx))
      .toEqual({ ok: false, reason: '命令不在当前可用列表中。' });
    expect(executeCommand({ id: 'goto:overview', label: '冒充文案', group: '页面' }, ctx))
      .toEqual({ ok: false, reason: '命令不在当前可用列表中。' });
  });

  it('rejects commands that are valid in another context', () => {
    const narrowCtx = context({
      allowedViews: ['overview'],
      viewLabels: { overview: '总览' },
      securities: [],
      sectors: [],
    });
    const secret = buildCommands(context({
      allowedViews: ['secret-view'],
      viewLabels: { 'secret-view': '秘密页面' },
    })).find(command => command.id === 'goto:secret-view');
    expect(secret).toBeDefined();
    expect(executeCommand(secret as Command, narrowCtx))
      .toEqual({ ok: false, reason: '命令不在当前可用列表中。' });
  });
});

describe('reportIndexItems', () => {
  afterEach(() => {
    delete (globalThis as { window?: unknown }).window;
    // 排空模块级补发队列，避免未绑定态试报残留到 bindIndexItems 用例。
    bindIndexItems(() => {})();
  });

  it('exposes the shared index event name constant', () => {
    expect(INDEX_ITEMS_EVENT).toBe('dr:index-items');
  });

  it('dispatches dr:index-items CustomEvents with the payload', () => {
    const dispatched: Array<{ type: string; detail: unknown }> = [];
    class FakeCustomEvent {
      type: string;
      detail: unknown;
      constructor(type: string, init?: { detail?: unknown }) {
        this.type = type;
        this.detail = init?.detail;
      }
    }
    (globalThis as { window?: unknown }).window = {
      CustomEvent: FakeCustomEvent,
      dispatchEvent: (event: { type: string; detail: unknown }) => {
        dispatched.push(event);
        return true;
      },
    };
    const payload = { securities: [{ id: 'S1', name: '证券' }], sectors: [{ id: 'T1', name: '题材' }] };
    expect(() => reportIndexItems(payload)).not.toThrow();
    expect(dispatched).toHaveLength(1);
    expect(dispatched[0].type).toBe(INDEX_ITEMS_EVENT);
    expect(dispatched[0].detail).toBe(payload);
  });

  it('stays silent when window is unavailable', () => {
    delete (globalThis as { window?: unknown }).window;
    expect(() => reportIndexItems({ securities: [], sectors: [] })).not.toThrow();
  });

  it('stays silent when the CustomEvent constructor or dispatchEvent throws', () => {
    class ThrowingConstructor {
      constructor() {
        throw new Error('constructor boom');
      }
    }
    (globalThis as { window?: unknown }).window = {
      CustomEvent: ThrowingConstructor,
      dispatchEvent: () => true,
    };
    expect(() => reportIndexItems({ securities: [] })).not.toThrow();

    class FakeCustomEvent {
      type: string;
      detail: unknown;
      constructor(type: string, init?: { detail?: unknown }) {
        this.type = type;
        this.detail = init?.detail;
      }
    }
    (globalThis as { window?: unknown }).window = {
      CustomEvent: FakeCustomEvent,
      dispatchEvent: () => {
        throw new Error('dispatch boom');
      },
    };
    expect(() => reportIndexItems({ sectors: [] })).not.toThrow();
  });
});

describe('bindIndexItems', () => {
  // 最小 EventTarget 桩：dispatchEvent 同步触达 addEventListener 注册的监听者。
  function installEventWindow() {
    const listeners = new Map<string, Set<(event: unknown) => void>>();
    class FakeCustomEvent {
      type: string;
      detail: unknown;
      constructor(type: string, init?: { detail?: unknown }) {
        this.type = type;
        this.detail = init?.detail;
      }
    }
    (globalThis as { window?: unknown }).window = {
      CustomEvent: FakeCustomEvent,
      dispatchEvent: (event: { type: string }) => {
        listeners.get(event.type)?.forEach(fn => fn(event));
        return true;
      },
      addEventListener: (type: string, fn: (event: unknown) => void) => {
        const set = listeners.get(type) ?? new Set<(event: unknown) => void>();
        set.add(fn);
        listeners.set(type, set);
      },
      removeEventListener: (type: string, fn: (event: unknown) => void) => {
        listeners.get(type)?.delete(fn);
      },
    };
  }

  afterEach(() => {
    delete (globalThis as { window?: unknown }).window;
    // 兜底排空模块队列，保证用例间互不污染。
    bindIndexItems(() => {})();
  });

  it('replays reports that arrived before bind in FIFO order, then delivers live events', () => {
    installEventWindow();
    const first = { securities: [{ id: 'S1', name: '甲' }] };
    const second = { securities: [{ id: 'S2', name: '乙' }] };
    reportIndexItems(first);
    reportIndexItems(second);
    const received: unknown[] = [];
    const unbind = bindIndexItems(detail => received.push(detail));
    expect(received).toEqual([first, second]);
    const live = { sectors: [{ id: 'X1', name: '题材' }] };
    reportIndexItems(live);
    expect(received).toEqual([first, second, live]);
    unbind();
  });

  it('stops delivery after unbind and never replays pre-unbind details on rebind', () => {
    installEventWindow();
    const oldPayload = { securities: [{ id: 'OLD', name: '旧' }] };
    reportIndexItems(oldPayload);
    const firstReceived: unknown[] = [];
    const unbind = bindIndexItems(detail => firstReceived.push(detail));
    expect(firstReceived).toEqual([oldPayload]);
    unbind();
    // 解绑后的新上报不会触达旧 handler。
    const afterPayload = { securities: [{ id: 'AFTER', name: '解绑后' }] };
    reportIndexItems(afterPayload);
    expect(firstReceived).toEqual([oldPayload]);
    // 再绑定不重放解绑前已补发过的 OLD；解绑期间入队的新 detail 按 FIFO 补发一次。
    const rebound: unknown[] = [];
    const rebind = bindIndexItems(detail => rebound.push(detail));
    expect(rebound).toEqual([afterPayload]);
    const fresh = { securities: [{ id: 'FRESH', name: '新会话' }] };
    reportIndexItems(fresh);
    expect(rebound).toEqual([afterPayload, fresh]);
    expect(firstReceived).toEqual([oldPayload]);
    rebind();
  });

  it('caps the pre-bind queue at 16 and drops the oldest entries', () => {
    installEventWindow();
    for (let i = 0; i < 18; i += 1) {
      reportIndexItems({ securities: [{ id: `S${i}`, name: `第${i}` }] });
    }
    const ids: string[] = [];
    const unbind = bindIndexItems(detail => {
      ids.push((detail as { securities: Array<{ id: string }> }).securities[0].id);
    });
    expect(ids).toEqual(Array.from({ length: 16 }, (_, i) => `S${i + 2}`));
    unbind();
  });

  it('treats repeated unbind calls as a no-op without throwing', () => {
    installEventWindow();
    const unbind = bindIndexItems(() => {});
    expect(() => { unbind(); unbind(); unbind(); }).not.toThrow();
  });

  it('replays the queue and unbinds safely when window is unavailable', () => {
    delete (globalThis as { window?: unknown }).window;
    reportIndexItems({ securities: [{ id: 'NW', name: null }] });
    const received: unknown[] = [];
    let unbind: () => void = () => {};
    expect(() => { unbind = bindIndexItems(detail => received.push(detail)); }).not.toThrow();
    expect(received).toHaveLength(1);
    expect(() => unbind()).not.toThrow();
  });
});

describe('bootstrapPreferences (node DOM stubs)', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  interface BootstrapStubsOptions {
    values?: Record<string, string | null>;
    getItem?: (key: string) => string | null;
    systemDark?: boolean;
    matchMedia?: (query: string) => { matches: boolean };
    existingMeta?: boolean;
    querySelector?: (selector: string) => unknown;
    documentElement?: { dataset: Record<string, unknown> };
  }

  // 纯 node 环境下伪造最小 DOM 契约：window.localStorage / window.matchMedia、
  // document.documentElement.dataset、document.querySelector('meta[name=color-scheme]')。
  function installStubs(options: BootstrapStubsOptions = {}) {
    const setAttribute = vi.fn();
    const metaElement = options.existingMeta === true ? { setAttribute } : null;
    const createElement = vi.fn();
    const querySelector = vi.fn(options.querySelector ?? (() => metaElement));
    const documentElement = options.documentElement ?? { dataset: {} as Record<string, unknown> };
    const getItem = vi.fn(
      options.getItem ?? ((key: string) => options.values?.[key] ?? null),
    );
    const matchMedia = vi.fn(
      options.matchMedia ?? (() => ({ matches: options.systemDark ?? false })),
    );
    const localStorageStub = { getItem };
    const windowStub = { localStorage: localStorageStub, matchMedia };
    const documentStub = { documentElement, querySelector, createElement };
    vi.stubGlobal('window', windowStub);
    vi.stubGlobal('document', documentStub);
    // 产品当前仅经 window.* 访问存储与媒体查询；顶层同名全局一并伪造以固定契约。
    vi.stubGlobal('localStorage', localStorageStub);
    vi.stubGlobal('matchMedia', matchMedia);
    return {
      setAttribute, createElement, querySelector, getItem, matchMedia, documentElement,
    };
  }

  it.each(['dark', 'light'] as const)(
    '落盘存储的 %s 主题与 compact 密度到 dataset，并设置已存在的 color-scheme meta',
    theme => {
      const refs = installStubs({
        values: { [THEME_KEY]: theme, [DENSITY_KEY]: 'compact' },
        existingMeta: true,
        systemDark: true,
      });
      let result: ReturnType<typeof bootstrapPreferences> | undefined;
      expect(() => { result = bootstrapPreferences(); }).not.toThrow();
      expect(result).toEqual({ theme, density: 'compact' });
      expect(refs.documentElement.dataset).toEqual({ theme, density: 'compact' });
      expect(refs.querySelector).toHaveBeenCalledWith('meta[name="color-scheme"]');
      expect(refs.setAttribute).toHaveBeenCalledTimes(1);
      expect(refs.setAttribute).toHaveBeenCalledWith('content', theme);
      // 显式主题存在时不查询系统深浅色，也不需要创建 meta
      expect(refs.matchMedia).not.toHaveBeenCalled();
      expect(refs.createElement).not.toHaveBeenCalled();
    },
  );

  it('meta[name="color-scheme"] 缺失时安全空转：偏好仍落盘，不抛错也不创建节点', () => {
    const refs = installStubs({
      values: { [THEME_KEY]: 'dark', [DENSITY_KEY]: 'comfortable' },
    });
    let result: ReturnType<typeof bootstrapPreferences> | undefined;
    expect(() => { result = bootstrapPreferences(); }).not.toThrow();
    expect(result).toEqual({ theme: 'dark', density: 'comfortable' });
    expect(refs.documentElement.dataset.theme).toBe('dark');
    expect(refs.createElement).not.toHaveBeenCalled();
    expect(refs.setAttribute).not.toHaveBeenCalled();
  });

  it.each([
    [true, 'dark'],
    [false, 'light'],
  ] as const)('无 dr:theme 存储时跟随 prefers-color-scheme matches=%s（%s）', (systemDark, expected) => {
    const refs = installStubs({ systemDark });
    let result: ReturnType<typeof bootstrapPreferences> | undefined;
    expect(() => { result = bootstrapPreferences(); }).not.toThrow();
    expect(result).toEqual({ theme: expected, density: 'comfortable' });
    expect(refs.documentElement.dataset.theme).toBe(expected);
    expect(refs.matchMedia).toHaveBeenCalledWith('(prefers-color-scheme: dark)');
  });

  it('存储值损坏时主题回退系统信号、密度回退 comfortable', () => {
    const refs = installStubs({
      values: { [THEME_KEY]: 'midnight', [DENSITY_KEY]: 'spacious' },
      systemDark: true,
    });
    expect(bootstrapPreferences()).toEqual({ theme: 'dark', density: 'comfortable' });
    expect(refs.documentElement.dataset).toEqual({
      theme: 'dark', density: 'comfortable',
    });
  });

  it('localStorage.getItem 抛错时回退 light/comfortable 且不抛出', () => {
    const refs = installStubs({
      getItem: () => { throw new Error('localStorage unavailable'); },
      systemDark: false,
    });
    let result: ReturnType<typeof bootstrapPreferences> | undefined;
    expect(() => { result = bootstrapPreferences(); }).not.toThrow();
    expect(result).toEqual({ theme: 'light', density: 'comfortable' });
    expect(refs.documentElement.dataset.theme).toBe('light');
  });

  it('storage 抛错不影响系统主题跟随（两个来源相互隔离）', () => {
    installStubs({
      getItem: () => { throw new Error('localStorage unavailable'); },
      systemDark: true,
    });
    let result: ReturnType<typeof bootstrapPreferences> | undefined;
    expect(() => { result = bootstrapPreferences(); }).not.toThrow();
    expect(result).toEqual({ theme: 'dark', density: 'comfortable' });
  });

  it('window.matchMedia 抛错时保持 light/comfortable 且不抛出', () => {
    const refs = installStubs({
      matchMedia: () => { throw new Error('matchMedia unavailable'); },
    });
    let result: ReturnType<typeof bootstrapPreferences> | undefined;
    expect(() => { result = bootstrapPreferences(); }).not.toThrow();
    expect(result).toEqual({ theme: 'light', density: 'comfortable' });
    expect(refs.documentElement.dataset.theme).toBe('light');
  });

  it('matchMedia 抛错时已存储的 density 仍被应用', () => {
    installStubs({
      values: { [DENSITY_KEY]: 'compact' },
      matchMedia: () => { throw new Error('matchMedia unavailable'); },
    });
    let result: ReturnType<typeof bootstrapPreferences> | undefined;
    expect(() => { result = bootstrapPreferences(); }).not.toThrow();
    expect(result).toEqual({ theme: 'light', density: 'compact' });
  });

  const lockedDataset: Record<string, string> = {};
  Object.defineProperty(lockedDataset, 'theme', {
    configurable: true,
    set() { throw new Error('dataset theme locked'); },
  });

  it.each<[string, BootstrapStubsOptions]>([
    ['documentElement.dataset 写入抛错', { documentElement: { dataset: lockedDataset } }],
    ['document.querySelector 抛错', { querySelector: () => { throw new Error('query boom'); } }],
    [
      '已存在 meta 的 setAttribute 抛错',
      { querySelector: () => ({ setAttribute: () => { throw new Error('attr boom'); } }) },
    ],
  ])('document 操作失败被外层吞掉（%s），回退 light/comfortable 且不抛出', (_label, domOptions) => {
    installStubs({
      values: { [THEME_KEY]: 'dark', [DENSITY_KEY]: 'compact' },
      ...domOptions,
    });
    let result: ReturnType<typeof bootstrapPreferences> | undefined;
    expect(() => { result = bootstrapPreferences(); }).not.toThrow();
    expect(result).toEqual({ theme: 'light', density: 'comfortable' });
  });
});

describe('sanitizeIndexPayload', () => {
  it('透传结构合法的 payload（产生新对象/新数组，不共享引用）', () => {
    const payload = { securities: [{ id: 'SH600000', name: '浦发银行' }], sectors: [{ id: 'SEC001', name: null }] };
    const clean = sanitizeIndexPayload(payload);
    expect(clean).toEqual({
      securities: [{ id: 'SH600000', name: '浦发银行' }],
      sectors: [{ id: 'SEC001', name: null }],
    });
    expect(clean.securities).not.toBe(payload.securities);
    expect(clean.sectors).not.toBe(payload.sectors);
    expect((clean.securities as SearchItem[])[0]).not.toBe(payload.securities[0]);
    expect(clean).not.toBe(payload);
  });

  it.each([null, undefined, 'oops', 42, [], Object.create(null)])('非普通对象 detail %j → 空载荷', detail => {
    expect(sanitizeIndexPayload(detail)).toEqual({});
  });

  it('securities/sectors 缺失或为非数组时丢弃该键', () => {
    expect(sanitizeIndexPayload({})).toEqual({});
    expect(sanitizeIndexPayload({ securities: null })).toEqual({});
    expect(sanitizeIndexPayload({ securities: 'nope' })).toEqual({});
    expect(sanitizeIndexPayload({ sectors: 42 })).toEqual({});
    const clean = sanitizeIndexPayload({ securities: [], sectors: [] });
    expect(clean).toEqual({ securities: [], sectors: [] });
  });

  it('丢弃畸形 item：非对象、id 非字符串或空白、name 既非 string 也非 null', () => {
    const clean = sanitizeIndexPayload({
      securities: [
        null,
        'SH600000',
        42,
        { id: '', name: '空 id' },
        { id: '   ', name: '空白 id' },
        { id: 42, name: '数字 id' },
        { id: null, name: 'null id' },
        { name: '无 id' },
        { id: 'S1', name: 42 },
        { id: 'S2', name: undefined },
        { id: 'S3', name: { nested: true } },
        { id: 'S4', name: '' },
        { id: 'S5', name: null },
        { id: 'S6', name: '正规名' },
      ],
    });
    expect(clean.securities).toEqual([
      { id: 'S4', name: '' },
      { id: 'S5', name: null },
      { id: 'S6', name: '正规名' },
    ]);
  });

  it('对恶意 detail 从不抛出（含循环引用/抛错 getter）', () => {
    const cyclic: Record<string, unknown> = {};
    cyclic.self = cyclic;
    expect(() => sanitizeIndexPayload(cyclic)).not.toThrow();
    const evil = {};
    Object.defineProperty(evil, 'securities', { get() { throw new Error('boom'); } });
    expect(() => sanitizeIndexPayload(evil)).not.toThrow();
    expect(sanitizeIndexPayload(evil)).toEqual({});
  });
});

describe('mergeIndexItems', () => {
  const empty: SearchIndex = { securities: [], sectors: [] };
  const item = (id: string, name: string | null = null): SearchItem => ({ id, name });

  it('基本合并：两类按 securities 先于 sectors 归入', () => {
    const merged = mergeIndexItems(empty, {
      securities: [item('S1', '证券甲'), item('S2', '证券乙')],
      sectors: [item('T1', '题材甲')],
    });
    expect(merged).toEqual({
      securities: [{ id: 'S1', name: '证券甲' }, { id: 'S2', name: '证券乙' }],
      sectors: [{ id: 'T1', name: '题材甲' }],
    });
  });

  it('跨事件按 kind+id 去重并保持首见顺序', () => {
    const first = mergeIndexItems(empty, { securities: [item('S2'), item('S1')], sectors: [item('T2')] });
    const second = mergeIndexItems(first, { securities: [item('S1'), item('S3')], sectors: [item('T1'), item('T2')] });
    expect(second.securities.map(s => s.id)).toEqual(['S2', 'S1', 'S3']);
    expect(second.sectors.map(s => s.id)).toEqual(['T2', 'T1']);
  });

  it('同 id 新名字为非空白字符串则更新；null/空白保留旧名；旧名为 null 时可被新名补上', () => {
    const prev: SearchIndex = {
      securities: [item('S1', '旧名'), item('S2', null), item('S3', '旧名三')],
      sectors: [],
    };
    const merged = mergeIndexItems(prev, {
      securities: [
        item('S1', '新名'),
        item('S1', '再报一名'),
        item('S2', null),
        item('S3', '   '),
        { id: 'S4', name: '' } as SearchItem,
      ],
    });
    // 同事件内重复 id 以首个非空白新名为准（首见更新语义）
    expect(merged.securities).toEqual([
      { id: 'S1', name: '新名' },
      { id: 'S2', name: null },
      { id: 'S3', name: '旧名三' },
      { id: 'S4', name: '' },
    ]);
    const filled = mergeIndexItems(
      { securities: [item('S2', null)], sectors: [] },
      { securities: [item('S2', '补名')] },
    );
    expect(filled.securities[0].name).toBe('补名');
  });

  it('畸形 item 在合并时同样被丢弃且不抛错（prev 与 incoming 都防御）', () => {
    const dirtyPrev = {
      securities: [null, 'x', 42, { id: '' }, { id: 'ok', name: 1 }, { name: '缺 id' }, item('KEEP', '保留')] as unknown[],
      sectors: [{ id: 'T1', name: ['not-string'] }] as unknown[],
    } as SearchIndex;
    let merged: SearchIndex | undefined;
    expect(() => {
      merged = mergeIndexItems(dirtyPrev, {
        securities: [null, { id: 'NEW', name: null }, { id: 7, name: null }, { id: 'NO_NAME' }] as unknown[] as SearchItem[],
      });
    }).not.toThrow();
    // name 非法（数字/缺失）的整项一律丢弃，仅 KEEP 与合法 NEW 保留
    expect(merged!.securities.map(s => s.id)).toEqual(['KEEP', 'NEW']);
    expect(merged!.sectors).toEqual([]);
  });

  it('上限截断确定性：securities 先于 sectors 占满 maxTotal，超出按首见顺序丢弃', () => {
    const prev: SearchIndex = {
      securities: [item('S1'), item('S2'), item('S3')],
      sectors: [item('T1')],
    };
    const merged = mergeIndexItems(prev, {
      securities: [item('S4')],
      sectors: [item('T2'), item('T3')],
    }, 4);
    expect(merged.securities.map(s => s.id)).toEqual(['S1', 'S2', 'S3', 'S4']);
    expect(merged.sectors).toEqual([]);
  });

  it('上限截断在两 kind 间按比例切（securities 占满后不再评估 sectors），边界恰满不丢项', () => {
    const merged = mergeIndexItems(empty, {
      securities: [item('S1'), item('S2')],
      sectors: [item('T1'), item('T2')],
    }, 3);
    expect(merged.securities.map(s => s.id)).toEqual(['S1', 'S2']);
    expect(merged.sectors.map(s => s.id)).toEqual(['T1']);
    const exact = mergeIndexItems(empty, {
      securities: [item('S1')], sectors: [item('T1')],
    }, 2);
    expect(exact.securities.map(s => s.id)).toEqual(['S1']);
    expect(exact.sectors.map(s => s.id)).toEqual(['T1']);
  });

  it('默认上限 500：300 证券 + 300 题材 → 证券 300 + 题材 200', () => {
    const securities = Array.from({ length: 300 }, (_, i) => item(`S${i}`));
    const sectors = Array.from({ length: 300 }, (_, i) => item(`T${i}`));
    const merged = mergeIndexItems(empty, { securities, sectors });
    expect(merged.securities).toHaveLength(300);
    expect(merged.sectors).toHaveLength(200);
    expect(merged.sectors[199].id).toBe('T199');
  });

  it('不突变入参并返回等价新对象', () => {
    const prev: SearchIndex = { securities: [item('S1', '甲')], sectors: [item('T1')] };
    const incoming = { securities: [item('S1', '乙'), item('S2')], sectors: [item('T2')] };
    const prevSnapshot = JSON.stringify(prev);
    const incomingSnapshot = JSON.stringify(incoming);
    const merged = mergeIndexItems(prev, incoming);
    expect(JSON.stringify(prev)).toBe(prevSnapshot);
    expect(JSON.stringify(incoming)).toBe(incomingSnapshot);
    expect(merged).not.toBe(prev);
    expect(merged.securities).not.toBe(prev.securities);
    expect(merged.sectors).not.toBe(prev.sectors);
    const emptyResult = mergeIndexItems(empty, {});
    expect(emptyResult).toEqual({ securities: [], sectors: [] });
    expect(emptyResult).not.toBe(empty);
    expect(emptyResult.securities).not.toBe(empty.securities);
  });
});

export type ThemeMode = 'light' | 'dark';
export type DensityMode = 'compact' | 'comfortable';

export interface Preferences {
  theme: ThemeMode;
  density: DensityMode;
}

export const THEME_KEY = 'dr:theme';
export const DENSITY_KEY = 'dr:density';

const THEME_VALUES: readonly ThemeMode[] = ['light', 'dark'];
const DENSITY_VALUES: readonly DensityMode[] = ['compact', 'comfortable'];

/** ECharts 配色（文字/轴标签/轴线/分割线/tooltip 背景边框），按主题纯映射。 */
export interface ChartTextColors {
  text: string;
  axisLabel: string;
  axisLine: string;
  splitLine: string;
  tooltipBackground: string;
  tooltipBorder: string;
  tooltipText: string;
}

/** matchMedia('(prefers-color-scheme: dark)').matches 的纯映射。 */
export function systemTheme(matchDark: boolean): ThemeMode {
  return matchDark ? 'dark' : 'light';
}

/** 无显式值或值非法/损坏时回退到入参给定的系统主题（fallback）。 */
export function parseStoredTheme(raw: string | null, fallback: ThemeMode): ThemeMode {
  return raw === 'light' || raw === 'dark' ? raw : fallback;
}

/** 无显式值或值非法/损坏时回退到 comfortable。 */
export function parseStoredDensity(raw: string | null): DensityMode {
  return raw === 'compact' || raw === 'comfortable' ? raw : 'comfortable';
}

/** 所有 storage 访问经 getItem 注入，node 环境零全局依赖。 */
export function readPreferences(
  getItem: (key: string) => string | null,
  fallback: ThemeMode,
): Preferences {
  return {
    theme: parseStoredTheme(getItem(THEME_KEY), fallback),
    density: parseStoredDensity(getItem(DENSITY_KEY)),
  };
}

/** 仅在值合法时写入白名单键；非法值不写入且不抛异常。 */
export function writeTheme(setItem: (key: string, value: string) => void, value: ThemeMode): void {
  if (THEME_VALUES.includes(value)) setItem(THEME_KEY, value);
}

/** 仅在值合法时写入白名单键；非法值不写入且不抛异常。 */
export function writeDensity(setItem: (key: string, value: string) => void, value: DensityMode): void {
  if (DENSITY_VALUES.includes(value)) setItem(DENSITY_KEY, value);
}

/** 把偏好写到注入根节点的 dataset（documentElement.dataset）。 */
export function applyPreferences(root: { dataset: Record<string, string> }, preferences: Preferences): void {
  root.dataset.theme = preferences.theme;
  root.dataset.density = preferences.density;
}

/** IME 组合态判定：isComposing 或 keyCode 229 均视为组合中。 */
export function isTypingBlocked(event: { isComposing?: boolean; keyCode?: number }): boolean {
  return event.isComposing === true || event.keyCode === 229;
}

/**
 * Ctrl/Cmd+K 快捷键判定；一律不触发的情形：
 * 无修饰键、Shift+K（key 为大写 'K'）、CapsLock 大写锁定下的 K（同为 'K'）、IME 组合态。
 */
export function isPaletteShortcut(event: {
  key: string;
  ctrlKey?: boolean;
  metaKey?: boolean;
  isComposing?: boolean;
  keyCode?: number;
}): boolean {
  return event.key === 'k'
    && (event.ctrlKey === true || event.metaKey === true)
    && !isTypingBlocked(event);
}

const LIGHT_CHART_COLORS: ChartTextColors = Object.freeze({
  // text 映射当前 Chart.tsx textStyle.color；axisLabel/axisLine/splitLine/tooltip
  // 为当前 echarts v6 token 默认渲染色（neutral70/neutral15/neutral00/border/tertiary），保持浅色零回归。
  text: '#4b5563',
  axisLabel: '#54555a',
  axisLine: '#54555a',
  splitLine: '#dbdee4',
  tooltipBackground: '#ffffff',
  tooltipBorder: '#b7b9be',
  tooltipText: '#6d6e73',
});

const DARK_CHART_COLORS: ChartTextColors = Object.freeze({
  text: '#F3F6FA',
  axisLabel: '#AAB6C4',
  axisLine: '#283442',
  splitLine: '#283442',
  tooltipBackground: '#1B2430',
  tooltipBorder: '#283442',
  tooltipText: '#F3F6FA',
});

export function chartTextColors(theme: ThemeMode): ChartTextColors {
  return theme === 'dark' ? DARK_CHART_COLORS : LIGHT_CHART_COLORS;
}

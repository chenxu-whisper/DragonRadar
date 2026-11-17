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

/** ECharts 配色：文字/轴/tooltip + 数据系列语义色（涨跌/强调/中性/五色板），按深色开关纯映射。 */
export interface ChartThemeColors {
  text: string;
  axisLabel: string;
  axisLine: string;
  splitLine: string;
  tooltipBg: string;
  tooltipBorder: string;
  tooltipText: string;
  up: string;
  down: string;
  accent: string;
  muted: string;
  palette: readonly string[];
}

const LIGHT_CHART_COLORS: ChartThemeColors = Object.freeze({
  // 逐值映射 Chart.tsx / echarts v6 现状渲染色，保持浅色零回归；palette 为原 Chart.tsx 五色。
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
  palette: Object.freeze(['#1759ad', '#a64d00', '#733eac', '#007d76', '#a33360']),
});

const DARK_CHART_COLORS: ChartThemeColors = Object.freeze({
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
  // 深色底（#141b24）上同语义档，保持 ≥3:1 对比。
  palette: Object.freeze(['#3b82f6', '#f59e0b', '#a78bfa', '#2dd4bf', '#f472b6']),
});

/** 深色开关到冻结色表的纯映射；Chart.tsx 据 documentElement[data-theme] 调用。 */
export function chartThemeColors(dark: boolean): ChartThemeColors {
  return dark ? DARK_CHART_COLORS : LIGHT_CHART_COLORS;
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

/** 值合法且 setItem 未抛错时返回 true；非法值或写入抛错返回 false，绝不向上抛。 */
export function writeTheme(setItem: (key: string, value: string) => void, value: ThemeMode): boolean {
  if (!THEME_VALUES.includes(value)) return false;
  try {
    setItem(THEME_KEY, value);
    return true;
  } catch {
    return false;
  }
}

/** 值合法且 setItem 未抛错时返回 true；非法值或写入抛错返回 false，绝不向上抛。 */
export function writeDensity(setItem: (key: string, value: string) => void, value: DensityMode): boolean {
  if (!DENSITY_VALUES.includes(value)) return false;
  try {
    setItem(DENSITY_KEY, value);
    return true;
  } catch {
    return false;
  }
}

/** 把偏好写到注入根节点的 dataset（documentElement.dataset）。 */
export function applyPreferences(
  root: { dataset: { theme?: string; density?: string } },
  preferences: Preferences,
): void {
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

/**
 * 首绘前/偏好变更后的自包含引导：读 localStorage 白名单键（theme 缺失时跟随系统
 * prefers-color-scheme）→ 写 documentElement.dataset → 同步 meta[name=color-scheme]。
 * 不引用本文件其他导出，使未用纯函数可被打包器摇出 entry；任何异常回退
 * {light, comfortable} 且不抛出。
 */
export function bootstrapPreferences(): Preferences {
  try {
    let theme: ThemeMode = 'light';
    let density: DensityMode = 'comfortable';
    // storage 与 matchMedia 分别隔离：任一来源不可用不得影响另一来源。
    let storedTheme: string | null = null;
    let storedDensity: string | null = null;
    try {
      storedTheme = window.localStorage.getItem(THEME_KEY);
      storedDensity = window.localStorage.getItem(DENSITY_KEY);
    } catch {
      // localStorage 不可用（隐私模式/禁用）：两个显式值均按缺失处理。
    }
    if (storedTheme === 'light' || storedTheme === 'dark') {
      theme = storedTheme;
    } else {
      try {
        if (window.matchMedia('(prefers-color-scheme: dark)').matches) theme = 'dark';
      } catch {
        // matchMedia 不可用：保持 light。
      }
    }
    if (storedDensity === 'compact' || storedDensity === 'comfortable') {
      density = storedDensity;
    }
    document.documentElement.dataset.theme = theme;
    document.documentElement.dataset.density = density;
    const meta = document.querySelector('meta[name="color-scheme"]');
    if (meta) meta.setAttribute('content', theme);
    return { theme, density };
  } catch {
    return { theme: 'light', density: 'comfortable' };
  }
}

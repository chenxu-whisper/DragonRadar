import { useEffect, useState } from 'react';
import { Moon, Sun } from 'lucide-react';
import {
  DENSITY_KEY,
  THEME_KEY,
  readPreferences,
  systemTheme,
  type Preferences,
} from './settings';
import { persistDensity, persistTheme } from './persist';

/** 读取当前偏好；localStorage/matchMedia 任一不可用时回退 light/comfortable。 */
function readCurrent(): Preferences {
  try {
    return readPreferences(
      (key: string) => window.localStorage.getItem(key),
      systemTheme(window.matchMedia('(prefers-color-scheme: dark)').matches),
    );
  } catch {
    return { theme: 'light', density: 'comfortable' };
  }
}

/** 是否存在显式 dr:theme（无显式值时系统深浅色变化才应驱动图标/label）。 */
function hasExplicitTheme(): boolean {
  try {
    const raw = window.localStorage.getItem(THEME_KEY);
    return raw === 'light' || raw === 'dark';
  } catch {
    return false;
  }
}

/**
 * 主题/密度控件（独立懒 chunk，仅在 reset 工作台挂载）：
 * 点击写入 storage（失败则不派发、不乐观更新）→ 派发 dr:prefs-changed
 * （entry 侧 bootstrapPreferences 统一落 dataset）→ 更新本组件 state；
 * window storage 事件负责跨标签同步；matchMedia 变化在无显式主题时跟随系统。
 */
export function PreferenceControls() {
  const [prefs, setPrefs] = useState<Preferences>(readCurrent);

  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key !== null && event.key !== THEME_KEY && event.key !== DENSITY_KEY) return;
      setPrefs(readCurrent());
    };
    const onSystemTheme = () => {
      if (!hasExplicitTheme()) setPrefs(readCurrent());
    };
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    window.addEventListener('storage', onStorage);
    media.addEventListener('change', onSystemTheme);
    return () => {
      window.removeEventListener('storage', onStorage);
      media.removeEventListener('change', onSystemTheme);
    };
  }, []);

  const dark = prefs.theme === 'dark';
  const compact = prefs.density === 'compact';
  const themeLabel = dark ? '切换到浅色主题' : '切换到深色主题';
  const densityLabel = compact ? '切换为舒适密度' : '切换为紧凑密度';

  return <>
    <button
      type="button"
      className="pref-toggle theme-toggle"
      aria-pressed={dark}
      aria-label={themeLabel}
      title={themeLabel}
      onClick={() => {
        const next = dark ? 'light' : 'dark';
        if (!persistTheme(next)) return;
        setPrefs(p => ({ ...p, theme: next }));
      }}
    >{dark ? <Sun size={17} /> : <Moon size={17} />}</button>
    <button
      type="button"
      className="pref-toggle density-toggle"
      aria-pressed={compact}
      aria-label={densityLabel}
      title={densityLabel}
      onClick={() => {
        const next = compact ? 'comfortable' : 'compact';
        if (!persistDensity(next)) return;
        setPrefs(p => ({ ...p, density: next }));
      }}
    >{compact ? '舒适' : '紧凑'}</button>
  </>;
}

import {
  writeDensity,
  writeTheme,
  type DensityMode,
  type ThemeMode,
} from './settings';

/** 面板与偏好控件同源的 setItem 注入点；调用方负责外层 try/catch 语义。 */
function setPreference(key: string, value: string): void {
  window.localStorage.setItem(key, value);
}

function notifyChanged(): void {
  window.dispatchEvent(new Event('dr:prefs-changed'));
}

/**
 * 经白名单写入主题并派发同标签变更事件；storage 不可用/写失败返回 false，
 * 此时不派发、不乐观切换。与 PreferenceControls 完全同源。
 */
export function persistTheme(value: ThemeMode): boolean {
  if (!writeTheme(setPreference, value)) return false;
  notifyChanged();
  return true;
}

/** 经白名单写入密度并派发同标签变更事件；失败语义同 persistTheme。 */
export function persistDensity(value: DensityMode): boolean {
  if (!writeDensity(setPreference, value)) return false;
  notifyChanged();
  return true;
}

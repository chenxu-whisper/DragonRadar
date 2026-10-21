import { useSyncExternalStore } from 'react';
import type { ThemeMode } from '../features/preferences/settings';

/**
 * 订阅 documentElement[data-theme] 的外部存储：
 * bootstrapPreferences 与偏好控件均通过该属性落盘，MutationObserver 单通道感知变化。
 * settings.ts 保持零 react 依赖；DOM 不可用时快照回退 light。
 */
function subscribe(onChange: () => void): () => void {
  try {
    const observer = new MutationObserver(onChange);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    return () => observer.disconnect();
  } catch {
    return () => { /* document 不可用：无订阅可清理 */ };
  }
}

function getSnapshot(): ThemeMode {
  try {
    return document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
  } catch {
    return 'light';
  }
}

export function useThemeMode(): ThemeMode {
  return useSyncExternalStore(subscribe, getSnapshot, () => 'light');
}

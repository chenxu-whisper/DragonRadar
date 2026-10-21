import { useSyncExternalStore } from 'react';
import type { DensityMode, ThemeMode } from '../features/preferences/settings';

/**
 * 订阅 documentElement 偏好属性的外部存储：bootstrapPreferences 与偏好控件均通过
 * data-theme/data-density 落盘，MutationObserver 单通道感知变化。
 * settings.ts 保持零 react 依赖；DOM 不可用时快照回退默认值。
 */
function subscribe(attribute: 'data-theme' | 'data-density', onChange: () => void): () => void {
  try {
    const observer = new MutationObserver(onChange);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: [attribute] });
    return () => observer.disconnect();
  } catch {
    return () => { /* document 不可用：无订阅可清理 */ };
  }
}

// 模块级稳定订阅引用：useSyncExternalStore 直接传函数引用，
// 避免每次渲染内联创建订阅函数导致 MutationObserver 反复重连。
const subscribeTheme = (onChange: () => void) => subscribe('data-theme', onChange);
const subscribeDensity = (onChange: () => void) => subscribe('data-density', onChange);

function getThemeSnapshot(): ThemeMode {
  try {
    return document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
  } catch {
    return 'light';
  }
}

function getDensitySnapshot(): DensityMode {
  try {
    return document.documentElement.dataset.density === 'compact' ? 'compact' : 'comfortable';
  } catch {
    return 'comfortable';
  }
}

export function useThemeMode(): ThemeMode {
  return useSyncExternalStore(
    subscribeTheme,
    getThemeSnapshot,
    () => 'light',
  );
}

export function useDensityMode(): DensityMode {
  return useSyncExternalStore(
    subscribeDensity,
    getDensitySnapshot,
    () => 'comfortable',
  );
}

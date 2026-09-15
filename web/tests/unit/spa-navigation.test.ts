import { describe, expect, it } from 'vitest';
import { shouldHandleSpaClick } from '../../src/components/Common';

type Click = {
  button: number;
  ctrlKey: boolean;
  metaKey: boolean;
  shiftKey: boolean;
  altKey: boolean;
};

describe('SPA 链接点击判定', () => {
  it.each([
    ['无修饰主按钮', { button: 0, ctrlKey: false, metaKey: false, shiftKey: false, altKey: false }, true],
    ['Ctrl 点击', { button: 0, ctrlKey: true, metaKey: false, shiftKey: false, altKey: false }, false],
    ['Meta 点击', { button: 0, ctrlKey: false, metaKey: true, shiftKey: false, altKey: false }, false],
    ['Shift 点击', { button: 0, ctrlKey: false, metaKey: false, shiftKey: true, altKey: false }, false],
    ['Alt 点击', { button: 0, ctrlKey: false, metaKey: false, shiftKey: false, altKey: true }, false],
    ['辅助按钮', { button: 1, ctrlKey: false, metaKey: false, shiftKey: false, altKey: false }, false],
  ] satisfies [string, Click, boolean][])('%s', (_name, event, expected) => {
    expect(shouldHandleSpaClick(event)).toBe(expected);
  });
});

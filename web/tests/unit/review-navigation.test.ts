import { afterEach, describe, expect, it } from 'vitest';
import {
  allowNavigation,
  captureNavigationContext,
  isNavigationContextCurrent,
  registerLeaveGuard,
} from '../../src/features/review/navigation';
import { sameCommandContext } from '../../src/features/review/useCommand';

let release: (() => void) | undefined;
afterEach(() => { release?.(); release = undefined; });

describe('私人命令导航代次', () => {
  it.each(['普通新建', '快捷新建', 'fork'])('%s 的迟到成功不能越过已接受导航', () => {
    const started = captureNavigationContext();
    release = registerLeaveGuard(() => true);

    expect(allowNavigation()).toBe(true);
    expect(isNavigationContextCurrent(started)).toBe(false);
  });

  it('取消离开不推进代次，原操作仍可导航', () => {
    const started = captureNavigationContext();
    release = registerLeaveGuard(() => false);

    expect(allowNavigation()).toBe(false);
    expect(isNavigationContextCurrent(started)).toBe(true);
  });
});

describe('私人命令资源上下文', () => {
  it.each([
    ['slot', { slot: 'watch:b', owner: 'owner-a', epoch: 3 }],
    ['owner', { slot: 'watch:a', owner: 'owner-b', epoch: 3 }],
    ['session epoch', { slot: 'watch:a', owner: 'owner-a', epoch: 4 }],
  ])('%s 变化后旧命令结果不能落地', (_field, current) => {
    expect(sameCommandContext(
      { slot: 'watch:a', owner: 'owner-a', epoch: 3 },
      current,
    )).toBe(false);
  });

  it('完整资源上下文未变化时允许命令结果落地', () => {
    expect(sameCommandContext(
      { slot: 'watch:a', owner: 'owner-a', epoch: 3 },
      { slot: 'watch:a', owner: 'owner-a', epoch: 3 },
    )).toBe(true);
  });
});

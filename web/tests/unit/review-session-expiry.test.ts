import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ReactElement } from 'react';
import { SessionProvider } from '../../src/features/review/SessionProvider';
import { SessionStore } from '../../src/features/review/session';

const hooks = vi.hoisted(() => ({
  store: undefined as SessionStore | undefined,
  effects: [] as (() => void | (() => void))[],
}));

// Exercise the real provider expiry effect without executing unrelated DOM effects.
vi.mock('react', async importOriginal => ({
  ...await importOriginal<typeof import('react')>(),
  useState: (initial: unknown) => [
    typeof initial === 'function' ? hooks.store : initial, vi.fn(),
  ],
  useRef: (initial: unknown) => ({ current: initial }),
  useCallback: (callback: unknown) => callback,
  useSyncExternalStore: (_subscribe: unknown, snapshot: () => number) => snapshot(),
  useEffect: (effect: () => void | (() => void)) => { hooks.effects.push(effect); },
}));

const now = Date.UTC(2026, 10, 23, 12);
const maximumDelay = 2147483647;
const session = (deadline: number, id = 'session-a') => ({
  owner_id: 'owner-a', session_id: id, expires_at: new Date(deadline).toISOString(),
});
let cleanups: (() => void)[] = [];

function mountExpiry() {
  hooks.effects = [];
  const tree = SessionProvider({ children: null }) as ReactElement<{ value: { store: SessionStore } }>;
  expect(tree.props.value.store).toBe(hooks.store);
  expect(hooks.effects).toHaveLength(3);
  const cleanup = hooks.effects[1]();
  if (typeof cleanup !== 'function') throw new Error('Authenticated expiry effect must return cleanup');
  cleanups.push(cleanup);
  return cleanup;
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(now);
  hooks.store = new SessionStore();
  cleanups = [];
});
afterEach(() => {
  for (const cleanup of cleanups) cleanup();
  hooks.store = undefined;
  hooks.effects = [];
  vi.useRealTimers();
});

describe('private session automatic expiry', () => {
  it('rearms an early wakeup and automatically locks at the deadline without another event', () => {
    const store = hooks.store!;
    store.authenticate(session(now + 1000));
    const controller = store.controller();
    mountExpiry();
    const clock = vi.spyOn(Date, 'now').mockReturnValue(now + 999);
    vi.advanceTimersByTime(1000);
    expect(store.locked).toBe(false);
    expect(controller.signal.aborted).toBe(false);
    clock.mockRestore();
    vi.advanceTimersByTime(1);
    expect(store.locked).toBe(true);
    expect(controller.signal.aborted).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('continues after the maximum timer delay and never locks before the actual deadline', () => {
    const store = hooks.store!;
    store.authenticate(session(now + maximumDelay + 1000));
    mountExpiry();
    vi.advanceTimersByTime(maximumDelay);
    expect(store.locked).toBe(false);
    vi.advanceTimersByTime(999);
    expect(store.locked).toBe(false);
    vi.advanceTimersByTime(1);
    expect(store.locked).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('cleanup cancels the latest rearmed timer without locking or aborting the live session', () => {
    const store = hooks.store!;
    store.authenticate(session(now + 1000));
    const controller = store.controller();
    const cleanup = mountExpiry();
    const clock = vi.spyOn(Date, 'now').mockReturnValue(now + 999);
    vi.advanceTimersByTime(1000);
    clock.mockRestore();
    cleanup();
    expect(vi.getTimerCount()).toBe(0);
    vi.advanceTimersByTime(1000);
    expect(store.locked).toBe(false);
    expect(controller.signal.aborted).toBe(false);
  });

  it('session replacement cancels the old timer and uses only the new deadline', () => {
    const store = hooks.store!;
    store.authenticate(session(now + 1000));
    const cleanup = mountExpiry();
    store.authenticate(session(now + 2000, 'session-b'));
    cleanup();
    mountExpiry();
    const controller = store.controller();
    expect(vi.getTimerCount()).toBe(1);
    vi.advanceTimersByTime(1000);
    expect(store.locked).toBe(false);
    expect(controller.signal.aborted).toBe(false);
    vi.advanceTimersByTime(1000);
    expect(store.locked).toBe(true);
    expect(controller.signal.aborted).toBe(true);
  });
});

import { afterEach, describe, expect, it, vi } from 'vitest';

const harness = vi.hoisted(() => ({
  effects: [] as Array<() => void | (() => void)>,
  snapshotAvailable: true,
}));

// Exercise the actual Workbench effect bodies without a DOM renderer/server.
vi.mock('react', async importOriginal => ({
  ...await importOriginal<typeof import('react')>(),
  useState: (initial: unknown) => [
    typeof initial === 'function' ? initial() : initial,
    vi.fn(),
  ],
  useRef: (initial: unknown) => ({ current: initial }),
  useEffect: (effect: () => void | (() => void)) => { harness.effects.push(effect); },
}));
vi.mock('../../src/hooks/useLocation', async () => {
  const { parseState } = await import('../../src/state');
  const state = parseState('?view=analysis&snapshot=11111111-1111-4111-8111-111111111111', false, true).state;
  return {
    useLocation: () => ({
      state, patch: vi.fn(), destination: () => state, urlFor: () => '/',
    }),
  };
});
vi.mock('../../src/hooks/useResource', async () => {
  const { resetSummary, snapshot } = await import('../fixtures/market');
  return {
  AccessProvider: () => null,
  useAccess: () => ({ denied: false, retry: vi.fn() }),
  useResource: (path?: string) => ({
    data: harness.snapshotAvailable && path === '/api/snapshots/11111111-1111-4111-8111-111111111111'
      ? resetSummary({ ...snapshot, snapshot_id: '11111111-1111-4111-8111-111111111111' })
      : undefined,
    loading: false,
  }),
  };
});

const cleanups: Array<() => void> = [];
afterEach(() => {
  cleanups.splice(0).forEach(cleanup => cleanup());
  harness.effects = [];
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.resetModules();
});

async function runWorkbench(reset: string, sharing: string, snapshotAvailable = true) {
  harness.snapshotAvailable = snapshotAvailable;
  vi.stubEnv('VITE_RESET_WORKBENCH', reset);
  vi.stubEnv('VITE_PUBLIC_SHARING', sharing);
  vi.stubEnv('VITE_PRIVATE_REVIEW', '0');
  vi.stubGlobal('window', {
    location: { search: '?view=analysis', pathname: '/' },
    addEventListener: vi.fn(), removeEventListener: vi.fn(),
    matchMedia: () => ({ addEventListener: vi.fn(), removeEventListener: vi.fn() }),
  });
  vi.stubGlobal('document', { cookie: '' });
  const fetcher = vi.fn().mockResolvedValue(new Response('{}', { status: 404 }));
  vi.stubGlobal('fetch', fetcher);
  const { Workbench } = await import('../../src/App');
  const tree = Workbench({});
  const { ResetBacktestPanel } = await import('../../src/pages/Analysis');
  const findPanel = (node: unknown): { snapshotId: string } | undefined => {
    if (Array.isArray(node)) return node.map(findPanel).find(Boolean);
    if (!node || typeof node !== 'object' || !('props' in node)) return undefined;
    const element = node as { type: unknown; props: { children?: unknown; snapshotId: string } };
    if (ResetBacktestPanel && element.type === ResetBacktestPanel) return element.props;
    return findPanel(element.props.children);
  };
  const panelProps = findPanel(tree);
  if (reset === '1' && sharing !== '1') {
    expect(panelProps).toMatchObject({ snapshotId: snapshotAvailable ? '11111111-1111-4111-8111-111111111111' : '' });
    const { default: OwnerBacktestWorkbench } = await import('../../src/features/research/OwnerBacktestWorkbench');
    OwnerBacktestWorkbench(panelProps!);
  } else {
    expect(panelProps).toBeUndefined();
  }
  for (const effect of harness.effects) {
    const cleanup = effect();
    if (cleanup) cleanups.push(cleanup);
  }
  // Drain dynamic imports deterministically, including the real manifest transport.
  await import('../../src/features/preferences/search-index');
  await import('../../src/features/research/manifest-resource');
  await new Promise(resolve => setImmediate(resolve));
  return fetcher;
}

describe('owner research compile-time sharing isolation', () => {
  it.each([
    ['default', '0', '0'],
    ['default sharing', '0', '1'],
    ['reset sharing', '1', '1'],
  ])('%s analysis never requests an owner research resource', async (_name, reset, sharing) => {
    const fetcher = await runWorkbench(reset, sharing);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('owner reset analysis still subscribes to the selected manifest', async () => {
    const fetcher = await runWorkbench('1', '0');
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][0]).toBe('/api/research/manifests/11111111-1111-4111-8111-111111111111');
  });

  it('owner reset analysis does not subscribe using an unverified URL snapshot', async () => {
    const fetcher = await runWorkbench('1', '0', false);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('reset sharing does not expose a lazy owner backtest component', async () => {
    vi.stubEnv('VITE_RESET_WORKBENCH', '1');
    vi.stubEnv('VITE_PUBLIC_SHARING', '1');
    const { ResetBacktestPanel } = await import('../../src/pages/Analysis');
    expect(ResetBacktestPanel).toBeNull();
  });
});

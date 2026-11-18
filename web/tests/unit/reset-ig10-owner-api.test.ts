import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ReactElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import CitationSource from '../../src/features/ai/CitationSource';
import type { AITask } from '../../src/features/ai/client';
import { ownerRequest } from '../../src/features/news/owner-api';

interface PanelHooks {
  slots: unknown[];
  cursor: number;
  effects: (() => void | (() => void))[];
  cleanups: (() => void)[];
}

const harness = vi.hoisted(() => ({
  active: undefined as PanelHooks | undefined,
  panels: [] as PanelHooks[],
}));

// Persist hooks separately for each real CitationSource instance in the node test environment.
vi.mock('react', async importOriginal => ({
  ...await importOriginal<typeof import('react')>(),
  useState: (initial: unknown) => {
    const panel = harness.active!;
    const index = panel.cursor++;
    if (!(index in panel.slots)) panel.slots[index] = initial;
    return [panel.slots[index], (value: unknown) => { panel.slots[index] = value; }];
  },
  useRef: (initial: unknown) => {
    const panel = harness.active!;
    const index = panel.cursor++;
    if (!(index in panel.slots)) panel.slots[index] = { current: initial };
    return panel.slots[index];
  },
  useEffect: (effect: () => void | (() => void)) => {
    const panel = harness.active!;
    const index = panel.cursor++;
    if (!(index in panel.slots)) {
      panel.slots[index] = true;
      panel.effects.push(effect);
    }
  },
}));

const owner = '00000000-0000-4000-8000-000000000001';
const snapshot = '00000000-0000-4000-8000-000000000002';
const task: AITask = {
  task_id: '00000000-0000-4000-8000-000000000003',
  task_type: 'summarize', snapshot_id: snapshot, as_of: '2026-09-10T10:00:00Z',
  version: 'v1', status: 'succeeded', verification_status: 'passed',
  human_review_status: 'unreviewed', processing_overdue: false,
  content_draft: {
    facts: [], inferences: [], missing_evidence: [], citations: [{
      evidence_id: 'ev-summary', evidence_position: '/input/article/summary',
      source_identity: 'news:licensed:v1', available_at: '2026-09-10T09:00:00Z',
      value: 'Authorized source text',
    }],
  },
  revision: 3, content_draft_source: 'provider', content_draft_created_at: null,
  ai_generated: true, model_version: null, prompt_version: null,
  retrieval_version: 'r1', provider: null, token_usage: null, cost: null,
  latency_ms: null, failure: null,
};
const evidencePath = `/api/ai/task/${task.task_id}/evidence/ev-summary?revision=3&snapshot_id=${snapshot}`;
const session = {
  owner_id: owner, session_id: '00000000-0000-4000-8000-000000000004',
  expires_at: '2099-01-01T00:00:00Z',
};
const source = {
  owner_id: owner, task_id: task.task_id, revision: task.revision,
  snapshot_id: snapshot, as_of: task.as_of, version: task.version,
  ...task.content_draft!.citations[0], status: 'ready', reason: null,
  locator: {
    kind: 'news_article_field', field: 'summary', index: null,
    article_id: '00000000-0000-4000-8000-000000000005',
    article_revision_id: '00000000-0000-4000-8000-000000000006',
    article_revision: 1,
  },
};

function findButton(node: unknown): ReactElement<{ onClick: (event: unknown) => void }> | undefined {
  if (Array.isArray(node)) return node.map(findButton).find(Boolean);
  if (!node || typeof node !== 'object' || !('props' in node)) return undefined;
  const element = node as ReactElement<{ children?: unknown; onClick: (event: unknown) => void }>;
  return element.type === 'button'
    ? element : findButton(element.props.children);
}

function panel() {
  const hooks: PanelHooks = { slots: [], cursor: 0, effects: [], cleanups: [] };
  harness.panels.push(hooks);
  const render = () => {
    harness.active = hooks;
    hooks.cursor = 0;
    const tree = CitationSource({ task });
    for (const effect of hooks.effects.splice(0)) {
      const cleanup = effect();
      if (cleanup) hooks.cleanups.push(cleanup);
    }
    return tree;
  };
  render();
  return {
    markup: () => renderToStaticMarkup(render()),
    open: async () => {
      findButton(render())!.props.onClick({ currentTarget: { focus: vi.fn() } });
      await vi.waitFor(() => {
        expect(renderToStaticMarkup(render())).not.toContain('正在核对来源显示授权');
      });
    },
  };
}

beforeEach(() => { vi.stubGlobal('window', new EventTarget()); });
afterEach(() => {
  for (const hooks of harness.panels) for (const cleanup of hooks.cleanups) cleanup();
  harness.panels = []; harness.active = undefined;
  vi.unstubAllGlobals();
});

describe('IG10 owner source-denial revocation', () => {
  it.each(['FORBIDDEN', 'source_forbidden'])(
    'clears all displayed citation panels when panel B receives 403 %s',
    async code => {
      let denied = false;
      const fetcher = vi.fn(async (path: string) => path === '/api/owner/v1/session'
        ? Response.json(session)
        : denied ? Response.json({ code, message: 'Source access denied' }, { status: 403 })
          : Response.json(source));
      vi.stubGlobal('fetch', fetcher);
      const a = panel(), b = panel();
      await a.open(); await b.open();
      expect(a.markup()).toContain('<mark>Authorized source text</mark>');
      expect(b.markup()).toContain('<mark>Authorized source text</mark>');
      denied = true;
      await b.open();
      expect(fetcher.mock.calls.filter(([path]) => path === evidencePath)).toHaveLength(3);
      for (const current of [a, b]) {
        expect(current.markup()).not.toContain('aria-label="引用来源"');
        expect(current.markup()).not.toContain('Authorized source text');
      }
    },
  );

  it('aborts a pending citation panel and drops its late successful response after source denial', async () => {
    let release!: (response: Response) => void;
    let started!: () => void;
    const entered = new Promise<void>(resolve => { started = resolve; });
    let reads = 0;
    let pendingSignal: AbortSignal | undefined;
    vi.stubGlobal('fetch', vi.fn(async (path: string, options: RequestInit) => {
      if (path === '/api/owner/v1/session') return Response.json(session);
      reads++;
      if (reads === 1) return Response.json(source);
      pendingSignal = options.signal as AbortSignal;
      started();
      return new Promise<Response>(resolve => { release = resolve; });
    }));
    const a = panel(), b = panel();
    await a.open();
    const pending = b.open();
    await entered;
    expect(b.markup()).toContain('aria-label="引用来源"');
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ code: 'FORBIDDEN' }, { status: 403 })));
    await expect(ownerRequest(evidencePath, new AbortController().signal)).rejects.toMatchObject({ status: 403 });
    expect(pendingSignal?.aborted).toBe(true);
    for (const current of [a, b]) expect(current.markup()).not.toContain('aria-label="引用来源"');
    release(Response.json(source));
    await pending;
    for (const current of [a, b]) {
      expect(current.markup()).not.toContain('aria-label="引用来源"');
      expect(current.markup()).not.toContain('Authorized source text');
    }
  });

  it.each(['csrf_forbidden', 'origin_forbidden', 'unknown', null, [], { code: 403 }])(
    'does not broadcast source revocation for unrelated 403 body %j',
    async body => {
      const revoked = vi.fn();
      window.addEventListener('private-source-revoked', revoked);
      vi.stubGlobal('fetch', vi.fn(async () => Response.json(
        typeof body === 'string' ? { code: body } : body, { status: 403 },
      )));
      await expect(ownerRequest(evidencePath, new AbortController().signal)).rejects.toMatchObject({ status: 403 });
      expect(revoked).not.toHaveBeenCalled();
    },
  );

  it('retains HTTP 403 for malformed error JSON without a revocation broadcast', async () => {
    const revoked = vi.fn();
    window.addEventListener('private-source-revoked', revoked);
    vi.stubGlobal('fetch', vi.fn(async () => new Response('not JSON', { status: 403 })));
    await expect(ownerRequest(evidencePath, new AbortController().signal)).rejects.toMatchObject({ status: 403 });
    expect(revoked).not.toHaveBeenCalled();
  });

  it('keeps 401 session-lock behavior distinct from source revocation', async () => {
    const locked = vi.fn(), revoked = vi.fn();
    window.addEventListener('private-session-lock', locked);
    window.addEventListener('private-source-revoked', revoked);
    vi.stubGlobal('fetch', vi.fn(async () => new Response('', { status: 401 })));
    await expect(ownerRequest('/api/owner/v1/session', new AbortController().signal)).rejects.toMatchObject({ status: 401 });
    expect(locked).toHaveBeenCalledTimes(1);
    expect(revoked).not.toHaveBeenCalled();
  });

  it('broadcasts source denial before rejecting without echoing the server message', async () => {
    const order: string[] = [];
    window.addEventListener('private-source-revoked', () => order.push('revoked'));
    vi.stubGlobal('fetch', vi.fn(async () => Response.json(
      { code: 'FORBIDDEN', message: 'Sensitive source content' }, { status: 403 },
    )));
    const failure = await ownerRequest(evidencePath, new AbortController().signal).catch(error => {
      order.push('rejected');
      return error;
    });
    expect(order).toEqual(['revoked', 'rejected']);
    expect(failure).toMatchObject({ status: 403, uncertain: false });
    if (!(failure instanceof Error)) throw new Error('Expected owner read error');
    expect(failure.message).not.toContain('Sensitive source content');
  });
});

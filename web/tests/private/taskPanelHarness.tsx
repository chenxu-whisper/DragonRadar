import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';

import { TaskPanel, type TaskPanelProps } from '../../src/features/ai/TaskPanel';
import type { AITask } from '../../src/features/ai/client';

type Operation = 'submit' | 'cancel' | 'review' | 'adopt';

interface PendingRequest {
  path: string;
  signal?: AbortSignal;
  resolve: (response: Response) => void;
}

interface PendingAdoption {
  signal: AbortSignal;
  resolve: (message: string) => void;
}

interface TaskPanelHarness {
  requests: PendingRequest[];
  adoption?: PendingAdoption;
  rerender: (snapshotId: string) => Promise<void>;
  unmount: () => Promise<void>;
  task: (status: 'running' | 'succeeded') => AITask;
  response: (body: unknown, status?: number) => Response;
}

declare global {
  interface Window {
    taskPanelHarness: TaskPanelHarness;
  }
}

const firstSnapshot = '00000000-0000-4000-8000-000000000001';
const taskId = '00000000-0000-4000-8000-000000000099';
const draft = {
  facts: [],
  inferences: [{
    text: 'The cited evidence supports owner review.',
    claim: { kind: 'interpretation' as const },
    confidence: { kind: 'explanatory' as const, value: '0.70' },
    evidence_ids: ['ev-1'],
  }],
  missing_evidence: [],
  citations: [{
    evidence_id: 'ev-1',
    evidence_position: '/input/value',
    source_identity: 'market:TEST:v1',
    available_at: '2026-09-10T08:01:00Z',
    value: 'published',
  }],
};

function task(status: 'running' | 'succeeded'): AITask {
  const succeeded = status === 'succeeded';
  return {
    task_id: taskId,
    task_type: 'topic_suggest',
    snapshot_id: firstSnapshot,
    as_of: '2026-09-10T08:01:00Z',
    version: 'v1',
    status,
    verification_status: succeeded ? 'passed' : 'pending',
    human_review_status: 'unreviewed',
    processing_overdue: false,
    content_draft: succeeded ? draft : null,
    revision: succeeded ? 1 : null,
    content_draft_source: succeeded ? 'provider' : null,
    content_draft_created_at: succeeded ? '2026-09-10T08:01:01Z' : null,
    ai_generated: succeeded ? true : null,
    model_version: succeeded ? 'model-v1' : null,
    prompt_version: succeeded ? 'prompt-v1' : null,
    retrieval_version: 'r1',
    provider: succeeded ? 'fake' : null,
    token_usage: succeeded ? 1 : null,
    cost: succeeded ? '0.01' : null,
    latency_ms: succeeded ? 1 : null,
    failure: null,
  };
}

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

export async function mountTaskPanelHarness(operation: Operation): Promise<void> {
  document.body.replaceChildren();
  const requests: PendingRequest[] = [];
  const host = document.createElement('div');
  document.body.append(host);
  const root: Root = createRoot(host);
  let adoption: PendingAdoption | undefined;
  let snapshotId = firstSnapshot;
  const originalFetch = window.fetch.bind(window);

  window.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
    const path = typeof input === 'string' ? input : input.toString();
    if (!path.startsWith('/api/ai/')) return originalFetch(input, init);
    if (path === '/api/ai/topic-suggest' && operation !== 'submit') {
      return Promise.resolve(response(task(operation === 'cancel' ? 'running' : 'succeeded')));
    }
    return new Promise<Response>(resolve => {
      requests.push({ path, signal: init?.signal || undefined, resolve });
    });
  }) as typeof fetch;

  const props = (): TaskPanelProps => ({
    taskType: 'topic_suggest',
    snapshotId,
    asOf: '2026-09-10T08:01:00Z',
    version: 'v1',
    date: snapshotId === firstSnapshot ? '2026-09-10' : '2026-09-11',
    onAdopt: operation === 'adopt'
      ? (_text, context) => new Promise<string>(resolve => {
        adoption = { signal: context.signal, resolve };
        window.taskPanelHarness.adoption = adoption;
      })
      : undefined,
  });
  const render = async () => {
    await act(async () => root.render(<TaskPanel {...props()} />));
  };

  window.taskPanelHarness = {
    requests,
    get adoption() { return adoption; },
    set adoption(value) { adoption = value; },
    rerender: async nextSnapshot => {
      snapshotId = nextSnapshot;
      await render();
    },
    unmount: async () => {
      await act(async () => root.unmount());
    },
    task,
    response,
  };
  await render();
}

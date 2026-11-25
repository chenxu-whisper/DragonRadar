import { createElement } from 'react';
import { renderToReadableStream } from 'react-dom/server';
import { readFileSync } from 'node:fs';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { AITask } from '../../src/features/ai/client';
import TaskPanel, { type TaskPanelProps } from '../../src/features/ai/TaskPanel';

const seeded = vi.hoisted(() => ({
  task: undefined as AITask | undefined, used: false, publication: vi.fn(),
}));
vi.mock('react', async importOriginal => {
  const actual = await importOriginal<typeof import('react')>();
  return {
    ...actual,
    useState: (initial: unknown) => {
      if (initial === undefined && !seeded.used) {
        seeded.used = true;
        return actual.useState(seeded.task);
      }
      return actual.useState(initial);
    },
  };
});
// Isolate the owned shared mount from B3's independently tested write behavior.
vi.mock('../../src/features/ai/PublicationPanel', () => ({
  default: (props: unknown) => {
    seeded.publication(props);
    return createElement('section', { 'aria-label': 'publication-wiring-probe' });
  },
}));

const props: TaskPanelProps = {
  taskType: 'daily_review', snapshotId: '00000000-0000-4000-8000-000000000141',
  asOf: '2026-09-10T10:00:00Z', version: 'v1', date: '2026-09-10',
  marketContext: 'Frozen market context',
};
const accepted: AITask = {
  task_id: '00000000-0000-4000-8000-000000000192', task_type: 'daily_review',
  snapshot_id: props.snapshotId, as_of: props.asOf, version: props.version,
  status: 'succeeded', verification_status: 'passed', human_review_status: 'accepted',
  revision: 4, content_draft: { facts: [], inferences: [], citations: [], missing_evidence: [] },
  processing_overdue: false, content_draft_source: 'provider',
  content_draft_created_at: props.asOf, ai_generated: true, model_version: 'synthetic',
  prompt_version: 'synthetic', retrieval_version: 'synthetic', provider: 'synthetic',
  token_usage: null, cost: null, latency_ms: null, failure: null,
};
beforeEach(() => {
  seeded.used = false;
  seeded.publication.mockClear();
});

async function render(task: AITask | undefined, overrides: Partial<TaskPanelProps> = {}) {
  seeded.task = task;
  const stream = await renderToReadableStream(createElement(TaskPanel, { ...props, ...overrides }));
  await stream.allReady;
  return new Response(stream).text();
}

describe('parent B3 private task publication wiring', () => {
  it.each(['daily_review', 'summarize'] as const)('passes exact accepted %s identity into the publication panel', async taskType => {
    const markup = await render({ ...accepted, task_type: taskType }, { taskType });
    expect(markup).toContain('publication-wiring-probe');
    expect(seeded.publication).toHaveBeenCalledWith({
      taskId: accepted.task_id, revision: 4, snapshotId: props.snapshotId, date: props.date,
    });
  });

  it.each([
    undefined,
    { ...accepted, human_review_status: 'unreviewed' },
    { ...accepted, human_review_status: 'edited' },
    { ...accepted, human_review_status: 'rejected' },
    { ...accepted, verification_status: 'failed' },
    { ...accepted, verification_status: 'pending' },
    { ...accepted, verification_status: 'needs_human' },
    { ...accepted, status: 'running' },
    { ...accepted, revision: null },
    { ...accepted, content_draft: null },
  ] as (AITask | undefined)[])('does not offer publication without an exact accepted verified revision: %j', async task => {
    expect(await render(task)).not.toContain('publication-wiring-probe');
    expect(seeded.publication).not.toHaveBeenCalled();
  });

  it.each(['topic_suggest', 'explain_stock'] as const)('does not promote %s tasks', async taskType => {
    expect(await render({ ...accepted, task_type: taskType }, { taskType })).not.toContain('publication-wiring-probe');
  });

  it.each([
    { snapshotId: '00000000-0000-4000-8000-000000000193' },
    { asOf: '2026-09-11T10:00:00Z' },
    { version: 'v2' },
    { taskType: 'summarize' as const },
  ])('hides publication for a stale task context: %j', async overrides => {
    expect(await render(accepted, overrides)).not.toContain('publication-wiring-probe');
    expect(seeded.publication).not.toHaveBeenCalled();
  });

  it('imports publication only dynamically in the already private task module', () => {
    const source = readFileSync('src/features/ai/TaskPanel.tsx', 'utf8');
    expect(source).toContain("import('./PublicationPanel')");
    expect(source).not.toMatch(/from\s+['"]\.\/PublicationPanel/);
    expect(source).not.toContain('publication-client');
  });
});

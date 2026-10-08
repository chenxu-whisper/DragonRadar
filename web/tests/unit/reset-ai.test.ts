import 'fake-indexeddb/auto';
import { afterEach, describe, expect, test, vi } from 'vitest';

import {
  AIClientError,
  aiRequest,
  createAICommand,
  validateCancellationReceipt,
  validateReviewReceipt,
  validateTask,
} from '../../src/features/ai/client';
import {
  adoptionConflict,
  buildNotesSelection,
  pollDecision,
  taskPresentation,
} from '../../src/features/ai/tasks';
import { AdoptionCommandCache } from '../../src/features/local-review/adoption-command';
import { LocalRepository, type SaveNote } from '../../src/repositories/local/repository';

const task = {
  task_id: '00000000-0000-4000-8000-000000000001',
  task_type: 'daily_review',
  snapshot_id: '00000000-0000-4000-8000-000000000002',
  as_of: '2026-09-10T08:01:00Z',
  version: 'v1',
  status: 'running',
  verification_status: 'pending',
  human_review_status: 'unreviewed',
  processing_overdue: false,
  content_draft: null,
  revision: null,
  content_draft_source: null,
  content_draft_created_at: null,
  ai_generated: null,
  model_version: null,
  prompt_version: null,
  retrieval_version: 'r1',
  provider: null,
  token_usage: null,
  cost: null,
  latency_ms: null,
  failure: null,
} as const;

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('dedicated AI client', () => {
  test('uses same-origin cookies, no-store and CSRF only for writes', async () => {
    vi.stubGlobal('document', {
      cookie: '__Host-dragon-private-csrf=csrf-token',
    });
    const fetcher = vi.fn().mockImplementation(async () =>
      new Response(JSON.stringify(task), {
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    vi.stubGlobal('fetch', fetcher);

    await aiRequest(`/api/ai/task/${task.task_id}`);
    await aiRequest('/api/ai/daily-review', {
      method: 'POST',
      body: { idempotency_key: 'stable-key' },
    });

    expect(fetcher.mock.calls[0][1]).toMatchObject({
      method: 'GET',
      credentials: 'same-origin',
      cache: 'no-store',
    });
    expect(fetcher.mock.calls[0][1].headers).not.toHaveProperty('X-CSRF-Token');
    expect(fetcher.mock.calls[1][1].headers).toMatchObject({
      'Content-Type': 'application/json',
      'X-CSRF-Token': 'csrf-token',
    });
  });

  test('rejects non-AI paths and malformed task responses', async () => {
    await expect(aiRequest('/api/owner/v1/session')).rejects.toBeInstanceOf(
      AIClientError,
    );
    expect(() => validateTask({ ...task, status: 'invented' })).toThrow(
      'AI task response',
    );
    expect(() =>
      validateTask({ ...task, verification_status: 'passed', content_draft: [] }),
    ).toThrow('AI task response');
    expect(() => validateTask({
      ...task,
      content_draft: {
        facts: [{
          text: 'fact',
          evidence_id: 'ev-1',
          evidence_position: '/input/value',
          value: 'value',
          provider_validation: true,
        }],
        inferences: [],
        missing_evidence: [],
        citations: [],
      },
      revision: 1,
      content_draft_source: 'provider',
      content_draft_created_at: '2026-09-10T08:01:02Z',
      ai_generated: true,
    })).toThrow('AI task response');
  });

  test('times out ignored transports and preserves one key across uncertain retries', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})));
    const command = createAICommand('/api/ai/daily-review', { date: '2026-09-10' });
    const first = command.body.idempotency_key;
    const promise = aiRequest(command.path, {
      method: 'POST',
      body: command.body,
      timeoutMs: 50,
    });
    const assertion = expect(promise).rejects.toMatchObject({
      status: 408,
      uncertain: true,
    });
    await vi.advanceTimersByTimeAsync(50);
    await assertion;

    expect(command.body.idempotency_key).toBe(first);
    expect(createAICommand(command.path, command.body, first).body.idempotency_key)
      .toBe(first);
  });

  test('classifies 409 for replay-safe cancel and review handling', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ code: 'VERSION_CONFLICT' }), { status: 409 }),
      ),
    );
    const failure = aiRequest(`/api/ai/task/${task.task_id}/cancel`, {
      method: 'POST',
      body: { idempotency_key: 'cancel-key' },
    });
    await expect(failure).rejects.toMatchObject({
      status: 409,
      replayableConflict: true,
      uncertain: false,
    });
  });

  test('strictly validates cancel and review receipts', () => {
    expect(() => validateCancellationReceipt({
      task_id: task.task_id,
      status: 'cancelled',
      idempotency_key: 'cancel-key',
      accepted_at: 'not-a-date',
    })).toThrow('receipt');
    expect(() => validateReviewReceipt({
      task_id: task.task_id,
      human_review_status: 'accepted',
      revision: 1,
      idempotency_key: 'review-key',
      verification_status: 'passed',
      note: 'ok',
      forged: true,
    })).toThrow('receipt');
  });

  test('requires exact numeric and structured inference metadata', () => {
    const content = {
      facts: [{
        text: 'The published close was 12.30.',
        evidence_id: 'ev-close',
        evidence_position: '/input/stock/close',
        value: '12.30',
        unit: 'currency/share',
        precision: 2,
      }],
      inferences: [{
        text: 'The evidence supports owner review.',
        claim: { kind: 'interpretation' },
        confidence: { kind: 'explanatory', value: '0.70' },
        evidence_ids: ['ev-close'],
      }],
      missing_evidence: [],
      citations: [{
        evidence_id: 'ev-close',
        evidence_position: '/input/stock/close',
        source_identity: 'market:TEST001:v1',
        available_at: '2026-09-10T08:01:00Z',
        value: '12.30',
        unit: 'currency/share',
        precision: 2,
      }],
    };
    const complete = {
      ...task,
      status: 'succeeded',
      verification_status: 'passed',
      content_draft: content,
      revision: 1,
      content_draft_source: 'provider',
      content_draft_created_at: '2026-09-10T08:01:02Z',
      ai_generated: true,
    };

    expect(validateTask(complete).content_draft).toEqual(content);
    expect(() => validateTask({
      ...complete,
      content_draft: {
        ...content,
        facts: [{
          text: 'The published close was 12.30.',
          evidence_id: 'ev-close',
          evidence_position: '/input/stock/close',
          value: '12.30',
        }],
      },
    })).toThrow('AI task response');
    expect(() => validateTask({
      ...complete,
      content_draft: {
        ...content,
        inferences: [{
          text: 'Legacy scalar confidence.',
          confidence: '0.70',
          evidence_ids: ['ev-close'],
        }],
      },
    })).toThrow('AI task response');
  });
});

describe('AI task and notes state', () => {
  const adoptionPayload = {
    expected_revision: 0,
    snapshot_id: 'snapshot-1',
    trade_date: '2026-09-10',
    security_id: 'TEST001',
    content: {
      title: 'AI draft',
      content: 'Exact payload',
      judgment: '',
      evidence: '',
      invalid_condition: '',
      verify_date: '',
    },
  };

  test('replays the exact adoption command after a lost response without duplication', async () => {
    const repo = new LocalRepository(`adoption-${crypto.randomUUID()}`);
    const commands = new AdoptionCommandCache();
    const sent: SaveNote[] = [];
    let loseResponse = true;
    const transport = {
      putNote: async (command: SaveNote) => {
        sent.push(command);
        const result = await repo.putNote(command);
        if (loseResponse) {
          loseResponse = false;
          throw new Error('response lost after commit');
        }
        return result;
      },
    };

    await expect(commands.execute(transport, {
      taskRevision: 7,
      strategy: 'save_as_new',
      payload: adoptionPayload,
    })).rejects.toThrow('response lost');
    const replay = await commands.execute(transport, {
      taskRevision: 7,
      strategy: 'save_as_new',
      payload: adoptionPayload,
    });

    expect(sent[1]).toEqual(sent[0]);
    expect(replay.revision).toBe(1);
    expect(await repo.revisions(sent[0].note_id)).toHaveLength(1);
    repo.close();
  });

  test('changes adoption identity when revision, strategy, or exact payload changes', async () => {
    const commands = new AdoptionCommandCache();
    const sent: SaveNote[] = [];
    const uncertain = {
      putNote: async (command: SaveNote) => {
        sent.push(command);
        throw new Error('uncertain');
      },
    };
    const run = (taskRevision: number, strategy: 'replace' | 'merge', content: string) =>
      commands.execute(uncertain, {
        taskRevision,
        strategy,
        payload: {
          ...adoptionPayload,
          content: { ...adoptionPayload.content, content },
        },
      }).catch(() => undefined);

    await run(1, 'replace', 'same');
    await run(1, 'replace', 'same');
    await run(2, 'replace', 'same');
    await run(2, 'merge', 'same');
    await run(2, 'merge', 'changed');
    const { security_id: _securityId, ...withoutSecurity } = adoptionPayload;
    await commands.execute(uncertain, {
      taskRevision: 3,
      strategy: 'replace',
      payload: withoutSecurity,
    }).catch(() => undefined);
    await commands.execute(uncertain, {
      taskRevision: 3,
      strategy: 'replace',
      payload: { ...withoutSecurity, security_id: undefined },
    }).catch(() => undefined);

    expect(sent[1]).toEqual(sent[0]);
    for (const command of sent.slice(2)) {
      expect(command.note_id).not.toBe(sent[0].note_id);
      expect(command.key).not.toBe(sent[0].key);
    }
    expect(sent[6].note_id).not.toBe(sent[5].note_id);
    expect(sent[6].key).not.toBe(sent[5].key);
  });

  test('schedules another task GET only while the task is active', () => {
    expect(pollDecision({ ...task, status: 'pending' })).toEqual({
      poll: true,
      delayMs: 1000,
    });
    expect(pollDecision(task)).toEqual({ poll: true, delayMs: 1000 });
    expect(
      pollDecision({
        ...task,
        status: 'failed',
        verification_status: 'failed',
        failure: { code: 'PROVIDER_FAILED', message: 'provider failed' },
      }),
    ).toEqual({ poll: false, delayMs: 0 });
    expect(pollDecision({ ...task, status: 'succeeded' })).toEqual({
      poll: false,
      delayMs: 0,
    });
    expect(pollDecision({ ...task, status: 'cancelled' })).toEqual({
      poll: false,
      delayMs: 0,
    });
  });

  test('does not schedule another task GET after content is returned', () => {
    expect(
      pollDecision({
        ...task,
        status: 'succeeded',
        content_draft: {
          facts: [],
          inferences: [],
          missing_evidence: [],
          citations: [],
        },
        revision: 2,
        content_draft_source: 'provider',
        content_draft_created_at: '2026-09-10T08:01:02Z',
        ai_generated: true,
      }),
    ).toEqual({ poll: false, delayMs: 0 });
  });

  test('distinguishes overdue processing, deterministic content and three statuses', () => {
    expect(taskPresentation({ ...task, processing_overdue: true })).toMatchObject({
      phase: 'processing_overdue',
      label: '仍在处理',
    });
    expect(
      taskPresentation({
        ...task,
        status: 'failed',
        verification_status: 'failed',
        human_review_status: 'edited',
        content_draft: {
          facts: [],
          inferences: [],
          missing_evidence: [{ text: '缺少证据' }],
          citations: [],
        },
        revision: 3,
        content_draft_source: 'deterministic_template',
        content_draft_created_at: '2026-09-10T08:03:00Z',
        ai_generated: false,
      }),
    ).toMatchObject({
      phase: 'deterministic',
      taskStatus: 'failed',
      verificationStatus: 'failed',
      reviewStatus: 'edited',
    });
  });

  test('uploads only explicit note fields and previews exact scope and count', () => {
    const selection = buildNotesSelection(
      {
        title: '标题',
        content: '正文',
        judgment: '判断',
        evidence: '依据',
        invalid_condition: '失效条件',
        verify_date: '2026-09-20',
      },
      ['judgment', 'evidence'],
      'snapshot-1',
      'TEST001',
    );
    expect(selection.text).toBe('判断：判断\n\n依据：依据');
    expect(selection.characterCount).toBe([...selection.text].length);
    expect(selection.snapshotId).toBe('snapshot-1');
    expect(selection.securityId).toBe('TEST001');
    expect(selection.fields).toEqual(['judgment', 'evidence']);
  });

  test('detects editor changes after submit before adoption', () => {
    expect(adoptionConflict('submitted-signature', 'submitted-signature')).toBe(
      false,
    );
    expect(adoptionConflict('submitted-signature', 'changed-signature')).toBe(true);
  });
});

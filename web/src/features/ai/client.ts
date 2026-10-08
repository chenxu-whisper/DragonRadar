export type AITaskType = 'summarize' | 'topic_suggest' | 'explain_stock' | 'daily_review';
export type AITaskStatus = 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled';
export type VerificationStatus = 'pending' | 'passed' | 'failed' | 'needs_human';
export type HumanReviewStatus = 'unreviewed' | 'accepted' | 'edited' | 'rejected';
export type DraftSource = 'deterministic_template' | 'provider' | 'human_edit';

export interface AIFact {
  text: string;
  evidence_id: string;
  evidence_position: string;
  value: unknown;
  recomputation?: {
    function: string;
    evidence_ids: string[];
    result: string;
  };
}

export interface AIInference {
  text: string;
  confidence: string;
  evidence_ids: string[];
}

export interface AIMissingEvidence {
  text: string;
  reason?: string;
}

export interface AICitation {
  evidence_id: string;
  evidence_position: string;
  source_identity: string;
  available_at: string;
  value: unknown;
}

export interface AIDraftContent {
  facts: AIFact[];
  inferences: AIInference[];
  missing_evidence: AIMissingEvidence[];
  citations: AICitation[];
}

export interface AITask {
  task_id: string;
  task_type: AITaskType;
  snapshot_id: string;
  as_of: string;
  version: string;
  status: AITaskStatus;
  verification_status: VerificationStatus;
  human_review_status: HumanReviewStatus;
  processing_overdue: boolean;
  content_draft: AIDraftContent | null;
  revision: number | null;
  content_draft_source: DraftSource | null;
  content_draft_created_at: string | null;
  ai_generated: boolean | null;
  model_version: string | null;
  prompt_version: string | null;
  retrieval_version: string;
  provider: string | null;
  token_usage: number | null;
  cost: number | string | null;
  latency_ms: number | null;
  failure: Record<string, unknown> | null;
}

export interface AICommand {
  path: string;
  body: Record<string, unknown> & { idempotency_key: string };
}

export interface AICancellationReceipt {
  task_id: string;
  status: 'cancelled';
  idempotency_key: string;
  accepted_at: string;
}

export interface AIReviewReceipt {
  task_id: string;
  human_review_status: Exclude<HumanReviewStatus, 'unreviewed'>;
  revision: number;
  idempotency_key: string;
  verification_status: VerificationStatus;
  note: string;
}

const UUID = /^[\da-f]{8}-[\da-f]{4}-[1-5][\da-f]{3}-[89ab][\da-f]{3}-[\da-f]{12}$/i;
const AI_PATH = /^\/api\/ai\/(?:summarize|topic-suggest|explain-stock|daily-review|task\/[\da-f-]{36}(?:\/(?:cancel|review))?)$/i;
const taskTypes = new Set<AITaskType>(['summarize', 'topic_suggest', 'explain_stock', 'daily_review']);
const taskStatuses = new Set<AITaskStatus>(['pending', 'running', 'succeeded', 'failed', 'cancelled']);
const verificationStatuses = new Set<VerificationStatus>(['pending', 'passed', 'failed', 'needs_human']);
const reviewStatuses = new Set<HumanReviewStatus>(['unreviewed', 'accepted', 'edited', 'rejected']);
const draftSources = new Set<DraftSource>(['deterministic_template', 'provider', 'human_edit']);

export class AIClientError extends Error {
  constructor(
    readonly status: number,
    readonly code = '',
    message = '',
  ) {
    super(message || (status === 409
      ? '草稿修订已变化，请重新读取后再操作。'
      : status === 408 || status === 0 || status >= 500
        ? 'AI 请求结果未知，请使用原幂等键重试。'
        : 'AI 请求未完成，请检查当前会话与输入。'));
    this.name = 'AIClientError';
  }

  get uncertain() {
    return this.status === 0 || this.status === 408 || this.status >= 500;
  }

  get replayableConflict() {
    return this.status === 409;
  }
}

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function nullableString(value: unknown): value is string | null {
  return value === null || typeof value === 'string';
}

function validDateTime(value: unknown): value is string {
  return typeof value === 'string' && Number.isFinite(Date.parse(value));
}

function exactKeys(value: Record<string, unknown>, required: string[]): boolean {
  return Object.keys(value).length === required.length
    && required.every(key => Object.hasOwn(value, key));
}

function validDraft(value: unknown): value is AIDraftContent {
  if (!record(value) || !exactKeys(value, ['facts', 'inferences', 'missing_evidence', 'citations'])) return false;
  if (!Array.isArray(value.facts) || !value.facts.every(item => {
    if (!record(item)
      || !(exactKeys(item, ['text', 'evidence_id', 'evidence_position', 'value'])
        || exactKeys(item, ['text', 'evidence_id', 'evidence_position', 'value', 'recomputation']))
      || typeof item.text !== 'string'
      || typeof item.evidence_id !== 'string'
      || typeof item.evidence_position !== 'string') return false;
    if (item.recomputation === undefined) return true;
    return record(item.recomputation)
      && exactKeys(item.recomputation, ['function', 'evidence_ids', 'result'])
      && typeof item.recomputation.function === 'string'
      && Array.isArray(item.recomputation.evidence_ids)
      && item.recomputation.evidence_ids.every(id => typeof id === 'string')
      && typeof item.recomputation.result === 'string';
  })) return false;
  if (!Array.isArray(value.inferences) || !value.inferences.every(item =>
    record(item)
    && exactKeys(item, ['text', 'confidence', 'evidence_ids'])
    && typeof item.text === 'string'
    && typeof item.confidence === 'string'
    && Array.isArray(item.evidence_ids)
    && item.evidence_ids.every(id => typeof id === 'string'))) return false;
  if (!Array.isArray(value.missing_evidence) || !value.missing_evidence.every(item =>
    record(item)
    && (exactKeys(item, ['text']) || exactKeys(item, ['text', 'reason']))
    && typeof item.text === 'string'
    && (item.reason === undefined || typeof item.reason === 'string'))) return false;
  return Array.isArray(value.citations) && value.citations.every(item =>
    record(item)
    && exactKeys(item, ['evidence_id', 'evidence_position', 'source_identity', 'available_at', 'value'])
    && typeof item.evidence_id === 'string'
    && typeof item.evidence_position === 'string'
    && typeof item.source_identity === 'string'
    && validDateTime(item.available_at)
    && Object.hasOwn(item, 'value'));
}

const taskFields = [
  'task_id', 'task_type', 'snapshot_id', 'as_of', 'version', 'status',
  'verification_status', 'human_review_status', 'processing_overdue',
  'content_draft', 'revision', 'content_draft_source',
  'content_draft_created_at', 'ai_generated', 'model_version', 'prompt_version',
  'retrieval_version', 'provider', 'token_usage', 'cost', 'latency_ms', 'failure',
];

export function validateTask(value: unknown): AITask {
  const invalid = () => { throw new AIClientError(502, 'malformed_response', 'AI task response is invalid.'); };
  if (!record(value) || !exactKeys(value, taskFields)) invalid();
  const task = value as Record<string, unknown>;
  if (!UUID.test(String(task.task_id)) || !UUID.test(String(task.snapshot_id))
    || !taskTypes.has(task.task_type as AITaskType)
    || !taskStatuses.has(task.status as AITaskStatus)
    || !verificationStatuses.has(task.verification_status as VerificationStatus)
    || !reviewStatuses.has(task.human_review_status as HumanReviewStatus)
    || !validDateTime(task.as_of)
    || typeof task.version !== 'string'
    || typeof task.processing_overdue !== 'boolean'
    || !nullableString(task.model_version)
    || !nullableString(task.prompt_version)
    || typeof task.retrieval_version !== 'string'
    || !nullableString(task.provider)
    || !(task.token_usage === null || Number.isSafeInteger(task.token_usage))
    || !(task.latency_ms === null || Number.isSafeInteger(task.latency_ms))
    || !(task.cost === null || typeof task.cost === 'number' || typeof task.cost === 'string')
    || !(task.failure === null || record(task.failure))) invalid();
  const hasDraft = task.content_draft !== null;
  if ((hasDraft && !validDraft(task.content_draft))
    || (hasDraft !== (Number.isSafeInteger(task.revision) && Number(task.revision) > 0))
    || (hasDraft !== draftSources.has(task.content_draft_source as DraftSource))
    || (hasDraft !== validDateTime(task.content_draft_created_at))
    || (hasDraft !== (typeof task.ai_generated === 'boolean'))) invalid();
  return task as unknown as AITask;
}

export function validateCancellationReceipt(value: unknown): AICancellationReceipt {
  if (!record(value)
    || !exactKeys(value, ['task_id', 'status', 'idempotency_key', 'accepted_at'])
    || !UUID.test(String(value.task_id))
    || value.status !== 'cancelled'
    || typeof value.idempotency_key !== 'string'
    || !validDateTime(value.accepted_at)) {
    throw new AIClientError(502, 'malformed_response', 'AI cancellation receipt is invalid.');
  }
  return value as unknown as AICancellationReceipt;
}

export function validateReviewReceipt(value: unknown): AIReviewReceipt {
  if (!record(value)
    || !exactKeys(value, [
      'task_id',
      'human_review_status',
      'revision',
      'idempotency_key',
      'verification_status',
      'note',
    ])
    || !UUID.test(String(value.task_id))
    || !reviewStatuses.has(value.human_review_status as HumanReviewStatus)
    || value.human_review_status === 'unreviewed'
    || !Number.isSafeInteger(value.revision)
    || Number(value.revision) < 1
    || typeof value.idempotency_key !== 'string'
    || !verificationStatuses.has(value.verification_status as VerificationStatus)
    || typeof value.note !== 'string') {
    throw new AIClientError(502, 'malformed_response', 'AI review receipt is invalid.');
  }
  return value as unknown as AIReviewReceipt;
}

function csrfToken(): string {
  if (typeof document === 'undefined') return '';
  return document.cookie.split(';')
    .map(value => value.trim())
    .find(value => value.startsWith('__Host-dragon-private-csrf='))
    ?.slice('__Host-dragon-private-csrf='.length) || '';
}

export async function aiRequest(
  path: string,
  options: {
    method?: 'GET' | 'POST';
    body?: Record<string, unknown>;
    signal?: AbortSignal;
    timeoutMs?: number;
  } = {},
): Promise<unknown> {
  const method = options.method || 'GET';
  if (!AI_PATH.test(path) || path.includes('\\') || path.includes('?')
    || (method === 'GET' && options.body !== undefined)) {
    throw new AIClientError(400, 'invalid_path');
  }
  const controller = new AbortController();
  let rejectInterrupted: (reason: unknown) => void = () => {};
  const interrupted = new Promise<never>((_, reject) => { rejectInterrupted = reject; });
  const abort = () => {
    controller.abort();
    rejectInterrupted(new DOMException('请求已取消', 'AbortError'));
  };
  options.signal?.addEventListener('abort', abort, { once: true });
  if (options.signal?.aborted) abort();
  const timer = setTimeout(() => {
    controller.abort();
    rejectInterrupted(new AIClientError(408, 'timeout'));
  }, options.timeoutMs ?? 15_000);
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (method === 'POST') {
    headers['Content-Type'] = 'application/json';
    headers['X-CSRF-Token'] = csrfToken();
  }
  try {
    const response = await Promise.race([
      fetch(path, {
        method,
        body: method === 'POST' ? JSON.stringify(options.body || {}) : undefined,
        credentials: 'same-origin',
        cache: 'no-store',
        signal: controller.signal,
        headers,
      }),
      interrupted,
    ]);
    let data: unknown;
    try {
      data = await Promise.race([response.json(), interrupted]);
    } catch {
      throw new AIClientError(502, 'malformed_response');
    }
    if (!response.ok) {
      const code = record(data) && typeof data.code === 'string' ? data.code : '';
      throw new AIClientError(response.status, code);
    }
    if (!record(data)) throw new AIClientError(502, 'malformed_response');
    return data;
  } catch (error) {
    if (error instanceof AIClientError
      || (error instanceof DOMException && error.name === 'AbortError')) throw error;
    throw new AIClientError(0, 'network_error');
  } finally {
    clearTimeout(timer);
    options.signal?.removeEventListener('abort', abort);
  }
}

export function createAICommand(
  path: string,
  body: Record<string, unknown>,
  key: string = crypto.randomUUID(),
): AICommand {
  if (!key || key.length > 100) throw new AIClientError(400, 'invalid_key');
  return { path, body: { ...body, idempotency_key: key } };
}

export async function submitAITask(command: AICommand, signal?: AbortSignal): Promise<AITask> {
  return validateTask(await aiRequest(command.path, {
    method: 'POST',
    body: command.body,
    signal,
  }));
}

export async function getAITask(taskId: string, signal?: AbortSignal): Promise<AITask> {
  return validateTask(await aiRequest(`/api/ai/task/${taskId}`, { signal }));
}

export async function cancelAITask(
  command: AICommand,
  signal?: AbortSignal,
): Promise<AICancellationReceipt> {
  return validateCancellationReceipt(await aiRequest(command.path, {
    method: 'POST',
    body: command.body,
    signal,
  }));
}

export async function reviewAITask(
  command: AICommand,
  signal?: AbortSignal,
): Promise<AIReviewReceipt> {
  return validateReviewReceipt(await aiRequest(command.path, {
    method: 'POST',
    body: command.body,
    signal,
  }));
}

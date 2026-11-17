import { ApiError } from '../../api';
import type { WorkbenchCoverage } from '../../types';
import {
  ROLE_NAMES,
  type CandidatesData,
  type ContinuityData,
  type EvidenceData,
  type LadderData,
  type RoleEnvelope,
  type RoleName,
  type RoleSelection,
  type RotationData,
} from './types';

type ObjectValue = Record<string, unknown>;

function object(value: unknown): ObjectValue {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new ApiError(502, '角色接口响应格式无效，已丢弃该响应。');
  }
  return value as ObjectValue;
}

function text(value: unknown): string {
  if (typeof value !== 'string') throw new ApiError(502, '角色接口文本字段无效，已丢弃该响应。');
  return value;
}

function nullableText(value: unknown): string | null {
  if (value !== null && typeof value !== 'string') throw new ApiError(502, '角色接口文本字段无效，已丢弃该响应。');
  return value;
}

function integer(value: unknown): number {
  if (!Number.isSafeInteger(value) || (value as number) < 0) throw new ApiError(502, '角色接口计数字段无效，已丢弃该响应。');
  return value as number;
}

function numberOrNull(value: unknown): number | null {
  if (value !== null && (typeof value !== 'number' || !Number.isFinite(value))) {
    throw new ApiError(502, '角色接口数值字段无效，已丢弃该响应。');
  }
  return value;
}

function texts(value: unknown): string[] {
  if (!Array.isArray(value) || value.some(item => typeof item !== 'string')) {
    throw new ApiError(502, '角色接口原因字段无效，已丢弃该响应。');
  }
  return value;
}

function role(value: unknown): RoleName {
  if (!ROLE_NAMES.includes(value as RoleName)) throw new ApiError(502, '角色接口包含未知角色，已丢弃该响应。');
  return value as RoleName;
}

function coverage(value: unknown): WorkbenchCoverage {
  const row = object(value);
  const result = {
    expected: integer(row.expected),
    valid: integer(row.valid),
    excluded: integer(row.excluded),
    missing: integer(row.missing),
  };
  if (result.expected !== result.valid + result.excluded + result.missing) {
    throw new ApiError(502, '角色接口覆盖率不守恒，已丢弃该响应。');
  }
  return result;
}

function envelope(value: unknown, selection: Pick<RoleSelection, 'snapshot' | 'date'>): ObjectValue {
  const row = object(value);
  if (row.snapshot_id !== selection.snapshot) throw new ApiError(409, '角色响应快照与当前请求不匹配，已丢弃该响应。');
  if (row.trade_date !== selection.date) throw new ApiError(409, '角色响应日期与当前请求不匹配，已丢弃该响应。');
  text(row.as_of);
  text(row.source);
  text(row.source_version);
  texts(row.quality_flags);
  if (row.rule_version !== null) text(row.rule_version);
  object(row.basis);
  if (row.coverage !== null) coverage(row.coverage);
  return row;
}

function query(selection: Pick<RoleSelection, 'date' | 'snapshot'>): URLSearchParams {
  return new URLSearchParams({ date: selection.date, snapshot_id: selection.snapshot });
}

export function ladderPath(selection: RoleSelection): string {
  const params = query(selection);
  if (selection.sector) params.set('sector', selection.sector);
  return `/api/limit-up/ladder?${params}`;
}

export function continuityPath(selection: Pick<RoleSelection, 'date' | 'snapshot'>): string {
  return `/api/limit-up/continuity?${query(selection)}`;
}

export function candidatePath(selection: Required<Pick<RoleSelection, 'date' | 'snapshot' | 'role'>> & Pick<RoleSelection, 'sector'>): string {
  const params = query(selection);
  params.set('role', selection.role);
  if (selection.sector) params.set('sector', selection.sector);
  return `/api/role/candidates?${params}`;
}

export function evidencePath(
  selection: Required<Pick<RoleSelection, 'date' | 'snapshot' | 'sector'>>,
  security: string,
): string {
  const params = query(selection);
  params.set('security_id', security);
  params.set('sector', selection.sector);
  return `/api/role/evidence?${params}`;
}

function promotion(value: unknown) {
  const row = object(value);
  return {
    yesterday_ge2: integer(row.yesterday_ge2),
    today_sealed: integer(row.today_sealed),
    tradable: integer(row.tradable),
    rate: numberOrNull(row.rate),
  };
}

export function validateLadder(value: unknown, selection: Pick<RoleSelection, 'snapshot' | 'date' | 'sector'>): RoleEnvelope<LadderData> {
  const root = envelope(value, selection);
  if (root.data === null) return root as unknown as RoleEnvelope<LadderData>;
  const data = object(root.data);
  if (!Array.isArray(data.columns) || !Array.isArray(data.cells)) throw new ApiError(502, '梯队矩阵响应格式无效，已丢弃该响应。');
  const columns = data.columns.map(integer);
  const seen = new Set<string>();
  let sum = 0;
  for (const rawCell of data.cells) {
    const cell = object(rawCell);
    const boards = integer(cell.boards);
    if (!columns.includes(boards) || !Array.isArray(cell.stocks)) throw new ApiError(502, '梯队矩阵板数无效，已丢弃该响应。');
    const count = integer(cell.count);
    if (count !== cell.stocks.length) throw new ApiError(502, '梯队矩阵计数不一致，已丢弃该响应。');
    sum += count;
    for (const rawStock of cell.stocks) {
      const stock = object(rawStock);
      const id = text(stock.security_id);
      text(stock.name);
      const sector = text(stock.sector_id);
      if (selection.sector && sector !== selection.sector) throw new ApiError(409, '梯队响应题材与当前请求不匹配，已丢弃该响应。');
      if (seen.has(id)) throw new ApiError(502, '梯队响应包含重复证券，已丢弃该响应。');
      seen.add(id);
      if (!Array.isArray(stock.roles)) throw new ApiError(502, '梯队响应角色字段无效，已丢弃该响应。');
      stock.roles.forEach(role);
    }
  }
  if (integer(data.total) !== sum) throw new ApiError(502, '梯队矩阵总数不一致，已丢弃该响应。');
  promotion(data.promote_rate);
  return root as unknown as RoleEnvelope<LadderData>;
}

export function validateContinuity(value: unknown, selection: Pick<RoleSelection, 'snapshot' | 'date'>): RoleEnvelope<ContinuityData> {
  const root = envelope(value, selection);
  if (root.data === null) return root as unknown as RoleEnvelope<ContinuityData>;
  const data = object(root.data);
  promotion(data.promote_rate);
  const promoteQuality = object(data.promote_rate_quality);
  coverage(promoteQuality.coverage);
  texts(promoteQuality.reasons);
  numberOrNull(data.yesterday_limit_up_median_return);
  const feedback = object(data.yesterday_limit_up_feedback);
  if (!['available', 'degraded', 'missing'].includes(text(feedback.status))) throw new ApiError(502, '昨日反馈状态无效，已丢弃该响应。');
  coverage(feedback.coverage);
  texts(feedback.reasons);
  integer(data.tradable_count);
  return root as unknown as RoleEnvelope<ContinuityData>;
}

export function validateCandidates(
  value: unknown,
  selection: Required<Pick<RoleSelection, 'snapshot' | 'date' | 'role'>> & Pick<RoleSelection, 'sector'>,
): RoleEnvelope<CandidatesData> {
  const root = envelope(value, selection);
  if (root.data === null) return root as unknown as RoleEnvelope<CandidatesData>;
  const data = object(root.data);
  if (role(data.role) !== selection.role) throw new ApiError(409, '候选响应角色与当前请求不匹配，已丢弃该响应。');
  if (!Array.isArray(data.statuses) || !Array.isArray(data.candidates)
    || !Array.isArray(data.change_summaries) || !Array.isArray(data.role_changes)) {
    throw new ApiError(502, '候选响应格式无效，已丢弃该响应。');
  }
  for (const raw of data.statuses) {
    const status = object(raw);
    const sector = text(status.sector_id);
    if (selection.sector && sector !== selection.sector) throw new ApiError(409, '候选响应题材与当前请求不匹配，已丢弃该响应。');
    if (!['ready', 'simplified', 'watch_only', 'pending_validation', 'insufficient', 'no_candidate'].includes(text(status.status))) {
      throw new ApiError(502, '候选状态无效，已丢弃该响应。');
    }
    text(status.capability);
    texts(status.reasons);
  }
  for (const raw of data.candidates) {
    const candidate = object(raw);
    text(candidate.security_id);
    text(candidate.name);
    const sector = text(candidate.sector_id);
    if (selection.sector && sector !== selection.sector) throw new ApiError(409, '候选响应题材与当前请求不匹配，已丢弃该响应。');
    if (integer(candidate.rank) < 1 || candidate.evidence_status !== 'candidate') throw new ApiError(502, '候选排名无效，已丢弃该响应。');
    if (candidate.boards !== null) integer(candidate.boards);
  }
  for (const raw of data.change_summaries) {
    const change = object(raw);
    const sector = text(change.sector_id);
    if (selection.sector && sector !== selection.sector) throw new ApiError(409, '变化响应题材与当前请求不匹配，已丢弃该响应。');
    if (!['comparable', 'not_comparable'].includes(text(change.comparison_status))
      || typeof change.membership_changed !== 'boolean') throw new ApiError(502, '角色变化状态无效，已丢弃该响应。');
    texts(change.reasons);
  }
  for (const raw of data.role_changes) {
    const change = object(raw);
    text(change.id);
    const sector = text(change.sector_id);
    if (selection.sector && sector !== selection.sector) throw new ApiError(409, '变化响应题材与当前请求不匹配，已丢弃该响应。');
    if (typeof change.security_id !== 'string' || typeof change.role !== 'string') {
      throw new ApiError(502, '角色变化明细无效，已丢弃该响应。');
    }
    if (!['comparable', 'not_comparable'].includes(text(change.comparison_status))
      || (change.change_type !== null && !['added', 'exited', 'tie_changed', 'unchanged'].includes(text(change.change_type)))
      || typeof change.membership_changed !== 'boolean') {
      throw new ApiError(502, '角色变化明细无效，已丢弃该响应。');
    }
    texts(change.reasons);
  }
  if (integer(data.total) !== data.candidates.length) throw new ApiError(502, '候选总数不一致，已丢弃该响应。');
  return root as unknown as RoleEnvelope<CandidatesData>;
}

export function validateEvidence(
  value: unknown,
  selection: Required<Pick<RoleSelection, 'snapshot' | 'date' | 'security' | 'sector'>>,
): RoleEnvelope<EvidenceData> {
  const root = envelope(value, selection);
  if (root.data === null) return root as unknown as RoleEnvelope<EvidenceData>;
  const data = object(root.data);
  if (data.security_id !== selection.security) throw new ApiError(409, '证据响应证券与当前请求不匹配，已丢弃该响应。');
  if (data.sector_id !== selection.sector) throw new ApiError(409, '证据响应题材与当前请求不匹配，已丢弃该响应。');
  if (!Array.isArray(data.roles)) throw new ApiError(502, '角色证据响应格式无效，已丢弃该响应。');
  const seen = new Set<RoleName>();
  for (const raw of data.roles) {
    const evidence = object(raw);
    const name = role(evidence.role_type);
    if (seen.has(name)) throw new ApiError(502, '角色证据响应包含重复角色，已丢弃该响应。');
    seen.add(name);
    text(evidence.evidence_status);
    for (const key of ['supporting_evidence', 'opposing_evidence', 'missing_evidence']) texts(evidence[key]);
    if (!Array.isArray(evidence.factors)) throw new ApiError(502, '角色证据因子无效，已丢弃该响应。');
    for (const rawFactor of evidence.factors) {
      const factor = object(rawFactor);
      text(factor.factor_id);
      nullableText(factor.metric);
      if (factor.value !== null && typeof factor.value !== 'number' && typeof factor.value !== 'string') {
        throw new ApiError(502, '角色证据因子值无效，已丢弃该响应。');
      }
      if (!['computed', 'unavailable'].includes(text(factor.status))) throw new ApiError(502, '角色证据因子状态无效，已丢弃该响应。');
      nullableText(factor.unit);
      nullableText(factor.window);
      coverage(factor.coverage);
      texts(factor.reasons);
      nullableText(factor.source_time);
      nullableText(factor.knowledge_cutoff);
    }
  }
  if (seen.size !== ROLE_NAMES.length) throw new ApiError(502, '角色证据未覆盖六类角色，已丢弃该响应。');
  return root as unknown as RoleEnvelope<EvidenceData>;
}

// Frozen EX11 reason vocabulary, identical to the backend ROTATION_REASONS.
export const ROTATION_FIRST_OBSERVATION = 'first_observation';
export const ROTATION_RULE_VERSION_CHANGED = 'rule_version_changed';
export const ROTATION_TOPIC_REVISION_CHANGED = 'topic_revision_changed';
export const ROTATION_TAXONOMY_VERSION_CHANGED = 'taxonomy_version_changed';
export const ROTATION_MEMBERSHIP_VERSION_CHANGED = 'membership_version_changed';
export const ROTATION_MEMBER_POOL_CHANGED = 'member_pool_changed';
export const ROTATION_PREVIOUS_COMPARABLE_RUN_MISMATCH = 'previous_comparable_run_mismatch';
export const ROTATION_NOT_PREVIOUS_COMPARABLE_SESSION = 'not_previous_comparable_session';
export const ROTATION_NO_CORE_CANDIDATE = 'no_core_candidate';

const ROTATION_REASONS = new Set<string>([
  ROTATION_FIRST_OBSERVATION,
  ROTATION_RULE_VERSION_CHANGED,
  ROTATION_TOPIC_REVISION_CHANGED,
  ROTATION_TAXONOMY_VERSION_CHANGED,
  ROTATION_MEMBERSHIP_VERSION_CHANGED,
  ROTATION_MEMBER_POOL_CHANGED,
  ROTATION_PREVIOUS_COMPARABLE_RUN_MISMATCH,
  ROTATION_NOT_PREVIOUS_COMPARABLE_SESSION,
  ROTATION_NO_CORE_CANDIDATE,
]);
const ROTATION_CHANGES = new Set(['segment_start', 'unchanged', 'expanded', 'contracted', 'reorganized']);
const ROTATION_DATA_KEYS = ['events', 'days', 'sector', 'window'];
const ROTATION_WINDOW_KEYS = ['start', 'end'];
const ROTATION_EVENT_KEYS = [
  'at', 'sector_id', 'core_ids', 'change', 'comparable', 'reason', 'snapshot_id', 'input_manifest',
];
const ROTATION_MANIFEST_KEYS = [
  'run_id', 'market_snapshot_id', 'topic_snapshot_id', 'rule_version', 'membership_version',
];
const CANONICAL_UUID = /^[\da-f]{8}-[\da-f]{4}-[1-5][\da-f]{3}-[89ab][\da-f]{3}-[\da-f]{12}$/;

export function rotationPath(selection: { date: string; snapshot: string; sector?: string; days: number }): string {
  const params = new URLSearchParams({
    date: selection.date,
    snapshot_id: selection.snapshot,
    days: String(selection.days),
  });
  if (selection.sector) params.set('sector', selection.sector);
  return `/api/topics/rotation?${params.toString()}`;
}

function rotationMalformed(): ApiError {
  return new ApiError(502, '题材轮动响应格式无效，已丢弃该响应。');
}

function exactKeys(row: ObjectValue, keys: readonly string[]): void {
  const present = Object.keys(row);
  if (present.length !== keys.length || keys.some(key => !(key in row))) throw rotationMalformed();
}

function canonicalUuid(value: unknown): string {
  if (typeof value !== 'string' || !CANONICAL_UUID.test(value)) {
    throw new ApiError(502, '题材轮动快照标识无效，已丢弃该响应。');
  }
  return value;
}

function rotationDay(value: unknown): string {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    throw new ApiError(502, '题材轮动日期无效，已丢弃该响应。');
  }
  const parsed = new Date(`${value}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== value) {
    throw new ApiError(502, '题材轮动日期无效，已丢弃该响应。');
  }
  return value;
}

export function validateRotation(
  value: unknown,
  selection: { snapshot: string; date: string; sector?: string },
  days: number,
): RoleEnvelope<RotationData> {
  const root = envelope(value, selection);
  if (root.data === null) return root as unknown as RoleEnvelope<RotationData>;
  const data = object(root.data);
  exactKeys(data, ROTATION_DATA_KEYS);
  if (!Number.isSafeInteger(data.days) || (data.days as number) < 1 || (data.days as number) > 250) {
    throw new ApiError(502, '题材轮动窗口天数无效，已丢弃该响应。');
  }
  if (data.days !== days) throw new ApiError(502, '题材轮动窗口天数与请求不一致，已丢弃该响应。');
  if (data.sector !== (selection.sector ?? null)) {
    throw new ApiError(409, '题材轮动响应题材与当前请求不匹配，已丢弃该响应。');
  }
  const windowRow = object(data.window);
  exactKeys(windowRow, ROTATION_WINDOW_KEYS);
  const start = rotationDay(windowRow.start);
  const end = rotationDay(windowRow.end);
  if (end !== selection.date) throw rotationMalformed();
  if (start > end) throw rotationMalformed();
  if (!Array.isArray(data.events)) throw rotationMalformed();

  let previousKey = '';
  for (const rawEvent of data.events) {
    const event = object(rawEvent);
    exactKeys(event, ROTATION_EVENT_KEYS);
    const at = rotationDay(event.at);
    if (at < start || at > end) throw new ApiError(502, '题材轮动事件日期越出窗口，已丢弃该响应。');
    const sectorId = event.sector_id;
    if (typeof sectorId !== 'string' || !sectorId) throw rotationMalformed();
    const orderKey = `${at}\u0000${sectorId}`;
    // Equal adjacent keys are duplicate (at, sector) pairs; smaller keys break
    // the global (at, sector) ascending order.
    if (orderKey <= previousKey) throw rotationMalformed();
    previousKey = orderKey;

    const coreIds = event.core_ids;
    if (
      !Array.isArray(coreIds)
      || coreIds.some(id => typeof id !== 'string' || !id)
      || new Set(coreIds).size !== coreIds.length
    ) {
      throw rotationMalformed();
    }
    const change = event.change;
    if (typeof change !== 'string' || !ROTATION_CHANGES.has(change)) throw rotationMalformed();
    if (typeof event.comparable !== 'boolean') throw rotationMalformed();
    const isSegmentStart = change === 'segment_start';
    if (isSegmentStart === event.comparable) throw rotationMalformed();

    const reason = event.reason;
    if (event.comparable) {
      if (reason !== null && reason !== ROTATION_NO_CORE_CANDIDATE) throw rotationMalformed();
      if ((coreIds.length === 0) !== (reason === ROTATION_NO_CORE_CANDIDATE)) throw rotationMalformed();
    } else if (typeof reason !== 'string' || !reason || !ROTATION_REASONS.has(reason)) {
      throw rotationMalformed();
    }

    // Anchor events must be served by the requested publication id so a
    // historical/anchor swap can never masquerade as the current version.
    const snapshotId = canonicalUuid(event.snapshot_id);
    if (at === selection.date && snapshotId !== selection.snapshot) {
      throw new ApiError(502, '题材轮动锚点快照与当前请求不匹配，已丢弃该响应。');
    }

    const manifest = object(event.input_manifest);
    exactKeys(manifest, ROTATION_MANIFEST_KEYS);
    for (const key of ['run_id', 'rule_version', 'membership_version'] as const) {
      if (typeof manifest[key] !== 'string' || !(manifest[key] as string)) throw rotationMalformed();
    }
    canonicalUuid(manifest.market_snapshot_id);
    canonicalUuid(manifest.topic_snapshot_id);
  }
  return root as unknown as RoleEnvelope<RotationData>;
}

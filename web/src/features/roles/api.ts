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

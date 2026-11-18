import { ApiError, request } from '../../api';
import type { LocalNote, VerificationContext } from '../../repositories/local/repository';
import { validLocalDate, validateVerificationFacts } from '../../repositories/local/verification-validation';
import type { ResetKlineData, ResetSnapshotData, WorkbenchEnvelope } from '../../types';

export type VerificationPublication = WorkbenchEnvelope<ResetSnapshotData>;
export function validatePublication(value: unknown, id?: string): VerificationPublication {
  const row = value as VerificationPublication;
  if (!row || typeof row !== 'object' || !row.snapshot_id || (id && row.snapshot_id !== id)
    || !validLocalDate(row.trade_date) || !Number.isFinite(Date.parse(row.as_of))
    || row.as_of.slice(0, 10) < row.trade_date
    || [row.source, row.source_version, row.rule_version].some(value => typeof value !== 'string' || !value.trim())
    || !row.data || !Number.isSafeInteger(row.data.revision) || row.data.revision < 1
    || !Number.isFinite(Date.parse(row.data.published_at))
    || !Array.isArray(row.data.calendar_sessions) || !row.data.calendar_sessions.every(validLocalDate)
    || row.data.calendar_sessions.some((date, index, dates) => index > 0 && date <= dates[index - 1])) {
    throw new Error('原始发布无法解析或发布身份不匹配');
  }
  return row;
}
export async function verificationPublications(note: LocalNote, signal: AbortSignal): Promise<VerificationPublication[]> {
  const end = note.content.verify_date;
  if (!validLocalDate(end)) throw new Error('原始修订验证日期不可用');
  const lists = await Promise.all([
    request<WorkbenchEnvelope<{ snapshots: unknown[] }>>(`/api/snapshots/list?date=${encodeURIComponent(end)}&limit=100`, { signal }),
    request<WorkbenchEnvelope<{ snapshots: unknown[] }>>('/api/snapshots/list?limit=100', { signal }),
  ]);
  const index = new Map<string, VerificationPublication>();
  for (const list of lists) {
    if (!Array.isArray(list?.data?.snapshots)) throw new Error('验证发布列表不可用');
    const rows = list.data.snapshots.map(value => validatePublication(value));
    if (new Set(rows.map(row => row.snapshot_id)).size !== rows.length) throw new Error('验证发布列表身份不匹配');
    for (const row of rows) {
      const current = index.get(row.snapshot_id);
      if (current && ['trade_date', 'as_of', 'source', 'source_version', 'rule_version'].some(key =>
        current[key as keyof VerificationPublication] !== row[key as keyof VerificationPublication])) throw new Error('验证发布列表身份不匹配');
      index.set(row.snapshot_id, row);
    }
  }
  const rows = [...index.values()];
  if (!rows.some(row => row.snapshot_id === note.snapshot_id)) {
    try {
      const original = validatePublication(await request(`/api/snapshots/${encodeURIComponent(note.snapshot_id)}`, {
        signal, snapshot: note.snapshot_id,
      }), note.snapshot_id);
      rows.push(original);
    } catch (error) { if (signal.aborted) throw error; }
  }
  return rows;
}
export async function loadVerificationFacts(
  note: LocalNote, selected: VerificationPublication, signal: AbortSignal,
): Promise<{ context: VerificationContext; fact_interval: ResetKlineData }> {
  const publication = validatePublication(await request(`/api/snapshots/${encodeURIComponent(selected.snapshot_id)}`, {
    signal, snapshot: selected.snapshot_id,
  }), selected.snapshot_id);
  if (['trade_date', 'as_of', 'source', 'source_version', 'rule_version'].some(key =>
    publication[key as keyof VerificationPublication] !== selected[key as keyof VerificationPublication])
    || publication.data.revision !== selected.data.revision
    || publication.data.published_at !== selected.data.published_at
    || publication.data.calendar_sessions.join(',') !== selected.data.calendar_sessions.join(',')) throw new Error('验证发布身份不匹配');
  const start = note.trade_date, end = note.content.verify_date;
  if (!validLocalDate(start) || !validLocalDate(end) || publication.trade_date < end || !note.security_id) {
    throw new Error('验证区间不可用：发布尚未覆盖原始验证日期');
  }
  const query = new URLSearchParams({ snapshot_id: publication.snapshot_id, date: publication.trade_date, start, end, adj: 'raw' });
  // Kline envelopes expose the actual end date, not necessarily the publication date.
  // Validate both identities here against the directly resolved immutable publication.
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal.addEventListener('abort', abort, { once: true });
  if (signal.aborted) abort();
  const timer = setTimeout(abort, 15000);
  try {
    const response = await fetch(`/api/stock/${encodeURIComponent(note.security_id)}/kline?${query}`, {
      method: 'GET', credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
      headers: { Accept: 'application/json' },
    });
    if (!response.ok) throw new ApiError(response.status);
    const envelope = await response.json() as WorkbenchEnvelope<ResetKlineData | null>;
    if (controller.signal.aborted) throw new DOMException('请求已取消', 'AbortError');
    if (envelope.snapshot_id !== publication.snapshot_id
      || ['as_of', 'source', 'source_version', 'rule_version'].some(key =>
        envelope[key as keyof typeof envelope] !== publication[key as keyof VerificationPublication])) {
      throw new Error('验证区间发布身份不匹配');
    }
    const facts = envelope.data;
    if (!facts || !envelope.basis || envelope.basis.price_type !== 'raw'
      || (envelope.trade_date !== publication.trade_date && envelope.trade_date !== facts.adjusted_end)) {
      throw new Error('验证区间事实缺失或不可用');
    }
    const context: VerificationContext = {
      requested_start: start, requested_end: end, adjusted_start: facts.adjusted_start, adjusted_end: facts.adjusted_end,
      trade_date: publication.trade_date, as_of: publication.as_of,
      source: publication.source, source_version: publication.source_version, rule_version: publication.rule_version,
      published_at: publication.data.published_at, publication_revision: publication.data.revision,
      calendar_sessions: publication.data.calendar_sessions.filter(day => day >= start && day <= end),
    };
    validateVerificationFacts({ outcome: 'supported', evidence: '事实读取校验', snapshot_id: publication.snapshot_id, context, fact_interval: facts }, note);
    const sessions = publication.data.calendar_sessions.filter(day => day >= start && day <= end);
    if (!sessions.length || sessions.join(',') !== facts.points.map(point => point.trade_date).join(',')) {
      throw new Error('验证区间事实缺失：实际交易日不完整');
    }
    return { context, fact_interval: facts };
  } finally {
    clearTimeout(timer); signal.removeEventListener('abort', abort);
  }
}

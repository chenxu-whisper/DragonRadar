import type { ResetKlineData } from '../../types';
import type { LocalNote, LocalVerification, VerificationContext, WatchContext } from './repository';

export function localValueSignature(value: unknown): string {
  return JSON.stringify(value, (_key, child) => child && typeof child === 'object' && !Array.isArray(child)
    ? Object.fromEntries(Object.entries(child).sort(([a], [b]) => a.localeCompare(b))) : child);
}
export function validateVerificationChain(rows: LocalVerification[]): void {
  const fail = (): never => { throw new Error('验证修正链冲突或循环'); };
  const index = new Map(rows.map(row => [`${row.note_id}:${row.verification_id}`, row]));
  if (index.size !== rows.length) fail();
  const roots = new Set<string>(), children = new Set<string>();
  for (const row of rows) {
    if (row.supersedes === null) {
      const key = `${row.note_id}:${row.revision}`;
      if (roots.has(key)) fail();
      roots.add(key); continue;
    }
    const key = `${row.note_id}:${row.supersedes}`;
    const prior = index.get(key);
    if (!prior || prior.revision !== row.revision || children.has(key)) fail();
    children.add(key);
    const seen = new Set<string>([row.verification_id]);
    let cursor = prior;
    while (cursor) {
      if (seen.has(cursor.verification_id)) fail();
      seen.add(cursor.verification_id);
      cursor = cursor.supersedes === null ? undefined : index.get(`${cursor.note_id}:${cursor.supersedes}`);
    }
  }
}

export function validLocalDate(value: unknown): value is string {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
}
export function validateWatchContext(value: unknown): WatchContext {
  const row = value as WatchContext;
  if (!row || typeof row !== 'object' || Array.isArray(row)
    || Object.keys(row).sort().join(',') !== 'sector_id,sector_name,security_name,trade_date'
    || (row.trade_date !== null && !validLocalDate(row.trade_date))
    || [row.security_name, row.sector_id, row.sector_name].some(text =>
      text !== null && (typeof text !== 'string' || !text.trim() || text.length > 200))) {
    throw new Error('自选上下文无效');
  }
  return { trade_date: row.trade_date, security_name: row.security_name, sector_id: row.sector_id, sector_name: row.sector_name };
}
export function validateVerificationFacts(
  input: { outcome: string; evidence: string; snapshot_id: string | null; context?: VerificationContext | null; fact_interval?: ResetKlineData | null },
  original: Pick<LocalNote, 'trade_date' | 'security_id' | 'content'>,
  checkNotDueTime = false,
  allowLegacyBackup = false,
): void {
  const fail = (): never => { throw new Error('验证区间事实缺失、身份不匹配或来源不可用'); };
  const start = original.trade_date, end = original.content.verify_date;
  if (!validLocalDate(start) || !validLocalDate(end) || end < start) fail();
  if (input.outcome === 'not_due') {
    if (input.snapshot_id !== null || input.context !== null || input.fact_interval !== null
      || (checkNotDueTime && end! <= new Date().toISOString().slice(0, 10))) fail();
    return;
  }
  const context = input.context, facts = input.fact_interval;
  if (!input.evidence.trim() || typeof input.snapshot_id !== 'string' || !input.snapshot_id.trim()
    || !context || !facts || !original.security_id) fail();
  if (!context || !facts) return fail();
  const optional = ['published_at', 'publication_revision', 'calendar_sessions'];
  if (Object.keys(context).filter(key => !optional.includes(key)).sort().join(',') !== 'adjusted_end,adjusted_start,as_of,requested_end,requested_start,rule_version,source,source_version,trade_date'
    || [context.source, context.source_version, context.rule_version].some(value =>
      typeof value !== 'string' || !value.trim() || value.length > 200)
    || typeof context.as_of !== 'string' || !Number.isFinite(Date.parse(context.as_of))
    || ![context.requested_start, context.requested_end, context.adjusted_start, context.adjusted_end, context.trade_date].every(validLocalDate)
    || context.requested_start !== start || context.requested_end !== end
    || context.trade_date < end! || context.as_of.slice(0, 10) < context.trade_date
    || context.adjusted_start !== start || context.adjusted_end > end!
    || context.adjusted_end < context.adjusted_start
    || facts.security_id !== original.security_id || facts.adjustment !== 'raw'
    || facts.adjusted_start !== context.adjusted_start || facts.adjusted_end !== context.adjusted_end
    || facts.chart_type !== 'candlestick' || !Array.isArray(facts.points) || !facts.points.length) fail();
  if (context.published_at !== undefined && (typeof context.published_at !== 'string' || !Number.isFinite(Date.parse(context.published_at)))) fail();
  if (context.publication_revision !== undefined && (!Number.isSafeInteger(context.publication_revision) || context.publication_revision < 1)) fail();
  let previous = '';
  for (const point of facts.points) {
    if (!validLocalDate(point.trade_date) || point.trade_date <= previous
      || point.trade_date < facts.adjusted_start || point.trade_date > facts.adjusted_end
      || [point.open, point.high, point.low, point.close, point.volume, point.amount].some(value =>
        typeof value !== 'number' || !Number.isFinite(value) || value < 0)
      || point.high! < Math.max(point.open!, point.close!, point.low!)
      || point.low! > Math.min(point.open!, point.close!, point.high!)) fail();
    previous = point.trade_date;
  }
  if (facts.points[0].trade_date !== facts.adjusted_start
    || facts.points.at(-1)?.trade_date !== facts.adjusted_end) fail();
  const sessions = context.calendar_sessions;
  if (sessions === undefined) {
    // Older extended v1 backups did not archive calendars. Never infer an adjusted interval.
    if (!allowLegacyBackup || context.adjusted_end !== end) fail();
  } else if (!Array.isArray(sessions) || !sessions.length
    || !sessions.every(validLocalDate)
    || sessions.some((day, index) => day < start! || day > end!
      || (index > 0 && day <= sessions[index - 1]))
    || sessions[0] !== start || sessions.at(-1) !== context.adjusted_end
    || sessions.join(',') !== facts.points.map(point => point.trade_date).join(',')) fail();
}

import Decimal from 'decimal.js';
import { ApiError } from '../../api';
import type { WorkbenchCoverage, WorkbenchEnvelope } from '../../types';

export type HistoryWindow = 20 | 60 | 120 | 250;
export interface IndexSelection { securityId: string; snapshot: string; date: string; start: string; end: string }
interface Bounds { start: string; end: string }
export interface IndexPoint {
  trade_date: string; open: string | null; high: string | null; low: string | null;
  close: string | null; volume: string | null; amount: string | null;
  available_at: string | null; source_id: string; source_version: string;
  reason: 'observation_missing' | null; chart_type: 'candle' | 'line' | 'gap';
  provenance: 'contemporaneous' | 'retrospective';
}
export interface IndexHistory {
  security_id: string;
  identity: { security_id: string; security_type: 'index'; name: string | null };
  index_basis: { series_type: 'price' | 'total_return'; series_reference: string; price_unit: 'point';
    amount_currency: string | null; volume_unit: 'share' | 'lot' | 'not_applicable' };
  price_type: 'raw'; adjustment_version: 'raw-index-v1';
  requested: Bounds; adjusted: Bounds; available: Bounds; archive: Bounds;
  points: IndexPoint[]; chart_type: 'candle' | 'line' | 'gap'; coverage: WorkbenchCoverage;
  source: { source_id: string; source_version: string; market_snapshot_id: string; source_archive_id: string;
    source_archive_digest: string; calendar_version: string; as_of: string;
    provenance: 'contemporaneous' | 'retrospective' };
}
export type IndexHistoryResponse = Omit<WorkbenchEnvelope<IndexHistory | null>, 'coverage'> & { coverage: WorkbenchCoverage | null };
const object = (v: unknown): v is Record<string, unknown> => Boolean(v) && typeof v === 'object' && !Array.isArray(v);
const text = (v: unknown): v is string => typeof v === 'string' && v.trim().length > 0;
const day = (v: unknown): v is string => typeof v === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(v) &&
  Number.isFinite(Date.parse(`${v}T00:00:00Z`)) && new Date(`${v}T00:00:00Z`).toISOString().slice(0, 10) === v;
const uuid = (v: unknown) => typeof v === 'string' && /^[\da-f]{8}-[\da-f]{4}-[1-5][\da-f]{3}-[89ab][\da-f]{3}-[\da-f]{12}$/i.test(v);
const instant = (v: unknown): v is string => typeof v === 'string' &&
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(v) &&
  day(v.slice(0, 10)) && Number.isFinite(Date.parse(v));
const decimal = (v: unknown, positive: boolean) => {
  if (typeof v !== 'string' || !/^-?\d+(?:\.\d+)?$/.test(v)) return false;
  const n = new Decimal(v);
  return n.isFinite() && (positive ? n.gt(0) : n.gte(0));
};
const partition = (v: unknown) => object(v) && ['expected', 'valid', 'excluded', 'missing'].every(k =>
  Number.isSafeInteger(v[k]) && Number(v[k]) >= 0) && v.expected === Number(v.valid) + Number(v.excluded) + Number(v.missing);
const calendar = (v: readonly string[]) => v.length > 0 && v.every((d, i) => day(d) && (i === 0 || v[i - 1] < d));
const bounds = (v: unknown): v is Bounds => object(v) && day(v.start) && day(v.end) && v.start <= v.end;

export function indexHistoryWindow(sessions: readonly string[], pubDate: string, size: HistoryWindow): Bounds | null {
  if (!calendar(sessions) || !day(pubDate) || ![20, 60, 120, 250].includes(size)) return null;
  const cutoff = sessions.indexOf(pubDate);
  if (cutoff < 0) return null;
  return { start: sessions[Math.max(0, cutoff - size + 1)], end: pubDate };
}

export function validateIndexHistory(value: unknown, selection: IndexSelection, sessions: readonly string[]): string | undefined {
  if (!object(value)) return 'malformed_response';
  if (value.snapshot_id !== selection.snapshot || value.trade_date !== selection.date) return 'selection_mismatch';
  if (!instant(value.as_of) || !text(value.source) || !text(value.source_version) || !text(value.rule_version) ||
    !Array.isArray(value.quality_flags) || !value.quality_flags.every(text)) return 'malformed_response';
  if (value.data === null) return value.coverage === null && text(value.reason) && ['materialization_unavailable', 'index_history_unavailable', 'no_sessions'].includes(value.reason)
    && value.quality_flags.includes(value.reason) ? undefined : 'malformed_response';
  const d = value.data;
  if (!object(d) || !partition(value.coverage) || !calendar(sessions) || !sessions.includes(selection.date)) return 'malformed_response';
  if (d.security_id !== selection.securityId) return 'selection_mismatch';
  const basis = d.index_basis, identity = d.identity, source = d.source;
  if (!object(basis) || !['price', 'total_return'].includes(String(basis.series_type)) || !text(basis.series_reference) ||
    basis.price_unit !== 'point' || !(basis.amount_currency === null || text(basis.amount_currency)) ||
    !['share', 'lot', 'not_applicable'].includes(String(basis.volume_unit)) ||
    d.price_type !== 'raw' || d.adjustment_version !== 'raw-index-v1' || !object(identity) ||
    identity.security_id !== selection.securityId || identity.security_type !== 'index' ||
    !(identity.name === null || text(identity.name)) || !object(source) ||
    source.source_id !== value.source || source.source_version !== value.source_version ||
    identity.source_id !== source.source_id || identity.source_version !== source.source_version ||
    !text(identity.pool_version) || !text(identity.exchange) || !day(identity.listing_date) ||
    identity.listing_date > selection.date || !day(identity.effective_from) || identity.effective_from > selection.date ||
    !(identity.effective_to === null || (day(identity.effective_to) && identity.effective_to > selection.date)) ||
    typeof identity.suspended !== 'boolean' || !instant(identity.available_at) || Date.parse(identity.available_at) > Date.parse(value.as_of) ||
    !uuid(source.market_snapshot_id) || !uuid(source.source_archive_id) ||
    typeof source.source_archive_digest !== 'string' || !/^[a-f0-9]{64}$/.test(source.source_archive_digest) ||
    !text(source.calendar_version) || !instant(source.as_of) || Date.parse(source.as_of) !== Date.parse(value.as_of) ||
    !['contemporaneous', 'retrospective'].includes(String(source.provenance)) ||
    !['requested', 'adjusted', 'available', 'archive'].every(k => bounds(d[k])) ||
    !partition(d.coverage) || !Array.isArray(d.points) || !d.points.length || d.points.length > 250) return 'malformed_response';
  const requested = d.requested as unknown as Bounds, adjusted = d.adjusted as unknown as Bounds;
  const available = d.available as unknown as Bounds, archive = d.archive as unknown as Bounds;
  if (requested.start !== selection.start || requested.end !== selection.end) return 'selection_mismatch';
  const canonical = sessions.filter(s => s <= selection.date);
  const selected = canonical.filter(s => s >= selection.start && s <= selection.end);
  const retained = canonical.slice(-250);
  const expected = selected.filter(s => s >= archive.start && s <= archive.end);
  if (!selected.length || selected.length > 250 || adjusted.start !== selected[0] || adjusted.end !== selected.at(-1) ||
    archive.start !== retained[0] || archive.end !== selection.date ||
    available.start !== expected[0] || available.end !== expected.at(-1) ||
    d.points.length !== expected.length) return 'malformed_response';
  let valid = 0, candles = 0;
  for (const [i, p] of d.points.entries()) {
    if (!object(p) || p.trade_date !== expected[i] || p.source_id !== source.source_id || p.source_version !== source.source_version ||
      p.provenance !== source.provenance || !['open', 'high', 'low', 'close'].every(k => p[k] === null || decimal(p[k], true)) ||
      !['volume', 'amount'].every(k => p[k] === null || decimal(p[k], false)) ||
      (basis.volume_unit === 'not_applicable' && p.volume !== null) || (basis.amount_currency === null && p.amount !== null)) return 'malformed_response';
    if (p.close === null) {
      if (!['open', 'high', 'low', 'volume', 'amount', 'available_at'].every(k => p[k] === null) ||
        p.reason !== 'observation_missing' || p.chart_type !== 'gap') return 'malformed_response';
    } else {
      valid++;
      const complete = ['open', 'high', 'low'].every(k => p[k] !== null);
      if (!instant(p.available_at) || Date.parse(p.available_at) > Date.parse(source.as_of) ||
        new Date(Date.parse(p.available_at) + 8 * 3600000).toISOString().slice(0, 10) < String(p.trade_date) ||
        p.reason !== null || p.chart_type !== (complete ? 'candle' : 'line') ||
        (!complete && !['open', 'high', 'low'].every(k => p[k] === null))) return 'malformed_response';
      if (complete) {
        candles++;
        const low = new Decimal(String(p.low)), high = new Decimal(String(p.high));
        if (low.gt(String(p.open)) || low.gt(String(p.close)) || high.lt(String(p.open)) || high.lt(String(p.close))) return 'malformed_response';
      }
    }
  }
  const c = d.coverage as Record<string, unknown>;
  const envelopeCoverage = value.coverage as Record<string, unknown>;
  if (c.expected !== expected.length || c.valid !== valid || c.excluded !== 0 || c.missing !== expected.length - valid ||
    ['expected', 'valid', 'excluded', 'missing'].some(k => envelopeCoverage[k] !== c[k]) ||
    d.chart_type !== (!valid ? 'gap' : candles === expected.length ? 'candle' : 'line')) return 'malformed_response';
  return undefined;
}

export async function fetchIndexHistory(selection: IndexSelection, sessions: readonly string[], signal?: AbortSignal): Promise<IndexHistoryResponse> {
  if (!text(selection.securityId) || /[/\\?#]/.test(selection.securityId) || ['.', '..'].includes(selection.securityId) ||
    !uuid(selection.snapshot) || ![selection.date, selection.start, selection.end].every(day) ||
    selection.start > selection.end || selection.end > selection.date || !calendar(sessions)) throw new ApiError(400);
  const query = new URLSearchParams({ snapshot_id: selection.snapshot, date: selection.date,
    start: selection.start, end: selection.end, adj: 'raw' });
  const controller = new AbortController();
  let rejectAbort: (reason: unknown) => void = () => {};
  const interrupted = new Promise<never>((_, reject) => { rejectAbort = reject; });
  const abort = () => { rejectAbort(new DOMException('请求已取消', 'AbortError')); controller.abort(); };
  signal?.addEventListener('abort', abort, { once: true });
  if (signal?.aborted) abort();
  const timeout = setTimeout(() => { rejectAbort(new ApiError(408)); controller.abort(); }, 15000);
  try {
    if (signal?.aborted) return await interrupted;
    const response = await Promise.race([fetch(`/api/index/${encodeURIComponent(selection.securityId)}/history?${query}`, {
      method: 'GET', credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
      headers: { Accept: 'application/json' },
    }), interrupted]);
    if (!response.ok) {
      let code: string | undefined;
      try { const payload = await Promise.race([response.json(), interrupted]); code = typeof payload?.code === 'string' ? payload.code : undefined; }
      catch (error) { if (error instanceof ApiError || (error instanceof DOMException && error.name === 'AbortError')) throw error; }
      throw new ApiError(response.status, undefined, code);
    }
    let data: unknown;
    try { data = await Promise.race([response.json(), interrupted]); }
    catch (error) {
      if (error instanceof ApiError || (error instanceof DOMException && error.name === 'AbortError')) throw error;
      throw new ApiError(502, '指数历史响应格式无效，已丢弃该响应。', 'malformed_response');
    }
    const failure = validateIndexHistory(data, selection, sessions);
    if (failure === 'selection_mismatch') throw new ApiError(409, '指数历史响应与当前选择不匹配，已丢弃该响应。');
    if (failure) throw new ApiError(502, '指数历史响应格式无效，已丢弃该响应。', 'malformed_response');
    return data as IndexHistoryResponse;
  } catch (error) {
    if (error instanceof ApiError || (error instanceof DOMException && error.name === 'AbortError')) throw error;
    throw new ApiError(0);
  } finally { clearTimeout(timeout); signal?.removeEventListener('abort', abort); }
}

export function indexHistorySegments(points: readonly IndexPoint[]): number[][] {
  const segments: number[][] = [];
  let current: number[] = [];
  points.forEach((point, i) => {
    if (point.close === null) { if (current.length) segments.push(current); current = []; }
    else current.push(i);
  });
  if (current.length) segments.push(current);
  return segments;
}

import Decimal from 'decimal.js';
import { ApiError } from '../../api';
import { parseRealtimeJson, RealtimeDataFormatError } from './json';
import type {
  AnomalyData,
  AnomalyEvent,
  AnomalyResponse,
  AnomalySelection,
  MinuteBar,
  MinuteData,
  MinuteResponse,
  MinuteSelection,
  QuoteData,
  QuoteResponse,
  QuoteSelection,
  RealtimeBasis,
  RealtimeCoverage,
  RealtimeEnvelope,
  RealtimeQuote,
  RealtimeStatus,
  SectorFlowData,
  SectorFlowPoint,
  SectorFlowResponse,
  SectorFlowSelection,
} from './types';

const ENVELOPE_KEYS = [
  'trade_date', 'as_of', 'snapshot_id', 'source', 'source_version',
  'coverage', 'quality_flags', 'rule_version', 'basis', 'data',
] as const;
const BASIS_KEYS = ['unit', 'currency', 'price_type', 'benchmark'] as const;
const COVERAGE_KEYS = ['expected', 'valid', 'excluded', 'missing'] as const;
const QUOTE_DATA_KEYS = [
  'quote_batch_id', 'source_event_at', 'received_at', 'delay_ms', 'status', 'quotes',
] as const;
const QUOTE_KEYS = [
  'security_id', 'last', 'bid', 'ask', 'bid_size', 'ask_size', 'volume', 'amount',
  'order_book_status', 'order_book_unavailable_reason',
] as const;
const MINUTE_DATA_KEYS = ['status', 'bars'] as const;
const MINUTE_KEYS = [
  'batch_id', 'source_id', 'source_version', 'security_id', 'event_at', 'received_at',
  'available_at', 'open', 'high', 'low', 'close', 'volume', 'amount', 'revision', 'final',
] as const;
const ANOMALY_DATA_KEYS = ['status', 'events', 'next_cursor'] as const;
const ANOMALY_KEYS = [
  'event_id', 'batch_id', 'security_id', 'rule_version', 'event_at', 'available_at',
  'metric', 'window', 'value', 'threshold', 'quality',
] as const;
const FLOW_DATA_KEYS = ['status', 'points'] as const;
const FLOW_KEYS = [
  'batch_id', 'source_id', 'source_version', 'sector', 'event_at', 'available_at',
  'net_inflow', 'unit', 'unavailable_reason',
] as const;
const STATUSES = new Set<RealtimeStatus>(['complete', 'delayed', 'incomplete', 'unavailable']);
const METRICS = new Set(['window_return', 'relative_volume', 'order_book_imbalance']);
const ORDER_BOOK_STATUSES = new Set(['available', 'disabled', 'unavailable']);
const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/;
const UNIT = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$/;
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const TIMESTAMP = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(Z|([+-])(\d{2}):(\d{2}))$/;
const JSON_DECIMAL = /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$/;
const ZERO_DECIMAL = /^-?0(?:\.0*)?(?:[eE][+-]?\d+)?$/;
const NEGATIVE_NONZERO_DECIMAL = /^-(?!0(?:\.0*)?(?:[eE][+-]?\d+)?$)/;
const SHANGHAI_DATE = new Intl.DateTimeFormat('en', {
  timeZone: 'Asia/Shanghai',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
});

type RecordValue = Record<string, unknown>;

export async function requestRealtime<T>(
  path: string,
  options: { signal?: AbortSignal } = {},
): Promise<T> {
  const allowed = /^\/api\/(?:realtime\/(?:quotes|minutes|anomalies)|market\/sector-flow\/intraday)(?:[?#]|$)/i.test(path);
  if (!allowed || path.includes('\\')) throw new ApiError(400, '仅允许同源只读接口。');
  const controller = new AbortController();
  let rejectAbort: (reason: unknown) => void = () => {};
  const interrupted = new Promise<never>((_, reject) => { rejectAbort = reject; });
  const abort = () => {
    controller.abort();
    rejectAbort(new DOMException('请求已取消', 'AbortError'));
  };
  options.signal?.addEventListener('abort', abort, { once: true });
  if (options.signal?.aborted) abort();
  const timeout = setTimeout(() => {
    rejectAbort(new ApiError(408));
    controller.abort();
  }, 15000);
  try {
    const response = await Promise.race([
      fetch(path, {
        method: 'GET',
        credentials: 'same-origin',
        cache: 'no-store',
        signal: controller.signal,
        headers: { Accept: 'application/json' },
      }),
      interrupted,
    ]);
    if (!response.ok) {
      let code: string | undefined;
      try {
        const data = await Promise.race([response.json(), interrupted]);
        code = typeof data?.code === 'string' ? data.code : undefined;
      } catch {
        // Fixed messages do not echo server payloads.
      }
      throw new ApiError(response.status, undefined, code);
    }
    try {
      const text = await Promise.race([response.text(), interrupted]);
      return parseRealtimeJson(text, path) as T;
    } catch (error) {
      if (error instanceof ApiError
        || (error instanceof DOMException && error.name === 'AbortError')) throw error;
      if (error instanceof RealtimeDataFormatError) {
        throw new ApiError(
          502,
          '当前运行环境不支持无损实时数值解析，已拒绝该响应。',
          'DATA_FORMAT',
        );
      }
      throw new ApiError(502, '接口响应格式无效，请重试。');
    }
  } catch (error) {
    if (error instanceof ApiError
      || (error instanceof DOMException && error.name === 'AbortError')) throw error;
    throw new ApiError(0);
  } finally {
    clearTimeout(timeout);
    options.signal?.removeEventListener('abort', abort);
  }
}

function fail(field: string): never {
  throw new Error(`Invalid realtime ${field}`);
}

function record(value: unknown, field: string): RecordValue {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) fail(field);
  return value as RecordValue;
}

function exact(value: RecordValue, keys: readonly string[], field: string): void {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
    fail(`${field} keys`);
  }
}

function identifier(value: unknown, field: string): string {
  if (typeof value !== 'string' || !IDENTIFIER.test(value)) fail(field);
  return value;
}

function nullableIdentifier(value: unknown, field: string): string | null {
  return value === null ? null : identifier(value, field);
}

function unit(value: unknown, field: string): string {
  if (typeof value !== 'string' || !UNIT.test(value)) fail(field);
  return value;
}

function nullableUnit(value: unknown, field: string): string | null {
  return value === null ? null : unit(value, field);
}

function validDecimalToken(text: string): boolean {
  return JSON_DECIMAL.test(text);
}

function finite(value: unknown, field: string, nullable = false): string | null {
  if (nullable && value === null) return null;
  const text = typeof value === 'number'
    ? String(value)
    : typeof value === 'string'
      ? value
      : null;
  if (text === null || !validDecimalToken(text)) fail(field);
  return text;
}

function nonnegative(value: unknown, field: string, nullable = false): string | null {
  const result = finite(value, field, nullable);
  if (result !== null && NEGATIVE_NONZERO_DECIMAL.test(result)) fail(field);
  return result;
}

export function decimalForChart(value: string): number | null {
  if (!validDecimalToken(value)) return null;
  try {
    const exact = new Decimal(value);
    if (!exact.isFinite() || exact.abs().gt(Number.MAX_SAFE_INTEGER)) return null;
    const converted = exact.toNumber();
    if (!Number.isFinite(converted) || (converted === 0 && !ZERO_DECIMAL.test(value))) return null;
    return converted;
  } catch {
    return null;
  }
}

function integer(value: unknown, field: string, minimum = 0): number {
  const parsed = typeof value === 'string' ? Number(value) : value;
  if (!Number.isSafeInteger(parsed) || (parsed as number) < minimum) fail(field);
  return parsed as number;
}

function dateValue(value: unknown, field: string): string {
  if (typeof value !== 'string' || !DATE.test(value)) fail(field);
  const parsed = new Date(`${value}T00:00:00Z`);
  if (!Number.isFinite(parsed.valueOf()) || parsed.toISOString().slice(0, 10) !== value) fail(field);
  return value;
}

function timestamp(value: unknown, field: string): string {
  if (typeof value !== 'string') fail(field);
  epochMicroseconds(value, field);
  return value;
}

function epochMicroseconds(value: string, field: string): bigint {
  const match = TIMESTAMP.exec(value);
  if (!match) fail(field);
  const [, yearText, monthText, dayText, hourText, minuteText, secondText,
    fraction = '', zone, offsetSign, offsetHourText = '0', offsetMinuteText = '0'] = match;
  const [year, month, day, hour, minute, second, offsetHour, offsetMinute] = [
    yearText, monthText, dayText, hourText, minuteText, secondText,
    offsetHourText, offsetMinuteText,
  ].map(Number);
  if (hour > 23 || minute > 59 || second > 59 || offsetHour > 23 || offsetMinute > 59) {
    fail(field);
  }
  const local = new Date(0);
  local.setUTCFullYear(year, month - 1, day);
  local.setUTCHours(hour, minute, second, 0);
  if (local.getUTCFullYear() !== year
    || local.getUTCMonth() !== month - 1
    || local.getUTCDate() !== day) {
    fail(field);
  }
  const offsetDirection = zone === 'Z' ? 0 : offsetSign === '+' ? 1 : -1;
  const offsetMilliseconds = offsetDirection * (offsetHour * 60 + offsetMinute) * 60_000;
  return BigInt(local.getTime() - offsetMilliseconds) * 1_000n
    + BigInt(fraction.padEnd(6, '0'));
}

function validateEventDate(value: string, selected: string): void {
  const parts = Object.fromEntries(
    SHANGHAI_DATE.formatToParts(new Date(value))
      .filter(part => part.type !== 'literal')
      .map(part => [part.type, part.value]),
  );
  if (`${parts.year}-${parts.month}-${parts.day}` !== selected) fail('event date');
}

function status(value: unknown): RealtimeStatus {
  if (typeof value !== 'string' || !STATUSES.has(value as RealtimeStatus)) fail('status');
  return value as RealtimeStatus;
}

function nullableString(value: unknown, field: string): string | null {
  if (value === null) return null;
  if (typeof value !== 'string' || value.trim() === '') fail(field);
  return value;
}

function coverage(value: unknown): RealtimeCoverage | null {
  if (value === null) return null;
  const item = record(value, 'coverage');
  exact(item, COVERAGE_KEYS, 'coverage');
  const result = {
    expected: integer(item.expected, 'coverage expected'),
    valid: integer(item.valid, 'coverage valid'),
    excluded: integer(item.excluded, 'coverage excluded'),
    missing: integer(item.missing, 'coverage missing'),
  };
  if (result.valid + result.excluded + result.missing !== result.expected) {
    fail('coverage counts');
  }
  if (result.excluded !== 0) fail('coverage exclusions');
  return result;
}

function realtimeBasis(value: unknown): RealtimeBasis {
  const item = record(value, 'basis');
  exact(item, BASIS_KEYS, 'basis');
  if (item.price_type !== 'raw') fail('basis price_type');
  return {
    unit: nullableUnit(item.unit, 'basis unit'),
    currency: nullableIdentifier(item.currency, 'basis currency'),
    price_type: 'raw',
    benchmark: nullableIdentifier(item.benchmark, 'basis benchmark'),
  };
}

function baseEnvelope(value: unknown): RealtimeEnvelope<unknown> {
  const item = record(value, 'envelope');
  exact(item, ENVELOPE_KEYS, 'envelope');
  if (item.trade_date !== null) dateValue(item.trade_date, 'trade date');
  const flags = item.quality_flags;
  if (!Array.isArray(flags) || flags.length > 1
    || !flags.every(flag => typeof flag === 'string' && flag.trim() !== '')) {
    fail('quality flags');
  }
  if (item.snapshot_id !== null || item.rule_version !== null) fail('intraday identity');
  const result: RealtimeEnvelope<unknown> = {
    trade_date: item.trade_date as string | null,
    as_of: timestamp(item.as_of, 'as_of'),
    snapshot_id: null,
    source: nullableIdentifier(item.source, 'source'),
    source_version: nullableIdentifier(item.source_version, 'source version'),
    coverage: coverage(item.coverage),
    quality_flags: [...flags],
    rule_version: null,
    basis: realtimeBasis(item.basis),
    data: item.data,
  };
  if (result.quality_flags.length === 0
    && (result.coverage === null || result.coverage.missing !== 0)) {
    fail('completeness');
  }
  return result;
}

function validateCompleteness(
  value: RealtimeStatus,
  envelope: RealtimeEnvelope<unknown>,
): RealtimeStatus {
  const known = envelope.coverage;
  const hasReason = envelope.quality_flags.length === 1;
  if (value === 'complete') {
    if (known === null || known.missing !== 0 || hasReason) fail('completeness');
  } else if (value === 'incomplete') {
    if (known === null || known.missing === 0 || !hasReason) fail('completeness');
  } else if (!hasReason) {
    fail('completeness');
  }
  return value;
}

function query(entries: [string, string | undefined][]): string {
  return new URLSearchParams(
    entries.filter((entry): entry is [string, string] => entry[1] !== undefined),
  ).toString();
}

function selectionBatch(selection: { sourceId?: string; batchId?: string }): void {
  if (selection.batchId && !selection.sourceId) {
    throw new Error('Explicit realtime batch selection requires source');
  }
}

function validateAsOfSelection(asOf: string, selected?: string): void {
  if (selected
    && epochMicroseconds(timestamp(selected, 'selected as_of'), 'selected as_of')
      !== epochMicroseconds(asOf, 'as_of')) {
    fail('as_of selection');
  }
}

function validateKnownByAsOf(value: string, asOf: string): void {
  if (epochMicroseconds(value, 'record time') > epochMicroseconds(asOf, 'as_of')) {
    fail('as_of record time');
  }
}

export function quotePath(selection: QuoteSelection): string {
  selectionBatch(selection);
  return `/api/realtime/quotes?${query([
    ['security_ids', selection.securityIds.join(',')],
    ['as_of', selection.asOf],
    ['source_id', selection.sourceId],
    ['quote_batch_id', selection.batchId],
  ])}`;
}

export function minutePath(selection: MinuteSelection): string {
  selectionBatch(selection);
  return `/api/realtime/minutes?${query([
    ['security_id', selection.securityId],
    ['date', selection.date],
    ['as_of', selection.asOf],
    ['source_id', selection.sourceId],
    ['batch_id', selection.batchId],
  ])}`;
}

export function anomalyPath(selection: AnomalySelection): string {
  return `/api/realtime/anomalies?${query([
    ['date', selection.date],
    ['as_of', selection.asOf],
    ['source_id', selection.sourceId],
    ['security_id', selection.securityId],
    ['cursor', selection.cursor],
    ['limit', selection.limit === undefined ? undefined : String(selection.limit)],
  ])}`;
}

export function sectorFlowPath(selection: SectorFlowSelection): string {
  selectionBatch(selection);
  return `/api/market/sector-flow/intraday?${query([
    ['sector', selection.sectorId],
    ['date', selection.date],
    ['as_of', selection.asOf],
    ['source_id', selection.sourceId],
    ['batch_id', selection.batchId],
  ])}`;
}

export function realtimeRequestPaths(
  selectedDate: string,
  securityIds: string[],
  sectorId?: string,
): { quote: string | null; minute: string | null; anomaly: string | null; flow: string | null } {
  try {
    dateValue(selectedDate, 'selected date');
  } catch {
    return { quote: null, minute: null, anomaly: null, flow: null };
  }
  const primary = securityIds[0];
  return {
    quote: primary ? quotePath({ securityIds }) : null,
    minute: primary ? minutePath({ securityId: primary, date: selectedDate }) : null,
    anomaly: primary
      ? anomalyPath({ securityId: primary, date: selectedDate, limit: 50 })
      : null,
    flow: sectorId ? sectorFlowPath({ sectorId, date: selectedDate }) : null,
  };
}

function validateQuote(value: unknown): RealtimeQuote {
  const item = record(value, 'quote');
  exact(item, QUOTE_KEYS, 'quote');
  const result: RealtimeQuote = {
    security_id: identifier(item.security_id, 'quote security'),
    last: nonnegative(item.last, 'quote last', true),
    bid: nonnegative(item.bid, 'quote bid', true),
    ask: nonnegative(item.ask, 'quote ask', true),
    bid_size: nonnegative(item.bid_size, 'quote bid size', true),
    ask_size: nonnegative(item.ask_size, 'quote ask size', true),
    volume: nonnegative(item.volume, 'quote volume', true),
    amount: nonnegative(item.amount, 'quote amount', true),
    order_book_status: item.order_book_status as RealtimeQuote['order_book_status'],
    order_book_unavailable_reason: nullableIdentifier(
      item.order_book_unavailable_reason,
      'quote order book reason',
    ),
  };
  if (!ORDER_BOOK_STATUSES.has(result.order_book_status)) fail('quote order book status');
  const book = [result.bid, result.ask, result.bid_size, result.ask_size];
  if (result.order_book_status === 'available') {
    if (book.some(field => field === null) || result.order_book_unavailable_reason !== null) {
      fail('quote order book reason');
    }
  } else if (book.some(field => field !== null) || result.order_book_unavailable_reason === null) {
    fail('quote order book reason');
  }
  return result;
}

function validateCoverageCount(
  value: RealtimeCoverage | null,
  returned: number,
  resource: string,
): void {
  if (value !== null && value.valid !== returned) fail(`${resource} coverage valid`);
}

function validateQuoteCoverage(value: RealtimeCoverage | null, requested: number, returned: number) {
  if (value === null || value.expected !== requested) fail('quote coverage expected');
  validateCoverageCount(value, returned, 'quote');
}

export function validateQuoteResponse(value: unknown, selection: QuoteSelection): QuoteResponse {
  const base = baseEnvelope(value);
  validateAsOfSelection(base.as_of, selection.asOf);
  if (base.trade_date !== null) fail('quote trade date');
  if (selection.sourceId && base.source !== selection.sourceId) fail('quote source selection');
  if (base.data === null) {
    validateQuoteCoverage(base.coverage, selection.securityIds.length, 0);
    return base as QuoteResponse;
  }
  const data = record(base.data, 'quote data');
  exact(data, QUOTE_DATA_KEYS, 'quote data');
  const sourceEvent = timestamp(data.source_event_at, 'quote source timestamp');
  const received = timestamp(data.received_at, 'quote received timestamp');
  if (epochMicroseconds(received, 'quote received timestamp')
    < epochMicroseconds(sourceEvent, 'quote source timestamp')) fail('quote timestamp order');
  validateKnownByAsOf(received, base.as_of);
  const quotes = data.quotes;
  if (!Array.isArray(quotes)) fail('quotes');
  const parsed = quotes.map(validateQuote);
  if (new Set(parsed.map(item => item.security_id)).size !== parsed.length
    || parsed.some(item => !selection.securityIds.includes(item.security_id))) {
    fail('quote security selection');
  }
  validateQuoteCoverage(base.coverage, selection.securityIds.length, parsed.length);
  const batchId = identifier(data.quote_batch_id, 'quote batch');
  if (selection.batchId && batchId !== selection.batchId) fail('quote batch selection');
  const completeness = validateCompleteness(status(data.status), base);
  return {
    ...base,
    data: {
      quote_batch_id: batchId,
      source_event_at: sourceEvent,
      received_at: received,
      delay_ms: integer(data.delay_ms, 'quote delay'),
      status: completeness,
      quotes: parsed,
    },
  };
}

function validateMinuteBar(value: unknown): MinuteBar {
  const item = record(value, 'minute bar');
  exact(item, MINUTE_KEYS, 'minute bar');
  const eventAt = timestamp(item.event_at, 'minute event timestamp');
  const receivedAt = timestamp(item.received_at, 'minute received timestamp');
  const availableAt = timestamp(item.available_at, 'minute available timestamp');
  if (epochMicroseconds(receivedAt, 'minute received timestamp')
      < epochMicroseconds(eventAt, 'minute event timestamp')
    || epochMicroseconds(availableAt, 'minute available timestamp')
      < epochMicroseconds(receivedAt, 'minute received timestamp')) fail('minute timestamp order');
  const open = nonnegative(item.open, 'minute open') as string;
  const high = nonnegative(item.high, 'minute high') as string;
  const low = nonnegative(item.low, 'minute low') as string;
  const close = nonnegative(item.close, 'minute close') as string;
  if (new Decimal(high).lt(Decimal.max(open, close, low))
    || new Decimal(low).gt(Decimal.min(open, close, high))) fail('minute OHLC');
  if (typeof item.final !== 'boolean') fail('minute final');
  return {
    batch_id: identifier(item.batch_id, 'minute batch'),
    source_id: identifier(item.source_id, 'minute source'),
    source_version: identifier(item.source_version, 'minute source version'),
    security_id: identifier(item.security_id, 'minute security'),
    event_at: eventAt,
    received_at: receivedAt,
    available_at: availableAt,
    open,
    high,
    low,
    close,
    volume: nonnegative(item.volume, 'minute volume', true),
    amount: nonnegative(item.amount, 'minute amount', true),
    revision: integer(item.revision, 'minute revision', 1),
    final: item.final,
  };
}

export function validateMinuteResponse(value: unknown, selection: MinuteSelection): MinuteResponse {
  const base = baseEnvelope(value);
  validateAsOfSelection(base.as_of, selection.asOf);
  if (base.trade_date !== selection.date) fail('minute date selection');
  if (selection.sourceId && base.source !== selection.sourceId) fail('minute source selection');
  if (base.data === null) {
    validateCoverageCount(base.coverage, 0, 'minute');
    return base as MinuteResponse;
  }
  const data = record(base.data, 'minute data');
  exact(data, MINUTE_DATA_KEYS, 'minute data');
  if (!Array.isArray(data.bars)) fail('minute bars');
  const bars = data.bars.map(validateMinuteBar);
  const identities = new Set<string>();
  let previous: bigint | null = null;
  for (const bar of bars) {
    const identity = `${bar.source_id}\0${bar.security_id}\0${bar.event_at}`;
    const eventTime = epochMicroseconds(bar.event_at, 'minute event timestamp');
    if (identities.has(identity) || (previous !== null && eventTime < previous)) {
      fail('minute ordering');
    }
    identities.add(identity);
    previous = eventTime;
    if (bar.security_id !== selection.securityId) fail('minute security selection');
    if (selection.sourceId && bar.source_id !== selection.sourceId) fail('minute source selection');
    if (selection.batchId && bar.batch_id !== selection.batchId) fail('minute batch selection');
    if (base.source && bar.source_id !== base.source) fail('minute source identity');
    if (base.source_version && bar.source_version !== base.source_version) fail('minute source version');
    validateKnownByAsOf(bar.received_at, base.as_of);
    validateKnownByAsOf(bar.available_at, base.as_of);
    validateEventDate(bar.event_at, selection.date);
  }
  const minuteStatus = validateCompleteness(status(data.status), base);
  validateCoverageCount(base.coverage, bars.length, 'minute');
  return {
    ...base,
    data: { status: minuteStatus, bars },
  };
}

function validateAnomaly(value: unknown): AnomalyEvent {
  const item = record(value, 'anomaly');
  exact(item, ANOMALY_KEYS, 'anomaly');
  if (!METRICS.has(item.metric as string)) fail('anomaly metric');
  if (!['complete', 'delayed', 'incomplete'].includes(item.quality as string)) {
    fail('anomaly quality');
  }
  const eventAt = timestamp(item.event_at, 'anomaly event timestamp');
  const availableAt = timestamp(item.available_at, 'anomaly available timestamp');
  if (epochMicroseconds(availableAt, 'anomaly available timestamp')
    < epochMicroseconds(eventAt, 'anomaly event timestamp')) fail('anomaly timestamp order');
  return {
    event_id: identifier(item.event_id, 'anomaly event'),
    batch_id: identifier(item.batch_id, 'anomaly batch'),
    security_id: identifier(item.security_id, 'anomaly security'),
    rule_version: identifier(item.rule_version, 'anomaly rule'),
    event_at: eventAt,
    available_at: availableAt,
    metric: item.metric as AnomalyEvent['metric'],
    window: integer(item.window, 'anomaly window', 2),
    value: finite(item.value, 'anomaly value', true),
    threshold: finite(item.threshold, 'anomaly threshold') as string,
    quality: item.quality as AnomalyEvent['quality'],
  };
}

export function validateAnomalyResponse(
  value: unknown,
  selection: AnomalySelection,
): AnomalyResponse {
  const base = baseEnvelope(value);
  validateAsOfSelection(base.as_of, selection.asOf);
  if (base.trade_date !== selection.date) fail('anomaly date selection');
  if (selection.sourceId && base.source !== selection.sourceId) fail('anomaly source selection');
  if (base.data === null) {
    validateCoverageCount(base.coverage, 0, 'anomaly');
    return base as AnomalyResponse;
  }
  const data = record(base.data, 'anomaly data');
  exact(data, ANOMALY_DATA_KEYS, 'anomaly data');
  if (!Array.isArray(data.events)) fail('anomaly events');
  const events = data.events.map(validateAnomaly);
  events.forEach(event => {
    validateEventDate(event.event_at, selection.date);
    validateKnownByAsOf(event.available_at, base.as_of);
  });
  if (selection.securityId && events.some(item => item.security_id !== selection.securityId)) {
    fail('anomaly security selection');
  }
  const cursor = data.next_cursor;
  if (cursor !== null && (typeof cursor !== 'string' || cursor.length === 0 || cursor.length > 4096)) {
    fail('anomaly cursor');
  }
  const anomalyStatus = validateCompleteness(status(data.status), base);
  validateCoverageCount(base.coverage, events.length, 'anomaly');
  return {
    ...base,
    data: {
      status: anomalyStatus,
      events,
      next_cursor: cursor as string | null,
    },
  };
}

function validateFlowPoint(value: unknown): SectorFlowPoint {
  const item = record(value, 'sector flow point');
  exact(item, FLOW_KEYS, 'sector flow point');
  const netInflow = finite(item.net_inflow, 'sector flow value', true);
  const reason = nullableIdentifier(item.unavailable_reason, 'sector flow reason');
  if ((netInflow === null) === (reason === null)) fail('sector flow reason');
  const eventAt = timestamp(item.event_at, 'sector flow event timestamp');
  const availableAt = timestamp(item.available_at, 'sector flow available timestamp');
  if (epochMicroseconds(availableAt, 'sector flow available timestamp')
    < epochMicroseconds(eventAt, 'sector flow event timestamp')) fail('sector flow timestamp order');
  return {
    batch_id: identifier(item.batch_id, 'sector flow batch'),
    source_id: identifier(item.source_id, 'sector flow source'),
    source_version: identifier(item.source_version, 'sector flow source version'),
    sector: identifier(item.sector, 'sector flow sector'),
    event_at: eventAt,
    available_at: availableAt,
    net_inflow: netInflow,
    unit: unit(item.unit, 'sector flow unit'),
    unavailable_reason: reason,
  };
}

export function validateSectorFlowResponse(
  value: unknown,
  selection: SectorFlowSelection,
): SectorFlowResponse {
  const base = baseEnvelope(value);
  validateAsOfSelection(base.as_of, selection.asOf);
  if (base.trade_date !== selection.date) fail('sector flow date selection');
  if (selection.sourceId && base.source !== selection.sourceId) fail('sector flow source selection');
  if (base.data === null) {
    validateCoverageCount(base.coverage, 0, 'sector flow');
    return base as SectorFlowResponse;
  }
  const data = record(base.data, 'sector flow data');
  exact(data, FLOW_DATA_KEYS, 'sector flow data');
  if (!Array.isArray(data.points)) fail('sector flow points');
  const points = data.points.map(validateFlowPoint);
  const flowStatus = validateCompleteness(status(data.status), base);
  const unavailablePoint = points.find(point => point.net_inflow === null);
  if (unavailablePoint) {
    if (flowStatus !== 'unavailable'
      || base.quality_flags[0] !== unavailablePoint.unavailable_reason) {
      fail('sector flow availability');
    }
  } else if (flowStatus === 'unavailable') {
    fail('sector flow availability');
  }
  for (const point of points) {
    if (point.sector !== selection.sectorId) fail('sector flow sector selection');
    if (selection.sourceId && point.source_id !== selection.sourceId) fail('sector flow source');
    if (selection.batchId && point.batch_id !== selection.batchId) fail('sector flow batch');
    if (base.source && point.source_id !== base.source) fail('sector flow source identity');
    if (base.source_version && point.source_version !== base.source_version) {
      fail('sector flow source version');
    }
    validateEventDate(point.event_at, selection.date);
    validateKnownByAsOf(point.available_at, base.as_of);
  }
  validateCoverageCount(base.coverage, points.length, 'sector flow');
  return {
    ...base,
    data: { status: flowStatus, points },
  };
}

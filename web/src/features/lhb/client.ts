import { useEffect, useRef, useState } from 'react';
import Decimal from 'decimal.js';
import { ApiError, requestGeneration } from '../../api';
import type { Resource } from '../../hooks/useResource';
import type {
  AggregatedSeat,
  DisclosureData,
  DisclosureItem,
  DisclosureListResponse,
  DisclosureSeat,
  DisclosureSelection,
  IdentityState,
  LhbBasis,
  LhbCoverage,
  LhbEnvelope,
  PermissionState,
  SeatCompleteness,
  SeatContribution,
  SeatHistoryData,
  SeatHistoryResponse,
  SeatHistorySelection,
  SeatIdentity,
  SeatSide,
} from './types';

interface JsonParseContext {
  source?: string;
}

type JsonReviver = (
  this: unknown,
  key: string,
  value: unknown,
  context: JsonParseContext,
) => unknown;

type StructuredJsonParse = (text: string, reviver?: JsonReviver) => unknown;

const DECIMAL_FIELDS = new Set(['buy_amount', 'sell_amount', 'net_amount']);

export class LhbDataFormatError extends Error {
  constructor() {
    super('JSON.parse context.source is unsupported');
    this.name = 'LhbDataFormatError';
  }
}

function nativeParse(): StructuredJsonParse {
  return JSON.parse as StructuredJsonParse;
}

export function supportsJsonParseSource(parse: StructuredJsonParse = nativeParse()): boolean {
  let supported = false;
  try {
    parse('0', (_key, value, context) => {
      supported ||= context?.source === '0';
      return value;
    });
  } catch {
    return false;
  }
  return supported;
}

const LHB_PATH = /^\/api\/lhb(?:\/seats\/[^?#/]+)?(?:[?#]|$)/i;

export function parseLhbJson(
  text: string,
  path: string,
  parse: StructuredJsonParse = nativeParse(),
): unknown {
  if (!supportsJsonParseSource(parse)) throw new LhbDataFormatError();
  if (!LHB_PATH.test(path)) throw new LhbDataFormatError();
  return parse(text, (key, value, context) => {
    if (typeof value !== 'number' || !DECIMAL_FIELDS.has(key)) return value;
    if (typeof context?.source !== 'string') throw new LhbDataFormatError();
    return context.source;
  });
}

export async function requestLhb<T>(
  path: string,
  options: { signal?: AbortSignal } = {},
): Promise<T> {
  const allowed = LHB_PATH.test(path);
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
      return parseLhbJson(text, path) as T;
    } catch (error) {
      if (error instanceof ApiError
        || (error instanceof DOMException && error.name === 'AbortError')) throw error;
      if (error instanceof LhbDataFormatError) {
        throw new ApiError(
          502,
          '当前运行环境不支持无损龙虎榜数值解析，已拒绝该响应。',
          'DATA_FORMAT',
        );
      }
      throw new ApiError(502, '龙虎榜接口响应格式无效，请重试。', 'malformed_response');
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

const ENVELOPE_KEYS = [
  'date', 'as_of', 'source', 'source_version', 'quality_flags',
  'basis', 'coverage', 'data',
] as const;
const BASIS_KEYS = ['unit', 'currency'] as const;
const COVERAGE_KEYS = [
  'total_disclosures', 'visible_disclosures', 'late_disclosures',
  'selected_disclosures', 'seat_count', 'complete_seats', 'unknown_seats',
] as const;
const DISCLOSURE_DATA_KEYS = ['items', 'total', 'page', 'size'] as const;
const SEAT_KEYS = [
  'source_row_id', 'seat_id', 'source_seat_name', 'mapping_version', 'side',
  'rank', 'buy_amount', 'sell_amount', 'currency', 'unit', 'completeness',
  'net_amount',
] as const;
const ITEM_KEYS = [
  'disclosure_id', 'security_id', 'disclosure_date', 'reason', 'revision',
  'source_id', 'source_version', 'source_event_at', 'received_at',
  'available_at', 'seats',
] as const;
const HISTORY_DATA_KEYS = ['seat', 'identity_state', 'items', 'total'] as const;
const SEAT_IDENTITY_KEYS = [
  'source_id', 'seat_id', 'source_seat_name', 'mapping_version',
] as const;
const AGGREGATED_KEYS = [
  'seat_id', 'source_id', 'source_version', 'mapping_version',
  'source_seat_name', 'currency', 'unit', 'buy_amount', 'sell_amount',
  'net_amount', 'completeness', 'contributions',
] as const;
const CONTRIBUTION_KEYS = [
  'disclosure_id', 'security_id', 'revision', 'disclosure_date', 'reason',
  'source_row_id', 'source_seat_name', 'mapping_version', 'permission_state',
] as const;
const COMPLETENESSES = new Set<SeatCompleteness>([
  'complete', 'buy_unknown', 'sell_unknown', 'both_unknown',
]);
const SIDES = new Set<SeatSide>(['buy', 'sell', 'net']);
const PERMISSION_STATES = new Set<PermissionState>([
  'authorized', 'revoked', 'unavailable',
]);
const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/;
const CONTROL_CHARACTER = /\p{Cc}/u;
const REASON_CONTENT = /[^\p{White_Space}\p{Cc}]/u;
const MAX_TEXT_CODEPOINTS = 4096;
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const TIMESTAMP = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(Z|([+-])(\d{2}):(\d{2}))$/;
const JSON_DECIMAL = /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$/;

type RecordValue = Record<string, unknown>;

function fail(field: string): never {
  throw new Error(`Invalid lhb ${field}`);
}

function record(value: unknown, field: string): RecordValue {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) fail(field);
  return value as RecordValue;
}

function exact(value: RecordValue, keys: readonly string[], field: string): void {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  if (actual.length !== expected.length
    || actual.some((key, index) => key !== expected[index])) {
    fail(`${field} keys`);
  }
}

function identifier(value: unknown, field: string): string {
  if (typeof value !== 'string' || !IDENTIFIER.test(value)) fail(field);
  return value;
}

function text(value: unknown, field: string): string {
  if (typeof value !== 'string'
    || [...value].length > MAX_TEXT_CODEPOINTS
    || CONTROL_CHARACTER.test(value)
    || !REASON_CONTENT.test(value)) {
    fail(field);
  }
  return value;
}

function nullableText(value: unknown, field: string): string | null {
  return value === null ? null : text(value, field);
}

function validDecimalToken(token: string): boolean {
  return JSON_DECIMAL.test(token);
}

function finite(value: unknown, field: string, nullable = false): string | null {
  if (nullable && value === null) return null;
  const token = typeof value === 'number'
    ? String(value)
    : typeof value === 'string'
      ? value
      : null;
  if (token === null || !validDecimalToken(token)) fail(field);
  return token;
}

function nonnegative(value: unknown, field: string, nullable = false): string | null {
  const result = finite(value, field, nullable);
  if (result !== null && result.startsWith('-') && Number(result) !== 0) fail(field);
  return result;
}

function integer(value: unknown, field: string, minimum = 0): number {
  const parsed = typeof value === 'string' ? Number(value) : value;
  if (!Number.isSafeInteger(parsed) || (parsed as number) < minimum) fail(field);
  return parsed as number;
}

function dateValue(value: unknown, field: string): string {
  if (typeof value !== 'string' || !DATE.test(value)) fail(field);
  const parsed = new Date(`${value}T00:00:00Z`);
  if (!Number.isFinite(parsed.valueOf()) || parsed.toISOString().slice(0, 10) !== value) {
    fail(field);
  }
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

function validateAsOfSelection(asOf: string, selected: string | undefined): void {
  if (selected
    && epochMicroseconds(timestamp(selected, 'selected as_of'), 'selected as_of')
      !== epochMicroseconds(asOf, 'as_of')) {
    fail('as_of selection');
  }
}

function notAfter(value: string, upper: string, field: string): void {
  if (epochMicroseconds(value, field) > epochMicroseconds(upper, 'as_of')) fail(field);
}

function query(entries: [string, string | undefined][]): string {
  return new URLSearchParams(
    entries.filter((entry): entry is [string, string] => entry[1] !== undefined),
  ).toString();
}

export function disclosurePath(selection: DisclosureSelection): string {
  return `/api/lhb?${query([
    ['date', selection.date],
    ['security_id', selection.securityId],
    ['page', String(selection.page)],
    ['size', String(selection.size)],
    ['as_of', selection.asOf],
  ])}`;
}

export function seatHistoryPath(selection: SeatHistorySelection): string {
  return `/api/lhb/seats/${encodeURIComponent(selection.seatId)}?${query([
    ['date', selection.date],
    ['days', String(selection.days)],
    ['as_of', selection.asOf],
  ])}`;
}

function validateCoverage(value: unknown): LhbCoverage {
  const item = record(value, 'coverage');
  exact(item, COVERAGE_KEYS, 'coverage');
  const result: LhbCoverage = {
    total_disclosures: integer(item.total_disclosures, 'coverage total'),
    visible_disclosures: integer(item.visible_disclosures, 'coverage visible'),
    late_disclosures: integer(item.late_disclosures, 'coverage late'),
    selected_disclosures: integer(item.selected_disclosures, 'coverage selected'),
    seat_count: integer(item.seat_count, 'coverage seat count'),
    complete_seats: integer(item.complete_seats, 'coverage complete seats'),
    unknown_seats: integer(item.unknown_seats, 'coverage unknown seats'),
  };
  if (result.selected_disclosures > result.visible_disclosures
    || result.visible_disclosures > result.total_disclosures) {
    fail('coverage counts');
  }
  if (result.late_disclosures !== result.total_disclosures - result.visible_disclosures) {
    fail('coverage late');
  }
  if (result.seat_count !== result.complete_seats + result.unknown_seats) {
    fail('coverage seats');
  }
  return result;
}

function validateBasis(value: unknown): LhbBasis {
  const item = record(value, 'basis');
  exact(item, BASIS_KEYS, 'basis');
  return {
    unit: nullableText(item.unit, 'basis unit'),
    currency: nullableText(item.currency, 'basis currency'),
  };
}

interface BaseEnvelope {
  date: string;
  as_of: string;
  source: string | null;
  source_version: string | null;
  quality_flags: string[];
  basis: LhbBasis;
  coverage: LhbCoverage;
  data: unknown;
}

function validateEnvelope(value: unknown, selectedDate: string): BaseEnvelope {
  const item = record(value, 'envelope');
  exact(item, ENVELOPE_KEYS, 'envelope');
  const tradeDate = dateValue(item.date, 'envelope date');
  if (tradeDate !== selectedDate) fail('date selection');
  const flags = item.quality_flags;
  if (!Array.isArray(flags)
    || flags.length > 1
    || flags.some(flag => typeof flag !== 'string' || flag.trim() === '' || flag !== 'no_data')) {
    fail('quality flags');
  }
  return {
    date: tradeDate,
    as_of: timestamp(item.as_of, 'as_of'),
    source: item.source === null ? null : identifier(item.source, 'source'),
    source_version: item.source_version === null
      ? null
      : identifier(item.source_version, 'source version'),
    quality_flags: [...flags],
    basis: validateBasis(item.basis),
    coverage: validateCoverage(item.coverage),
    data: item.data,
  };
}

function validateAmountCompleteness(
  buy: string | null,
  sell: string | null,
  net: string | null,
  completeness: unknown,
  field: string,
): SeatCompleteness {
  if (typeof completeness !== 'string' || !COMPLETENESSES.has(completeness as SeatCompleteness)) {
    fail(`${field} completeness`);
  }
  const state = completeness as SeatCompleteness;
  const buyUnknown = state === 'buy_unknown' || state === 'both_unknown';
  const sellUnknown = state === 'sell_unknown' || state === 'both_unknown';
  if ((buy === null) !== buyUnknown || (sell === null) !== sellUnknown) {
    fail(`${field} completeness`);
  }
  // Unknown amounts stay null: null never collapses into a numeric zero.
  if ((net === null) !== (buy === null || sell === null)) fail(`${field} net`);
  return state;
}

function validateSeat(value: unknown): DisclosureSeat {
  const item = record(value, 'seat');
  exact(item, SEAT_KEYS, 'seat');
  const buy = nonnegative(item.buy_amount, 'seat buy amount', true);
  const sell = nonnegative(item.sell_amount, 'seat sell amount', true);
  const net = finite(item.net_amount, 'seat net amount', true);
  const completeness = validateAmountCompleteness(buy, sell, net, item.completeness, 'seat');
  if (item.side !== null && (typeof item.side !== 'string' || !SIDES.has(item.side as SeatSide))) {
    fail('seat side');
  }
  return {
    source_row_id: identifier(item.source_row_id, 'seat source row'),
    seat_id: identifier(item.seat_id, 'seat id'),
    source_seat_name: text(item.source_seat_name, 'seat name'),
    mapping_version: identifier(item.mapping_version, 'seat mapping version'),
    side: item.side as SeatSide | null,
    rank: item.rank === null ? null : integer(item.rank, 'seat rank', 1),
    buy_amount: buy,
    sell_amount: sell,
    currency: text(item.currency, 'seat currency'),
    unit: text(item.unit, 'seat unit'),
    completeness,
    net_amount: net,
  };
}

function validateDisclosureItem(value: unknown): DisclosureItem {
  const item = record(value, 'disclosure item');
  exact(item, ITEM_KEYS, 'disclosure item');
  const eventAt = timestamp(item.source_event_at, 'item source timestamp');
  const receivedAt = timestamp(item.received_at, 'item received timestamp');
  const availableAt = timestamp(item.available_at, 'item available timestamp');
  if (epochMicroseconds(receivedAt, 'item received timestamp')
      < epochMicroseconds(eventAt, 'item source timestamp')
    || epochMicroseconds(availableAt, 'item available timestamp')
      < epochMicroseconds(receivedAt, 'item received timestamp')) {
    fail('item timestamp order');
  }
  if (!Array.isArray(item.seats) || item.seats.length === 0) fail('item seats');
  const seats = item.seats.map(validateSeat);
  if (new Set(seats.map(seat => seat.seat_id)).size !== seats.length) {
    fail('item duplicate seat');
  }
  return {
    disclosure_id: identifier(item.disclosure_id, 'item disclosure id'),
    security_id: identifier(item.security_id, 'item security'),
    disclosure_date: dateValue(item.disclosure_date, 'item disclosure date'),
    reason: text(item.reason, 'item reason'),
    revision: integer(item.revision, 'item revision', 1),
    source_id: identifier(item.source_id, 'item source'),
    source_version: identifier(item.source_version, 'item source version'),
    source_event_at: eventAt,
    received_at: receivedAt,
    available_at: availableAt,
    seats,
  };
}

export function validateDisclosureList(
  value: unknown,
  selection: DisclosureSelection,
): DisclosureListResponse {
  const base = validateEnvelope(value, selection.date);
  validateAsOfSelection(base.as_of, selection.asOf);
  const dataValue = record(base.data, 'disclosure data');
  exact(dataValue, DISCLOSURE_DATA_KEYS, 'disclosure data');
  const rawItems = dataValue.items;
  if (!Array.isArray(rawItems)) fail('disclosure items');
  const items = rawItems.map(validateDisclosureItem);
  const total = integer(dataValue.total, 'total', 0);
  const page = integer(dataValue.page, 'page', 1);
  const size = integer(dataValue.size, 'size', 1);
  if (page !== selection.page) fail('page echo');
  if (size !== selection.size) fail('size echo');
  if (total < items.length) fail('total');
  const empty = total === 0;
  if (empty !== base.quality_flags.includes('no_data')) fail('quality flags');
  const coverage = base.coverage;
  if (coverage.selected_disclosures !== coverage.visible_disclosures) {
    fail('coverage selected');
  }
  if (total !== coverage.visible_disclosures) fail('coverage total');

  const currencies = new Set<string>();
  const units = new Set<string>();
  for (const item of items) {
    if (item.disclosure_date !== base.date) fail('item date selection');
    if (item.security_id !== selection.securityId) fail('item security selection');
    if (base.source && item.source_id !== base.source) fail('item source identity');
    if (base.source_version && item.source_version !== base.source_version) {
      fail('item source version');
    }
    notAfter(item.source_event_at, base.as_of, 'item source timestamp');
    notAfter(item.received_at, base.as_of, 'item received timestamp');
    notAfter(item.available_at, base.as_of, 'item available timestamp');
    for (const seat of item.seats) {
      currencies.add(seat.currency);
      units.add(seat.unit);
    }
  }

  // Page-level seat aggregation mirrors the backend accumulation: a seat is
  // complete only when every one of its rows has both amounts. Coverage is
  // computed over ALL selected disclosures, while a response only carries the
  // current page, so bounds stay subset-safe until the page provably contains
  // every selected disclosure (items.length === total), at which point the
  // page aggregation must equal the reported coverage exactly.
  const pageSeats = new Map<string, { rows: number; completeRows: number }>();
  let pageSeatRows = 0;
  for (const item of items) {
    for (const seat of item.seats) {
      pageSeatRows += 1;
      const bucket = pageSeats.get(seat.seat_id) ?? { rows: 0, completeRows: 0 };
      bucket.rows += 1;
      if (seat.completeness === 'complete') bucket.completeRows += 1;
      pageSeats.set(seat.seat_id, bucket);
      currencies.add(seat.currency);
      units.add(seat.unit);
    }
  }
  let pageComplete = 0;
  for (const bucket of pageSeats.values()) {
    if (bucket.completeRows === bucket.rows) pageComplete += 1;
  }
  const pagePartial = pageSeats.size - pageComplete;
  if (pageSeats.size > coverage.seat_count || pagePartial > coverage.unknown_seats) {
    fail('coverage seats');
  }
  if (items.length === total) {
    if (coverage.seat_count !== pageSeats.size
      || coverage.complete_seats !== pageComplete
      || coverage.unknown_seats !== pagePartial) {
      fail('coverage seats');
    }
  }

  // Basis is merged over the current page rows only. An out-of-range page has
  // no rows to bind, so it neither requires nor contradicts an envelope basis.
  if (pageSeatRows > 0) {
    const expectedBasis: LhbBasis = {
      unit: units.size === 1 ? [...units][0] : null,
      currency: currencies.size === 1 ? [...currencies][0] : null,
    };
    if (base.basis.unit !== expectedBasis.unit
      || base.basis.currency !== expectedBasis.currency) {
      fail('basis');
    }
  }
  if (empty) {
    if (items.length !== 0 || base.source !== null || base.source_version !== null
      || base.basis.unit !== null || base.basis.currency !== null
      || coverage.seat_count !== 0 || coverage.complete_seats !== 0
      || coverage.unknown_seats !== 0
      || coverage.total_disclosures !== 0 || coverage.visible_disclosures !== 0
      || coverage.late_disclosures !== 0 || coverage.selected_disclosures !== 0) {
      fail('empty window');
    }
  }

  const data: DisclosureData = { items, total, page, size };
  const envelope: LhbEnvelope<DisclosureData> = { ...base, data };
  return envelope as DisclosureListResponse;
}

function validateContribution(value: unknown): SeatContribution {
  const item = record(value, 'contribution');
  exact(item, CONTRIBUTION_KEYS, 'contribution');
  const permission = item.permission_state;
  if (typeof permission !== 'string'
    || !PERMISSION_STATES.has(permission as PermissionState)) {
    fail('contribution permission state');
  }
  return {
    disclosure_id: identifier(item.disclosure_id, 'contribution disclosure id'),
    security_id: identifier(item.security_id, 'contribution security'),
    revision: integer(item.revision, 'contribution revision', 1),
    disclosure_date: dateValue(item.disclosure_date, 'contribution date'),
    reason: text(item.reason, 'contribution reason'),
    source_row_id: identifier(item.source_row_id, 'contribution source row'),
    source_seat_name: text(item.source_seat_name, 'contribution seat name'),
    mapping_version: identifier(item.mapping_version, 'contribution mapping version'),
    permission_state: permission as PermissionState,
  };
}

function validateAggregatedSeat(value: unknown): AggregatedSeat {
  const item = record(value, 'aggregated seat');
  exact(item, AGGREGATED_KEYS, 'aggregated seat');
  const buy = nonnegative(item.buy_amount, 'aggregated buy amount', true);
  const sell = nonnegative(item.sell_amount, 'aggregated sell amount', true);
  const net = finite(item.net_amount, 'aggregated net amount', true);
  const completeness = validateAmountCompleteness(
    buy,
    sell,
    net,
    item.completeness,
    'aggregated seat',
  );
  if (!Array.isArray(item.contributions)) fail('aggregated contributions');
  return {
    seat_id: identifier(item.seat_id, 'aggregated seat id'),
    source_id: identifier(item.source_id, 'aggregated source'),
    source_version: identifier(item.source_version, 'aggregated source version'),
    mapping_version: identifier(item.mapping_version, 'aggregated mapping version'),
    source_seat_name: text(item.source_seat_name, 'aggregated seat name'),
    currency: text(item.currency, 'aggregated currency'),
    unit: text(item.unit, 'aggregated unit'),
    buy_amount: buy,
    sell_amount: sell,
    net_amount: net,
    completeness,
    contributions: item.contributions.map(validateContribution),
  };
}

function validateSeatIdentity(value: unknown): SeatIdentity {
  const item = record(value, 'seat identity');
  exact(item, SEAT_IDENTITY_KEYS, 'seat identity');
  return {
    source_id: identifier(item.source_id, 'identity source'),
    seat_id: identifier(item.seat_id, 'identity seat id'),
    source_seat_name: text(item.source_seat_name, 'identity seat name'),
    mapping_version: identifier(item.mapping_version, 'identity mapping version'),
  };
}

export function validateSeatHistory(
  value: unknown,
  selection: SeatHistorySelection,
): SeatHistoryResponse {
  const base = validateEnvelope(value, selection.date);
  validateAsOfSelection(base.as_of, selection.asOf);
  const dataValue = record(base.data, 'seat history data');
  exact(dataValue, HISTORY_DATA_KEYS, 'seat history data');
  if (typeof dataValue.identity_state !== 'string'
    || (dataValue.identity_state !== 'known' && dataValue.identity_state !== 'unknown')) {
    fail('identity state');
  }
  const identityState = dataValue.identity_state as IdentityState;
  const seat = validateSeatIdentity(dataValue.seat);
  if (seat.seat_id !== selection.seatId) fail('identity seat selection');
  const rawItems = dataValue.items;
  if (!Array.isArray(rawItems)) fail('seat history items');
  const items = rawItems.map(validateAggregatedSeat);
  const total = integer(dataValue.total, 'total', 0);
  if (total !== items.length) fail('total');
  const empty = total === 0;
  if (empty !== base.quality_flags.includes('no_data')) fail('quality flags');
  const coverage = base.coverage;

  if (identityState === 'unknown') {
    // Identity is decided by identity_state alone; a literal mapping version
    // can never be used to guess a real source.
    if (seat.source_id !== selection.seatId
      || seat.source_seat_name !== selection.seatId
      || seat.mapping_version !== 'unknown') {
      fail('identity synthetic header');
    }
  }

  const currencies = new Set<string>();
  const units = new Set<string>();
  for (const item of items) {
    if (item.seat_id !== selection.seatId) fail('aggregated seat selection');
    if (base.source && item.source_id !== base.source) fail('aggregated source identity');
    if (base.source_version && item.source_version !== base.source_version) {
      fail('aggregated source version');
    }
    currencies.add(item.currency);
    units.add(item.unit);
  }
  if (base.source && seat.source_id !== base.source) fail('identity source identity');
  if (base.source_version && items.length > 0) {
    const versions = new Set(items.map(item => item.source_version));
    if (!versions.has(base.source_version)) fail('identity source version');
  }

  if (empty) {
    if (coverage.selected_disclosures !== 0 || coverage.seat_count !== 0
      || coverage.complete_seats !== 0 || coverage.unknown_seats !== 0) {
      fail('empty window coverage');
    }
    if (base.source !== null || base.source_version !== null
      || base.basis.unit !== null || base.basis.currency !== null) {
      fail('empty window provenance');
    }
  } else {
    const completeSeats = items.filter(item => item.completeness === 'complete').length;
    if (coverage.seat_count !== items.length
      || coverage.complete_seats !== completeSeats
      || coverage.unknown_seats !== items.length - completeSeats) {
      fail('coverage seats');
    }
    if (coverage.selected_disclosures < items.length) fail('coverage selected');
    const expectedBasis: LhbBasis = {
      unit: units.size === 1 ? [...units][0] : null,
      currency: currencies.size === 1 ? [...currencies][0] : null,
    };
    if (base.basis.unit !== expectedBasis.unit
      || base.basis.currency !== expectedBasis.currency) {
      fail('basis');
    }
  }

  const data: SeatHistoryData = { seat, identity_state: identityState, items, total };
  const envelope: LhbEnvelope<SeatHistoryData> = { ...base, data };
  return envelope as SeatHistoryResponse;
}

// Display-only Decimal formatting, copied from the realtime panel so the LHB
// feature never edits the shared format module. Null means "unknown" and a
// reported zero always renders as "0".
const MAX_ABSOLUTE_EXPONENT = 1_000_000;
const MAX_PLAIN_EXPONENT = 40;
const MAX_PLAIN_SIGNIFICANT_DIGITS = 48;
const MAX_DISPLAY_LENGTH = 80;
const DECIMAL_TEXT = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e([+-]?\d+))?$/i;

export function formatLhbDecimal(value: string | null): string {
  if (typeof value !== 'string') return '未知';
  const match = DECIMAL_TEXT.exec(value);
  const exponent = match?.[1];
  if (!match || (exponent !== undefined
    && (exponent.replace(/^[+-]/, '').length > 7
      || Math.abs(Number(exponent)) > MAX_ABSOLUTE_EXPONENT))) {
    return '未知';
  }
  try {
    const number = new Decimal(value);
    if (!number.isFinite() || Math.abs(number.e) > MAX_ABSOLUTE_EXPONENT) return '未知';
    if (number.isZero()) return '0';
    if (Math.abs(number.e) > MAX_PLAIN_EXPONENT
      || number.sd() > MAX_PLAIN_SIGNIFICANT_DIGITS) {
      return number.toSignificantDigits(12).toExponential();
    }
    const [whole, fraction] = number.toFixed().split('.');
    const displayed = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
      + (fraction ? `.${fraction}` : '');
    return displayed.length <= MAX_DISPLAY_LENGTH
      ? displayed
      : number.toSignificantDigits(12).toExponential();
  } catch {
    return '未知';
  }
}

/**
 * Panel-local resource hook for the read-only LHB endpoints. It mirrors the
 * realtime generation gate and abort handling but never touches the review
 * session, the shared access provider, or global revocation events: a 401/403
 * stays a local error and never clears other panels on the page.
 */
export function useLhbResource<T>(
  path: string | null,
  refreshToken = 0,
): Resource<T> {
  const [count, setCount] = useState(0);
  const [result, setResult] = useState<{
    key: string | null;
    data?: T;
    error?: ApiError;
    loading: boolean;
  }>({ key: null, loading: false });
  const gate = useRef(requestGeneration());
  const key = path;

  useEffect(() => {
    const generation = gate.current.next();
    const controller = new AbortController();
    if (!key || !path) {
      setResult({ key: null, loading: false });
      return;
    }
    setResult(previous => ({
      key,
      data: previous.key === key ? previous.data : undefined,
      error: previous.key === key ? previous.error : undefined,
      loading: true,
    }));
    Promise.resolve().then(() => {
      if (controller.signal.aborted) throw new DOMException('请求已取消', 'AbortError');
      return requestLhb<T>(path, { signal: controller.signal });
    }).then(data => {
      if (gate.current.isCurrent(generation)) setResult({ key, data, loading: false });
    }).catch((error: unknown) => {
      if (!gate.current.isCurrent(generation)
        || (error instanceof DOMException && error.name === 'AbortError')) return;
      const failure = error instanceof ApiError ? error : new ApiError(0);
      const retain = failure.code !== 'malformed_response'
        && (failure.status === 0 || failure.status === 408 || failure.status >= 500);
      setResult(previous => ({
        key,
        data: retain && previous.key === key ? previous.data : undefined,
        error: failure,
        loading: false,
      }));
    });
    return () => {
      gate.current.next();
      controller.abort();
    };
  }, [path, key, count, refreshToken]);

  const current = result.key === key && key !== null;
  return {
    data: current ? result.data : undefined,
    error: current ? result.error : undefined,
    loading: Boolean(key) && (!current || result.loading),
    refresh: () => setCount(value => value + 1),
  };
}

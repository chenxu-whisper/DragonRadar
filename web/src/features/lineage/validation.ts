import {
  CLOCK_NAMES, DAILY_DOMAINS, type ClockCell, type Clocks, type ClockReason,
  type DailyLineage, type DomainLineage, type Lineage, type LineageSelection,
  type SourceLineage,
} from './types';

function fail(): never { throw new Error('Invalid or mismatched clock lineage'); }
function object(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return fail();
  return value as Record<string, unknown>;
}
function exact(value: unknown, keys: readonly string[]): Record<string, unknown> {
  const result = object(value);
  if (Object.keys(result).length !== keys.length || keys.some(key => !Object.hasOwn(result, key))) fail();
  return result;
}
export function timestampMicros(value: unknown): bigint {
  if (typeof value !== 'string') return fail();
  const match = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})(?:\.(\d{1,6}))?(Z|[+-]\d{2}:\d{2})$/.exec(value);
  if (!match) return fail();
  const calendar = new Date(`${match[1]}T${match[2]}Z`);
  if (!Number.isFinite(calendar.getTime()) || calendar.toISOString().slice(0, 19) !== `${match[1]}T${match[2]}`) fail();
  if (match[4] !== 'Z' && (Number(match[4].slice(1, 3)) > 23 || Number(match[4].slice(4)) > 59)) fail();
  const milliseconds = Date.parse(`${match[1]}T${match[2]}${match[4]}`);
  if (!Number.isFinite(milliseconds)) return fail();
  return BigInt(milliseconds) * 1000n + BigInt((match[3] ?? '').padEnd(6, '0'));
}
function nullableTime(value: unknown): string | null {
  if (value === null) return null;
  timestampMicros(value); return value as string;
}
const reasons: readonly ClockReason[] = ['legacy_clock_unavailable', 'input_clock_unavailable',
  'domain_missing', 'domain_disabled', 'browser_clock', 'domain_clock_not_applicable'];
function cell(value: unknown): ClockCell {
  const item = exact(value, ['state', 'at', 'first_at', 'last_at', 'reason']);
  const at = nullableTime(item.at), first = nullableTime(item.first_at), last = nullableTime(item.last_at);
  if ((first === null) !== (last === null)) fail();
  if (first !== null && timestampMicros(first) > timestampMicros(last)) fail();
  const state = item.state;
  if (!['known', 'mixed', 'unknown', 'not_applicable'].includes(state as string)) fail();
  if (state === 'known') {
    if (at === null || first === null || last === null ||
      timestampMicros(at) !== timestampMicros(first) || timestampMicros(at) !== timestampMicros(last)) fail();
  } else if (at !== null) fail();
  if (state === 'mixed' && (first === null || timestampMicros(first) === timestampMicros(last))) fail();
  if (state === 'not_applicable' && first !== null) fail();
  if (state === 'known' || state === 'mixed') { if (item.reason !== null) fail(); }
  else if (!reasons.includes(item.reason as ClockReason)) fail();
  return Object.freeze({ state, at, first_at: first, last_at: last, reason: item.reason }) as ClockCell;
}
function parseClocks(value: unknown, daily: boolean): Clocks {
  const raw = exact(value, CLOCK_NAMES);
  const result = Object.fromEntries(CLOCK_NAMES.map(name => [name, cell(raw[name])])) as Record<typeof CLOCK_NAMES[number], ClockCell>;
  if (result.page_updated_at.state !== 'not_applicable' || result.page_updated_at.reason !== 'browser_clock') fail();
  if (daily ? result.snapshot_published_at.state !== 'known' :
    result.snapshot_published_at.state !== 'not_applicable' ||
      result.snapshot_published_at.reason !== 'domain_clock_not_applicable') fail();
  for (const [start, end] of [
    ['provider_published_at', 'first_received_at'],
    ['first_received_at', 'computed_at'], ['computed_at', 'snapshot_published_at'],
  ] as const) {
    const left = result[start], right = result[end];
    if (left.last_at !== null && right.first_at !== null &&
      timestampMicros(left.last_at) > timestampMicros(right.first_at)) {
      // Aggregate provider/receipt ranges are not paired supplier observations.
      if (start !== 'provider_published_at' || left.state === 'known' && right.state === 'known') fail();
    }
  }
  return Object.freeze(result);
}
export function validateSelection(selection: LineageSelection): void {
  const identifier = (value: unknown) => {
    if (typeof value !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/.test(value)) fail();
  };
  if (selection.type === 'daily') {
    exact(selection, ['type', 'snapshot_id', 'trade_date']);
    if (!/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(selection.snapshot_id)) fail();
    if (!/^\d{4}-\d{2}-\d{2}$/.test(selection.trade_date)) fail();
    timestampMicros(`${selection.trade_date}T00:00:00Z`);
  } else {
    identifier(selection.source_id); identifier(selection.source_version); timestampMicros(selection.as_of);
    if (selection.type === 'realtime') {
      exact(selection, ['type', 'source_id', 'source_version', 'kind', 'batch_id', 'as_of']);
      identifier(selection.batch_id);
      if (!['quotes', 'minutes', 'sector_flow', 'anomalies'].includes(selection.kind)) fail();
    } else if (selection.type === 'lhb') {
      exact(selection, ['type', 'source_id', 'source_version', 'disclosure_id', 'revision', 'as_of']);
      identifier(selection.disclosure_id);
      if (!Number.isInteger(selection.revision) || selection.revision < 1 || selection.revision > 2147483647) fail();
    } else fail();
  }
}
export function selectionKey(selection: LineageSelection): string {
  validateSelection(selection);
  return JSON.stringify(Object.entries(selection).sort(([a], [b]) => a.localeCompare(b)));
}
export function parseLineage(value: unknown, selection: LineageSelection): Lineage {
  validateSelection(selection);
  if (selection.type === 'daily') {
    const raw = exact(value, ['schema', 'snapshot_id', 'trade_date', 'domains']);
    if (raw.schema !== 'dragon-radar.publication-clocks/v1' ||
      raw.snapshot_id !== selection.snapshot_id || raw.trade_date !== selection.trade_date) fail();
    const rawDomains = exact(raw.domains, DAILY_DOMAINS);
    const domains = Object.fromEntries(DAILY_DOMAINS.map(name => {
      const domain = exact(rawDomains[name], ['status', 'input_id', 'as_of', 'clocks']);
      if (!['ready', 'missing', 'disabled'].includes(domain.status as string)) fail();
      if (domain.input_id !== null && (typeof domain.input_id !== 'string' ||
        !/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(domain.input_id))) fail();
      if (domain.status === 'ready' && domain.input_id === null) fail();
      const clocks = parseClocks(domain.clocks, true);
      if (domain.status !== 'ready' && clocks.computed_at.state !== 'unknown') fail();
      return [name, Object.freeze({ ...domain, as_of: nullableTime(domain.as_of), clocks })];
    })) as unknown as Record<typeof DAILY_DOMAINS[number], DomainLineage>;
    const publications = DAILY_DOMAINS.map(name => timestampMicros(domains[name].clocks.snapshot_published_at.at));
    if (publications.some(at => at !== publications[0])) fail();
    return Object.freeze({ ...raw, domains: Object.freeze(domains) }) as unknown as DailyLineage;
  }
  const raw = exact(value, ['schema', 'identity', 'as_of', 'clocks']);
  if (raw.schema !== 'dragon-radar.source-clocks/v1' ||
    timestampMicros(raw.as_of) !== timestampMicros(selection.as_of)) fail();
  const keys = selection.type === 'realtime' ? ['source_id', 'source_version', 'kind', 'batch_id'] :
    ['source_id', 'source_version', 'disclosure_id', 'revision'];
  const identity = exact(raw.identity, keys);
  const selected = selection as unknown as Record<string, unknown>;
  if (keys.some(key => identity[key] !== selected[key])) fail();
  return Object.freeze({ ...raw, identity: Object.freeze({ ...identity }), clocks: parseClocks(raw.clocks, false) }) as unknown as SourceLineage;
}
export function clockDelay(start: ClockCell, end: ClockCell, allowMixedReceipt = false): {
  milliseconds: number | null; reason: 'clock_unavailable' | 'mixed_clock' | 'negative_delay' | null;
} {
  if (!['known', 'mixed'].includes(start.state) || end.state !== 'known') return { milliseconds: null, reason: 'clock_unavailable' };
  if (start.state === 'mixed' && !allowMixedReceipt) return { milliseconds: null, reason: 'mixed_clock' };
  const delta = timestampMicros(end.at) - timestampMicros(start.last_at);
  return delta < 0n ? { milliseconds: null, reason: 'negative_delay' } :
    { milliseconds: Number(delta / 1000n), reason: null };
}
export function formatTimestamp(value: string): string {
  const micros = timestampMicros(value);
  const shifted = new Date(Number(micros / 1000n) + 8 * 3600 * 1000).toISOString();
  const fraction = ((micros % 1000000n + 1000000n) % 1000000n).toString().padStart(6, '0');
  return `${shifted.slice(0, 10)} ${shifted.slice(11, 19)}.${fraction} UTC+08:00`;
}
export function formatClock(value: ClockCell): string {
  if (value.state === 'known') return formatTimestamp(value.at!);
  const range = value.first_at === null ? '' :
    `${formatTimestamp(value.first_at)} to ${formatTimestamp(value.last_at!)}`;
  if (value.state === 'mixed') return range;
  return `${value.state === 'unknown' ? 'Unknown' : 'Not applicable'} (${value.reason})` +
    (range ? `; known subset: ${range}` : '');
}

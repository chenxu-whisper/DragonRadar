import { describe, expect, it } from 'vitest';
import {
  anomalyPath,
  minutePath,
  quotePath,
  sectorFlowPath,
  validateAnomalyResponse,
  validateMinuteResponse,
  validateQuoteResponse,
  validateSectorFlowResponse,
} from '../../src/features/realtime/client';

const date = '2026-10-09';
const source = 'licensed-feed';
const version = 'v1';
const batch = 'batch-1';
const security = '600000.SH';
const coverage = { expected: 2, valid: 2, excluded: 0, missing: 0 };
const basis = {
  unit: null,
  currency: null,
  price_type: 'raw',
  benchmark: null,
};

function envelope(data: unknown, changes: Record<string, unknown> = {}) {
  return {
    trade_date: date,
    as_of: '2026-10-09T02:00:00Z',
    snapshot_id: null,
    source,
    source_version: version,
    coverage,
    quality_flags: [],
    rule_version: null,
    basis,
    data,
    ...changes,
  };
}

function quoteResponse(changes: Record<string, unknown> = {}) {
  return envelope({
    quote_batch_id: batch,
    source_event_at: '2026-10-09T01:30:00Z',
    received_at: '2026-10-09T01:30:02Z',
    delay_ms: 2000,
    status: 'delayed',
    quotes: [{
      security_id: security,
      last: 10.125,
      bid: null,
      ask: null,
      bid_size: null,
      ask_size: null,
      volume: 0,
      amount: 9007199254740.125,
      order_book_status: 'unavailable',
      order_book_unavailable_reason: 'field_not_authorized',
    }],
    ...changes,
  }, {
    trade_date: null,
  });
}

function minuteResponse(
  bars: Record<string, unknown>[] = [
    minuteBar('2026-10-09T01:30:00Z', 10),
    minuteBar('2026-10-09T01:32:00Z', 11),
  ],
  changes: Record<string, unknown> = {},
) {
  return envelope({
    status: 'incomplete',
    bars,
    ...changes,
  }, {
    coverage: { expected: 3, valid: 2, excluded: 0, missing: 1 },
    quality_flags: ['stream_gap'],
  });
}

function minuteBar(eventAt: string, close: number) {
  return {
    batch_id: batch,
    source_id: source,
    source_version: version,
    security_id: security,
    event_at: eventAt,
    received_at: eventAt,
    available_at: eventAt,
    open: close,
    high: close,
    low: close,
    close,
    volume: 0,
    amount: 0,
    revision: 1,
    final: true,
  };
}

function anomalyResponse(changes: Record<string, unknown> = {}) {
  return envelope({
    status: 'complete',
    events: [{
      event_id: 'event-1',
      batch_id: batch,
      security_id: security,
      rule_version: 'return-v1',
      event_at: '2026-10-09T01:32:00Z',
      available_at: '2026-10-09T01:32:02Z',
      metric: 'window_return',
      window: 2,
      value: 0.03,
      threshold: 0.02,
      quality: 'complete',
    }],
    next_cursor: 'payload.signature',
    ...changes,
  }, {
    coverage: { expected: 1, valid: 1, excluded: 0, missing: 0 },
    source_version: null,
  });
}

function flowResponse(
  netInflow: number | null = null,
  unavailableReason: string | null = 'field_not_supplied',
) {
  return envelope({
    status: 'complete',
    points: [{
      batch_id: batch,
      source_id: source,
      source_version: version,
      sector: 'sector-1',
      event_at: '2026-10-09T01:30:00Z',
      available_at: '2026-10-09T01:30:02Z',
      net_inflow: netInflow,
      unit: 'CNY',
      unavailable_reason: unavailableReason,
    }],
  }, {
    basis: { ...basis, unit: 'CNY', currency: 'CNY' },
    coverage: { expected: 1, valid: 1, excluded: 0, missing: 0 },
  });
}

describe('realtime public GET path builders', () => {
  it('binds quote selection without adding write or subscription parameters', () => {
    expect(quotePath({
      securityIds: ['600000.SH', '000001.SZ'],
      asOf: '2026-10-09T02:00:00Z',
      sourceId: source,
      batchId: batch,
    })).toBe(
      '/api/realtime/quotes?security_ids=600000.SH%2C000001.SZ&as_of=2026-10-09T02%3A00%3A00Z'
      + '&source_id=licensed-feed&quote_batch_id=batch-1',
    );
  });

  it('uses the actual EX05 batch_id contract and exact security/date selection', () => {
    expect(minutePath({
      securityId: security,
      date,
      sourceId: source,
      batchId: batch,
    })).toBe(
      '/api/realtime/minutes?security_id=600000.SH&date=2026-10-09'
      + '&source_id=licensed-feed&batch_id=batch-1',
    );
  });

  it('binds anomaly filters and the opaque cursor', () => {
    expect(anomalyPath({
      date,
      securityId: security,
      sourceId: source,
      cursor: 'opaque.cursor',
      limit: 50,
    })).toBe(
      '/api/realtime/anomalies?date=2026-10-09&source_id=licensed-feed'
      + '&security_id=600000.SH&cursor=opaque.cursor&limit=50',
    );
  });

  it('uses only the public EX07 GET selection', () => {
    expect(sectorFlowPath({
      sectorId: 'sector-1',
      date,
      sourceId: source,
      batchId: batch,
    })).toBe(
      '/api/market/sector-flow/intraday?sector=sector-1&date=2026-10-09'
      + '&source_id=licensed-feed&batch_id=batch-1',
    );
  });
});

describe('realtime strict response validation', () => {
  it('accepts finite decimal wire numbers and keeps zero values', () => {
    const result = validateQuoteResponse(quoteResponse(), {
      securityIds: [security],
      sourceId: source,
      batchId: batch,
    });
    expect(result.data?.quotes[0]).toMatchObject({
      last: 10.125,
      volume: 0,
      amount: 9007199254740.125,
    });
  });

  it.each([
    ['extra envelope key', () => envelope(null, { extra: true })],
    ['missing envelope key', () => {
      const response = envelope(null);
      delete (response as Partial<typeof response>).basis;
      return response;
    }],
    ['extra quote key', () => {
      const response = quoteResponse();
      (response.data as { quotes: Record<string, unknown>[] }).quotes[0].daily_close = 10;
      return response;
    }],
  ])('rejects %s', (_name, fixture) => {
    expect(() => validateQuoteResponse(fixture(), {
      securityIds: [security],
    })).toThrow();
  });

  it('rejects an exact requested batch when the response substitutes another batch', () => {
    expect(() => validateQuoteResponse(
      quoteResponse({ quote_batch_id: 'batch-2' }),
      { securityIds: [security], sourceId: source, batchId: batch },
    )).toThrow(/batch/i);
  });

  it('rejects source and security identity mismatches', () => {
    expect(() => validateQuoteResponse(quoteResponse(), {
      securityIds: ['000001.SZ'],
      sourceId: source,
    })).toThrow(/security/i);
    expect(() => validateQuoteResponse(
      envelope((quoteResponse().data), { trade_date: null, source: 'other-feed' }),
      { securityIds: [security], sourceId: source },
    )).toThrow(/source/i);
  });

  it('rejects a response from a different point-in-time selection', () => {
    expect(() => validateQuoteResponse(quoteResponse(), {
      securityIds: [security],
      asOf: '2026-10-09T02:00:01Z',
    })).toThrow(/as.of/i);
  });

  it.each([
    ['invalid source timestamp', { source_event_at: 'not-a-date' }],
    ['timestamp without timezone', { received_at: '2026-10-09T01:30:02' }],
    ['negative delay', { delay_ms: -1 }],
    ['non-finite decimal', { quotes: [{
      ...(quoteResponse().data as { quotes: Record<string, unknown>[] }).quotes[0],
      last: Number.POSITIVE_INFINITY,
    }] }],
  ])('rejects %s', (_name, changes) => {
    expect(() => validateQuoteResponse(quoteResponse(changes), {
      securityIds: [security],
    })).toThrow();
  });

  it('requires a reason when order book evidence is unavailable', () => {
    const response = quoteResponse();
    (response.data as { quotes: Record<string, unknown>[] })
      .quotes[0].order_book_unavailable_reason = null;
    expect(() => validateQuoteResponse(response, {
      securityIds: [security],
    })).toThrow(/reason/i);
  });

  it('preserves missing minutes as an absent timestamp instead of interpolating', () => {
    const result = validateMinuteResponse(minuteResponse(), {
      securityId: security,
      date,
      sourceId: source,
      batchId: batch,
    });
    expect(result.data?.bars.map(item => item.event_at)).toEqual([
      '2026-10-09T01:30:00Z',
      '2026-10-09T01:32:00Z',
    ]);
    expect(result.coverage).toEqual({
      expected: 3,
      valid: 2,
      excluded: 0,
      missing: 1,
    });
  });

  it('rejects minute batch, source, security, and date selection mismatches', () => {
    const cases = [
      [minuteResponse([{ ...minuteBar('2026-10-09T01:30:00Z', 10), batch_id: 'batch-2' }]), /batch/i],
      [minuteResponse([{ ...minuteBar('2026-10-09T01:30:00Z', 10), source_id: 'other' }]), /source/i],
      [minuteResponse([{ ...minuteBar('2026-10-09T01:30:00Z', 10), security_id: 'other' }]), /security/i],
      [envelope(minuteResponse().data, { trade_date: '2026-10-08' }), /date/i],
    ] as const;
    for (const [response, message] of cases) {
      expect(() => validateMinuteResponse(response, {
        securityId: security,
        date,
        sourceId: source,
        batchId: batch,
      })).toThrow(message);
    }
  });

  it('rejects invalid minute dates, OHLC, revisions, and non-finite numerics', () => {
    const base = minuteBar('2026-10-09T01:30:00Z', 10);
    for (const patch of [
      { event_at: 'invalid' },
      { high: 9 },
      { revision: 0 },
      { amount: Number.NaN },
    ]) {
      expect(() => validateMinuteResponse(minuteResponse([{ ...base, ...patch }]), {
        securityId: security,
        date,
      })).toThrow();
    }
  });

  it('validates anomaly rule evidence and preserves the opaque next cursor', () => {
    const result = validateAnomalyResponse(anomalyResponse(), {
      date,
      sourceId: source,
      securityId: security,
      cursor: 'prior.cursor',
      limit: 50,
    });
    expect(result.data?.events[0]).toMatchObject({
      rule_version: 'return-v1',
      value: 0.03,
      threshold: 0.02,
      batch_id: batch,
      quality: 'complete',
    });
    expect(result.data?.next_cursor).toBe('payload.signature');
  });

  it.each([
    ['invalid cursor type', { next_cursor: 7 }],
    ['blank cursor', { next_cursor: '' }],
    ['unsupported metric', {
      events: [{ ...(anomalyResponse().data as { events: Record<string, unknown>[] }).events[0], metric: 'guess' }],
    }],
    ['non-finite threshold', {
      events: [{ ...(anomalyResponse().data as { events: Record<string, unknown>[] }).events[0], threshold: Number.NaN }],
    }],
  ])('rejects anomaly %s', (_name, changes) => {
    expect(() => validateAnomalyResponse(anomalyResponse(changes), {
      date,
      sourceId: source,
      securityId: security,
    })).toThrow();
  });

  it('rejects anomaly availability before its source event', () => {
    const event = (anomalyResponse().data as { events: Record<string, unknown>[] }).events[0];
    expect(() => validateAnomalyResponse(anomalyResponse({
      events: [{ ...event, available_at: '2026-10-09T01:31:59Z' }],
    }), {
      date,
      sourceId: source,
      securityId: security,
    })).toThrow(/timestamp/i);
  });

  it('keeps unavailable flow null separate from an observed zero', () => {
    const unavailable = validateSectorFlowResponse(flowResponse(), {
      sectorId: 'sector-1',
      date,
      sourceId: source,
      batchId: batch,
    });
    const zero = validateSectorFlowResponse(flowResponse(0, null), {
      sectorId: 'sector-1',
      date,
      sourceId: source,
      batchId: batch,
    });
    expect(unavailable.data?.points[0].net_inflow).toBeNull();
    expect(unavailable.data?.points[0].unavailable_reason).toBe('field_not_supplied');
    expect(zero.data?.points[0].net_inflow).toBe(0);
    expect(zero.data?.points[0].unavailable_reason).toBeNull();
  });

  it('rejects unavailable flow without a reason and observed flow with a reason', () => {
    expect(() => validateSectorFlowResponse(flowResponse(null, null), {
      sectorId: 'sector-1',
      date,
    })).toThrow(/reason/i);
    expect(() => validateSectorFlowResponse(flowResponse(0, 'not_really_unavailable'), {
      sectorId: 'sector-1',
      date,
    })).toThrow(/reason/i);
  });

  it('rejects flow selection and source identity mismatches', () => {
    expect(() => validateSectorFlowResponse(flowResponse(), {
      sectorId: 'sector-2',
      date,
      sourceId: source,
      batchId: batch,
    })).toThrow(/sector/i);
    expect(() => validateSectorFlowResponse(flowResponse(), {
      sectorId: 'sector-1',
      date,
      sourceId: 'other-feed',
      batchId: batch,
    })).toThrow(/source/i);
  });

  it('rejects flow availability before its source event', () => {
    const response = flowResponse();
    const point = (response.data as { points: Record<string, unknown>[] }).points[0];
    point.available_at = '2026-10-09T01:29:59Z';
    expect(() => validateSectorFlowResponse(response, {
      sectorId: 'sector-1',
      date,
    })).toThrow(/timestamp/i);
  });

  it('accepts unavailable null-data envelopes but rejects inconsistent coverage', () => {
    const unavailable = envelope(null, {
      trade_date: null,
      source: null,
      source_version: null,
      coverage: null,
      quality_flags: ['no_data'],
    });
    expect(validateQuoteResponse(unavailable, {
      securityIds: [security],
    }).data).toBeNull();

    expect(() => validateQuoteResponse(envelope(null, {
      trade_date: null,
      coverage,
      quality_flags: [],
    }), {
      securityIds: [security],
    })).toThrow();
  });
});

import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  anomalyPath,
  decimalForChart,
  minutePath,
  quotePath,
  realtimeRequestPaths,
  requestRealtime,
  sectorFlowPath,
  validateAnomalyResponse,
  validateMinuteResponse,
  validateQuoteResponse,
  validateSectorFlowResponse,
} from '../../src/features/realtime/client';
import {
  parseRealtimeJson,
  supportsJsonParseSource,
} from '../../src/features/realtime/json';

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

afterEach(() => vi.unstubAllGlobals());

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
    coverage: { expected: 1, valid: 1, excluded: 0, missing: 0 },
    quality_flags: ['delayed'],
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
    coverage: {
      expected: 3,
      valid: bars.length,
      excluded: 0,
      missing: 3 - bars.length,
    },
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
  const unavailable = netInflow === null;
  return envelope({
    status: unavailable ? 'unavailable' : 'complete',
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
    quality_flags: unavailable && unavailableReason ? [unavailableReason] : [],
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

  it.each(['', '2026-02-30', 'not-a-date'])(
    'does not plan requests for invalid date %j',
    invalidDate => {
      expect(realtimeRequestPaths(invalidDate, [security], 'sector-1')).toEqual({
        quote: null,
        minute: null,
        anomaly: null,
        flow: null,
      });
    },
  );

  it('plans requests only for selections required by each endpoint', () => {
    expect(realtimeRequestPaths(date, [], undefined)).toEqual({
      quote: null,
      minute: null,
      anomaly: null,
      flow: null,
    });
    expect(realtimeRequestPaths(date, [security], undefined)).toMatchObject({
      quote: expect.stringContaining('/api/realtime/quotes?'),
      minute: expect.stringContaining('/api/realtime/minutes?'),
      anomaly: expect.stringContaining('/api/realtime/anomalies?'),
      flow: null,
    });
    expect(realtimeRequestPaths(date, [], 'sector-1')).toMatchObject({
      quote: null,
      minute: null,
      anomaly: null,
      flow: expect.stringContaining('/api/market/sector-flow/intraday?'),
    });
  });
});

describe('realtime strict response validation', () => {
  it('uses native JSON.parse lexical source in the current Node runtime', () => {
    expect(supportsJsonParseSource()).toBe(true);
  });

  it.each([
    ['/api/realtime/quotes', [
      'last', 'bid', 'ask', 'bid_size', 'ask_size', 'volume', 'amount',
    ]],
    ['/api/realtime/minutes', [
      'open', 'high', 'low', 'close', 'volume', 'amount',
    ]],
    ['/api/realtime/anomalies', ['value', 'threshold']],
    ['/api/market/sector-flow/intraday', ['net_inflow']],
  ] as const)('preserves every declared Decimal token for %s', (path, fields) => {
    const pairs = fields.map(field => `"${field}":1.2300`);
    const parsed = parseRealtimeJson(
      `{${[...pairs, '"revision":1'].join(',')}}`,
      path,
    ) as Record<string, unknown>;
    fields.forEach(field => expect(parsed[field]).toBe('1.2300'));
    expect(parsed.revision).toBe(1);
  });

  it('bounds lossy chart conversion without changing exact Decimal storage', () => {
    expect(decimalForChart('10.125')).toBe(10.125);
    expect(decimalForChart('0')).toBe(0);
    expect(decimalForChart('9007199254740993.01')).toBeNull();
    expect(decimalForChart('1e10000')).toBeNull();
    expect(decimalForChart('1e-10000')).toBeNull();
    expect(decimalForChart('Infinity')).toBeNull();
  });

  it.each([
    '0xff',
    '0b101',
    '1_000',
    '+1',
    '01',
    '.1',
    '1.',
    'NaN',
    'Infinity',
    '-Infinity',
  ])('rejects non-RFC8259 Decimal token %j', invalid => {
    const response = quoteResponse();
    (response.data as { quotes: Record<string, unknown>[] }).quotes[0].last = invalid;
    expect(() => validateQuoteResponse(response, {
      securityIds: [security],
    })).toThrow(/last/i);
  });

  it('bounds Decimal coefficient and exponent while accepting scientific notation and negative zero', () => {
    const valid = quoteResponse();
    (valid.data as { quotes: Record<string, unknown>[] }).quotes[0] = {
      ...(valid.data as { quotes: Record<string, unknown>[] }).quotes[0],
      last: '1.25e2',
      volume: '-0',
      amount: '9007199254740993.01',
    };
    expect(validateQuoteResponse(valid, {
      securityIds: [security],
    }).data?.quotes[0]).toMatchObject({
      last: '1.25e2',
      volume: '-0',
      amount: '9007199254740993.01',
    });
    expect(decimalForChart('1.25e2')).toBe(125);

    for (const invalid of [
      '1'.repeat(81),
      '1e10001',
      '1e-10001',
      `${'1'.repeat(80)}e${'9'.repeat(46)}`,
    ]) {
      const response = quoteResponse();
      (response.data as { quotes: Record<string, unknown>[] }).quotes[0].last = invalid;
      expect(() => validateQuoteResponse(response, {
        securityIds: [security],
      })).toThrow(/last/i);
      expect(decimalForChart(invalid)).toBeNull();
    }
  });

  it('keeps a large unquoted Decimal exact from the HTTP wire through validation', async () => {
    const body = JSON.stringify(quoteResponse()).replace(
      '9007199254740.125',
      '9007199254740993.01',
    );
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(body)));

    const raw = await requestRealtime<unknown>(quotePath({ securityIds: [security] }));
    const result = validateQuoteResponse(raw, { securityIds: [security] });

    expect(result.data?.quotes[0].amount).toBe('9007199254740993.01');
  });

  it('fails with DATA_FORMAT when JSON.parse lexical source is unsupported', async () => {
    const nativeJson = JSON;
    vi.stubGlobal('JSON', {
      stringify: nativeJson.stringify.bind(nativeJson),
      parse(
        text: string,
        reviver?: (this: unknown, key: string, value: unknown) => unknown,
      ) {
        return nativeJson.parse(text, function (key, value) {
          return reviver ? reviver.call(this, key, value) : value;
        });
      },
    });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response(nativeJson.stringify(quoteResponse())),
    ));

    await expect(requestRealtime<unknown>(quotePath({ securityIds: [security] })))
      .rejects.toMatchObject({ status: 502, code: 'DATA_FORMAT' });
  });

  it('preserves every quote Decimal field as exact wire text and keeps zero values', () => {
    const response = quoteResponse();
    (response.data as { quotes: Record<string, unknown>[] }).quotes[0] = {
      ...(response.data as { quotes: Record<string, unknown>[] }).quotes[0],
      last: '9007199254740993.01',
      bid: '9007199254740993.02',
      ask: '9007199254740993.03',
      bid_size: '9007199254740993.04',
      ask_size: '9007199254740993.05',
      volume: '0',
      amount: '9007199254740993.06',
      order_book_status: 'available',
      order_book_unavailable_reason: null,
    };
    const result = validateQuoteResponse(response, {
      securityIds: [security],
      sourceId: source,
      batchId: batch,
    });
    expect(result.data?.quotes[0]).toMatchObject({
      last: '9007199254740993.01',
      bid: '9007199254740993.02',
      ask: '9007199254740993.03',
      bid_size: '9007199254740993.04',
      ask_size: '9007199254740993.05',
      volume: '0',
      amount: '9007199254740993.06',
    });
  });

  it('preserves all minute, anomaly, and flow Decimal fields as exact wire text', () => {
    const exact = '9007199254740993.01';
    const bar = {
      ...minuteBar('2026-10-09T01:30:00Z', 10),
      open: exact,
      high: exact,
      low: exact,
      close: exact,
      volume: '0',
      amount: exact,
    };
    const minute = validateMinuteResponse(minuteResponse([bar]), {
      securityId: security,
      date,
    });
    expect(minute.data?.bars[0]).toMatchObject({
      open: exact,
      high: exact,
      low: exact,
      close: exact,
      volume: '0',
      amount: exact,
    });

    const anomaly = anomalyResponse();
    (anomaly.data as { events: Record<string, unknown>[] }).events[0] = {
      ...(anomaly.data as { events: Record<string, unknown>[] }).events[0],
      value: exact,
      threshold: '0.02000000000000000001',
    };
    expect(validateAnomalyResponse(anomaly, { date }).data?.events[0]).toMatchObject({
      value: exact,
      threshold: '0.02000000000000000001',
    });

    const flow = flowResponse(0, null);
    (flow.data as { points: Record<string, unknown>[] }).points[0] = {
      ...(flow.data as { points: Record<string, unknown>[] }).points[0],
      net_inflow: exact,
      unavailable_reason: null,
    };
    expect(validateSectorFlowResponse(flow, {
      sectorId: 'sector-1',
      date,
    }).data?.points[0].net_inflow).toBe(exact);
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

  it('rejects future-visible or future-received records across every realtime resource', () => {
    const future = '2026-10-09T02:00:01Z';
    expect(() => validateQuoteResponse(quoteResponse({
      received_at: future,
    }), {
      securityIds: [security],
    })).toThrow(/as.of/i);

    for (const patch of [
      { received_at: future, available_at: future },
      { available_at: future },
    ]) {
      expect(() => validateMinuteResponse(minuteResponse([{
        ...minuteBar('2026-10-09T01:30:00Z', 10),
        ...patch,
      }]), {
        securityId: security,
        date,
      })).toThrow(/as.of/i);
    }

    const event = (anomalyResponse().data as {
      events: Record<string, unknown>[];
    }).events[0];
    expect(() => validateAnomalyResponse(anomalyResponse({
      events: [{ ...event, available_at: future }],
    }), { date })).toThrow(/as.of/i);

    const flow = flowResponse();
    (flow.data as { points: Record<string, unknown>[] }).points[0].available_at = future;
    expect(() => validateSectorFlowResponse(flow, {
      sectorId: 'sector-1',
      date,
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

  it('binds quote coverage to the request and actual returned quotes', () => {
    const partial = quoteResponse({
      status: 'incomplete',
    });
    Object.assign(partial, {
      coverage: { expected: 2, valid: 1, excluded: 0, missing: 1 },
      quality_flags: ['partial_quotes'],
    });
    expect(validateQuoteResponse(partial, {
      securityIds: [security, '000001.SZ'],
    }).data?.quotes).toHaveLength(1);

    const wrongExpected = quoteResponse();
    Object.assign(wrongExpected, {
      coverage: { expected: 2, valid: 1, excluded: 0, missing: 1 },
      quality_flags: ['partial_quotes'],
    });
    expect(() => validateQuoteResponse(wrongExpected, {
      securityIds: [security],
    })).toThrow(/coverage expected/i);

    const wrongValid = quoteResponse();
    Object.assign(wrongValid, {
      coverage: { expected: 1, valid: 0, excluded: 0, missing: 1 },
      quality_flags: ['partial_quotes'],
    });
    expect(() => validateQuoteResponse(wrongValid, {
      securityIds: [security],
    })).toThrow(/coverage valid/i);
  });

  it('binds minute, anomaly, and flow coverage to actual returned record counts', () => {
    const wrongMinute = minuteResponse();
    Object.assign(wrongMinute, {
      coverage: { expected: 3, valid: 1, excluded: 0, missing: 2 },
    });
    expect(() => validateMinuteResponse(wrongMinute, {
      securityId: security,
      date,
    })).toThrow(/coverage valid/i);

    const wrongAnomaly = anomalyResponse();
    Object.assign(wrongAnomaly, {
      coverage: { expected: 2, valid: 2, excluded: 0, missing: 0 },
    });
    expect(() => validateAnomalyResponse(wrongAnomaly, {
      date,
    })).toThrow(/coverage valid/i);

    const wrongFlow = flowResponse(0, null);
    Object.assign(wrongFlow, {
      coverage: { expected: 2, valid: 2, excluded: 0, missing: 0 },
    });
    expect(() => validateSectorFlowResponse(wrongFlow, {
      sectorId: 'sector-1',
      date,
    })).toThrow(/coverage valid/i);

    const falseEmpty = envelope(null, {
      coverage: { expected: 1, valid: 1, excluded: 0, missing: 0 },
      quality_flags: ['no_data'],
    });
    expect(() => validateMinuteResponse(falseEmpty, {
      securityId: security,
      date,
    })).toThrow(/coverage valid/i);
  });

  it('accepts truthful empty, partial, and complete coverage for realtime records', () => {
    const empty = envelope(null, {
      coverage: { expected: 0, valid: 0, excluded: 0, missing: 0 },
      quality_flags: ['no_data'],
    });
    expect(validateMinuteResponse(empty, {
      securityId: security,
      date,
    }).data).toBeNull();
    expect(validateSectorFlowResponse(empty, {
      sectorId: 'sector-1',
      date,
    }).data).toBeNull();

    expect(validateMinuteResponse(minuteResponse(), {
      securityId: security,
      date,
    }).data?.bars).toHaveLength(2);

    const partialAnomaly = anomalyResponse({ status: 'incomplete' });
    Object.assign(partialAnomaly, {
      coverage: { expected: 2, valid: 1, excluded: 0, missing: 1 },
      quality_flags: ['stream_gap'],
    });
    expect(validateAnomalyResponse(partialAnomaly, {
      date,
    }).data?.events).toHaveLength(1);

    expect(validateSectorFlowResponse(flowResponse(0, null), {
      sectorId: 'sector-1',
      date,
    }).data?.points).toHaveLength(1);
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

  it('binds each minute event to the selected Asia/Shanghai trade date', () => {
    expect(validateMinuteResponse(
      minuteResponse([minuteBar('2026-10-08T16:00:00Z', 10)]),
      { securityId: security, date },
    ).data?.bars[0].event_at).toBe('2026-10-08T16:00:00Z');

    expect(() => validateMinuteResponse(
      minuteResponse([minuteBar('2026-10-08T15:59:59Z', 10)]),
      { securityId: security, date },
    )).toThrow(/event date/i);
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
      value: '0.03',
      threshold: '0.02',
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

  it('binds each anomaly event to the selected Asia/Shanghai trade date', () => {
    const event = (anomalyResponse().data as { events: Record<string, unknown>[] }).events[0];
    expect(() => validateAnomalyResponse(anomalyResponse({
      events: [{
        ...event,
        event_at: '2026-10-09T16:00:00Z',
        available_at: '2026-10-09T16:00:00Z',
      }],
    }), { date })).toThrow(/event date/i);
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
    expect(zero.data?.points[0].net_inflow).toBe('0');
    expect(zero.data?.points[0].unavailable_reason).toBeNull();
  });

  it('rejects complete flow containing an unavailable null point', () => {
    const response = flowResponse();
    (response.data as Record<string, unknown>).status = 'complete';
    response.quality_flags = [];

    expect(() => validateSectorFlowResponse(response, {
      sectorId: 'sector-1',
      date,
    })).toThrow(/availability/i);
  });

  it('requires unavailable flow flag to match the null point reason', () => {
    const response = flowResponse();
    Object.assign(response, { quality_flags: ['source_unavailable'] });

    expect(() => validateSectorFlowResponse(response, {
      sectorId: 'sector-1',
      date,
    })).toThrow(/availability/i);
  });

  it('accepts the source-defined slash unit without relaxing identifiers', () => {
    const response = flowResponse(0, null);
    Object.assign(response.basis, {
      unit: 'currency/share',
      currency: null,
    });
    const point = (response.data as { points: Record<string, unknown>[] }).points[0];
    point.unit = 'currency/share';

    expect(validateSectorFlowResponse(response, {
      sectorId: 'sector-1',
      date,
      sourceId: source,
      batchId: batch,
    })).toMatchObject({
      basis: { unit: 'currency/share' },
      data: { points: [{ unit: 'currency/share' }] },
    });

    point.source_id = 'licensed/feed';
    expect(() => validateSectorFlowResponse(response, {
      sectorId: 'sector-1',
      date,
    })).toThrow(/source/i);
  });

  it.each([
    ['empty', ''],
    ['markup', '<script>'],
    ['control character', 'currency/\nshare'],
    ['overlong', `currency/${'s'.repeat(193)}`],
  ])('rejects %s source-defined flow unit', (_name, unit) => {
    const response = flowResponse(0, null);
    Object.assign(response.basis, { unit, currency: null });
    (response.data as { points: Record<string, unknown>[] }).points[0].unit = unit;

    expect(() => validateSectorFlowResponse(response, {
      sectorId: 'sector-1',
      date,
    })).toThrow(/unit/i);
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

  it('binds each flow point to the selected Asia/Shanghai trade date', () => {
    const response = flowResponse();
    const point = (response.data as { points: Record<string, unknown>[] }).points[0];
    point.event_at = '2026-10-09T16:00:00Z';
    point.available_at = '2026-10-09T16:00:00Z';
    expect(() => validateSectorFlowResponse(response, {
      sectorId: 'sector-1',
      date,
    })).toThrow(/event date/i);
  });

  it('accepts legal null-data envelopes with known missing or zero coverage', () => {
    const missing = envelope(null, {
      trade_date: null,
      source: null,
      source_version: null,
      coverage: { expected: 1, valid: 0, excluded: 0, missing: 1 },
      quality_flags: ['no_data'],
    });
    expect(validateQuoteResponse(missing, {
      securityIds: [security],
    })).toMatchObject({
      data: null,
      coverage: { expected: 1, valid: 0, excluded: 0, missing: 1 },
    });

    const contradictory = envelope(null, {
      trade_date: null,
      coverage: { expected: 1, valid: 1, excluded: 0, missing: 0 },
      quality_flags: [],
    });
    expect(() => validateQuoteResponse(contradictory, {
      securityIds: [security],
    })).toThrow(/coverage valid/i);
  });

  it.each([
    ['complete with missing coverage', minuteResponse([], {
      status: 'complete',
    })],
    ['complete with a reason', envelope({
      status: 'complete',
      bars: [],
    }, { quality_flags: ['stale_stream'] })],
    ['incomplete without missing coverage', envelope({
      status: 'incomplete',
      bars: [],
    })],
    ['incomplete without known coverage', envelope({
      status: 'incomplete',
      bars: [],
    }, { coverage: null, quality_flags: ['stream_gap'] })],
    ['incomplete without a reason', envelope({
      status: 'incomplete',
      bars: [],
    }, {
      coverage: { expected: 1, valid: 0, excluded: 0, missing: 1 },
      quality_flags: [],
    })],
    ['delayed without a reason', envelope({
      status: 'delayed',
      bars: [],
    })],
    ['unavailable without a reason', envelope({
      status: 'unavailable',
      bars: [],
    })],
  ])('rejects SourceCompleteness contradiction: %s', (_name, response) => {
    expect(() => validateMinuteResponse(response, {
      securityId: security,
      date,
    })).toThrow(/completeness/i);
  });

  it('accepts delayed and unavailable states allowed by backend SourceCompleteness', () => {
    const delayedUnknown = envelope({
      status: 'delayed',
      bars: [],
    }, {
      coverage: null,
      quality_flags: ['stale_stream'],
    });
    expect(validateMinuteResponse(delayedUnknown, {
      securityId: security,
      date,
    }).data?.status).toBe('delayed');

    const unavailableKnown = envelope({
      status: 'unavailable',
      bars: [],
    }, {
      coverage: { expected: 1, valid: 0, excluded: 0, missing: 1 },
      quality_flags: ['source_unavailable'],
    });
    expect(validateMinuteResponse(unavailableKnown, {
      securityId: security,
      date,
    }).data?.status).toBe('unavailable');
  });

  it.each([
    ['realtime exclusions', envelope({
      status: 'incomplete',
      bars: [],
    }, {
      coverage: { expected: 1, valid: 0, excluded: 1, missing: 0 },
      quality_flags: ['stream_gap'],
    })],
    ['multiple quality reasons', envelope({
      status: 'delayed',
      bars: [],
    }, {
      quality_flags: ['stream_gap', 'second_reason'],
    })],
  ])('rejects noncanonical %s', (_name, response) => {
    expect(() => validateMinuteResponse(response, {
      securityId: security,
      date,
    })).toThrow();
  });
});

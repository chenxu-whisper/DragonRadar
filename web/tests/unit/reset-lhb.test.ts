import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  disclosurePath,
  formatLhbDecimal,
  parseLhbJson,
  requestLhb,
  seatHistoryPath,
  supportsJsonParseSource,
  validateDisclosureList,
  validateSeatHistory,
  LhbDataFormatError,
} from '../../src/features/lhb/client';
import type {
  DisclosureListResponse,
  DisclosureSelection,
  SeatHistoryResponse,
  SeatHistorySelection,
} from '../../src/features/lhb/types';

const date = '2026-09-10';
const asOf = '2026-09-10T08:01:00Z';
const security = 'TEST001';
const source = 'demo-feed';
const version = 'v1';

const coverage = {
  total_disclosures: 1,
  visible_disclosures: 1,
  late_disclosures: 0,
  selected_disclosures: 1,
  seat_count: 2,
  complete_seats: 1,
  unknown_seats: 1,
};
const basis = { unit: '元', currency: 'CNY' };

afterEach(() => vi.unstubAllGlobals());

function selection(changes: Partial<DisclosureSelection> = {}): DisclosureSelection {
  return { date, securityId: security, page: 1, size: 20, asOf, ...changes };
}

function historySelection(
  changes: Partial<SeatHistorySelection> = {},
): SeatHistorySelection {
  return { seatId: 'SEAT-A', date, days: 30, asOf, ...changes };
}

function seat(changes: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    source_row_id: 'row-A',
    seat_id: 'SEAT-A',
    source_seat_name: '同名席位演示',
    mapping_version: 'map-v1',
    side: 'buy',
    rank: 1,
    buy_amount: 100,
    sell_amount: 40,
    currency: 'CNY',
    unit: '元',
    completeness: 'complete',
    net_amount: 60,
    ...changes,
  };
}

function seatB(): Record<string, unknown> {
  return {
    source_row_id: 'row-B',
    seat_id: 'SEAT-B',
    source_seat_name: '同名席位演示',
    mapping_version: 'map-v1',
    side: null,
    rank: null,
    buy_amount: null,
    sell_amount: 0,
    currency: 'CNY',
    unit: '元',
    completeness: 'buy_unknown',
    net_amount: null,
  };
}

function disclosureItem(changes: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    disclosure_id: 'disc-1',
    security_id: security,
    disclosure_date: date,
    reason: '日价格涨幅偏离值达到 7%',
    revision: 1,
    source_id: source,
    source_version: version,
    source_event_at: '2026-09-10T07:10:00Z',
    received_at: '2026-09-10T07:20:00Z',
    available_at: '2026-09-10T07:30:00Z',
    seats: [seat(), seatB()],
    ...changes,
  };
}

function lhbEnvelope(data: unknown, changes: Record<string, unknown> = {}) {
  return {
    date,
    as_of: asOf,
    source,
    source_version: version,
    quality_flags: [],
    basis: structuredClone(basis),
    coverage: structuredClone(coverage),
    data,
    ...changes,
  };
}

function listResponse(changes: Record<string, unknown> = {}): Record<string, unknown> {
  return lhbEnvelope({
    items: [disclosureItem()],
    total: 1,
    page: 1,
    size: 20,
  }, changes) as Record<string, unknown>;
}

function emptyListResponse(changes: Record<string, unknown> = {}): Record<string, unknown> {
  return lhbEnvelope({
    items: [],
    total: 0,
    page: 1,
    size: 20,
  }, {
    source: null,
    source_version: null,
    quality_flags: ['no_data'],
    basis: { unit: null, currency: null },
    coverage: {
      total_disclosures: 0,
      visible_disclosures: 0,
      late_disclosures: 0,
      selected_disclosures: 0,
      seat_count: 0,
      complete_seats: 0,
      unknown_seats: 0,
    },
    ...changes,
  }) as Record<string, unknown>;
}

function contribution(changes: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    disclosure_id: 'disc-1',
    security_id: security,
    revision: 1,
    disclosure_date: date,
    reason: '日价格涨幅偏离值达到 7%',
    source_row_id: 'row-A',
    source_seat_name: '同名席位演示',
    mapping_version: 'map-v1',
    permission_state: 'authorized',
    ...changes,
  };
}

function aggregatedSeat(changes: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    seat_id: 'SEAT-A',
    source_id: source,
    source_version: version,
    mapping_version: 'map-v1',
    source_seat_name: '同名席位演示',
    currency: 'CNY',
    unit: '元',
    buy_amount: 100,
    sell_amount: 80,
    net_amount: 20,
    completeness: 'complete',
    contributions: [
      contribution(),
      contribution({
        revision: 2,
        source_row_id: 'row-A2',
        permission_state: 'revoked',
      }),
    ],
    ...changes,
  };
}

function seatIdentity(changes: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    source_id: source,
    seat_id: 'SEAT-A',
    source_seat_name: '同名席位演示',
    mapping_version: 'map-v1',
    ...changes,
  };
}

function historyResponse(changes: Record<string, unknown> = {}): Record<string, unknown> {
  return lhbEnvelope({
    seat: seatIdentity(),
    identity_state: 'known',
    items: [aggregatedSeat()],
    total: 1,
  }, {
    coverage: {
      total_disclosures: 2,
      visible_disclosures: 2,
      late_disclosures: 0,
      selected_disclosures: 2,
      seat_count: 1,
      complete_seats: 1,
      unknown_seats: 0,
    },
    ...changes,
  }) as Record<string, unknown>;
}

function emptyHistoryResponse(changes: Record<string, unknown> = {}): Record<string, unknown> {
  return lhbEnvelope({
    seat: {
      source_id: 'SEAT-B',
      seat_id: 'SEAT-B',
      source_seat_name: 'SEAT-B',
      mapping_version: 'unknown',
    },
    identity_state: 'unknown',
    items: [],
    total: 0,
  }, {
    source: null,
    source_version: null,
    quality_flags: ['no_data'],
    basis: { unit: null, currency: null },
    coverage: {
      total_disclosures: 0,
      visible_disclosures: 0,
      late_disclosures: 0,
      selected_disclosures: 0,
      seat_count: 0,
      complete_seats: 0,
      unknown_seats: 0,
    },
    ...changes,
  }) as Record<string, unknown>;
}

describe('LHB GET path builders', () => {
  it('builds the EX08 disclosure path with exact pagination and point-in-time parameters', () => {
    expect(disclosurePath(selection())).toBe(
      '/api/lhb?date=2026-09-10&security_id=TEST001&page=1&size=20'
      + '&as_of=2026-09-10T08%3A01%3A00Z',
    );
  });

  it('omits optional as_of when absent', () => {
    const { asOf: _asOf, ...withoutAsOf } = selection();
    expect(disclosurePath(withoutAsOf)).toBe(
      '/api/lhb?date=2026-09-10&security_id=TEST001&page=1&size=20',
    );
  });

  it('builds the EX09 seat history path with an encoded path segment', () => {
    expect(seatHistoryPath(historySelection())).toBe(
      '/api/lhb/seats/SEAT-A?date=2026-09-10&days=30&as_of=2026-09-10T08%3A01%3A00Z',
    );
    expect(seatHistoryPath(historySelection({ seatId: 'a/b:d' }))).toBe(
      '/api/lhb/seats/a%2Fb%3Ad?date=2026-09-10&days=30&as_of=2026-09-10T08%3A01%3A00Z',
    );
  });

  it.each([
    '/api/lhb2',
    '/api/lhb/seats/SEAT-A/evil',
    '/api/other',
    '//api/lhb',
  ])('rejects non-whitelisted path %j without dispatching fetch', async path => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    await expect(requestLhb(path)).rejects.toMatchObject({
      status: 400,
      message: '仅允许同源只读接口。',
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('rejects literal backslash paths but accepts an encoded %5C query value', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{}')));
    await expect(requestLhb('/api/lhb?x=%5C')).resolves.toBeDefined();

    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    await expect(requestLhb('/api/lhb\\evil')).rejects.toMatchObject({ status: 400 });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe('LHB lossless JSON parsing', () => {
  it('uses native JSON.parse lexical source in the current Node runtime', () => {
    expect(supportsJsonParseSource()).toBe(true);
  });

  it.each([
    '/api/lhb?date=2026-09-10',
    '/api/lhb/seats/SEAT-A?date=2026-09-10',
  ])('preserves amount Decimal tokens on both LHB routes (%s)', path => {
    const parsed = parseLhbJson(
      '{"buy_amount":1.2300,"sell_amount":0,"net_amount":-4.5600,"revision":1}',
      path,
    ) as Record<string, unknown>;
    expect(parsed.buy_amount).toBe('1.2300');
    expect(parsed.sell_amount).toBe('0');
    expect(parsed.net_amount).toBe('-4.5600');
    expect(parsed.revision).toBe(1);
  });

  it('refuses to parse unknown paths', () => {
    expect(() => parseLhbJson('{"buy_amount":1}', '/api/realtime/quotes'))
      .toThrow(LhbDataFormatError);
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
      new Response(nativeJson.stringify(listResponse())),
    ));

    await expect(requestLhb<unknown>(disclosurePath(selection())))
      .rejects.toMatchObject({ status: 502, code: 'DATA_FORMAT' });
  });

  it('maps malformed JSON text to malformed_response', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{not json')));
    await expect(requestLhb<unknown>(disclosurePath(selection())))
      .rejects.toMatchObject({ status: 502, code: 'malformed_response' });
  });

  it('keeps an unquoted arbitrary-precision Decimal exact through HTTP validation', async () => {
    const exact = `${'9'.repeat(40)}.${'8'.repeat(20)}`;
    const body = JSON.stringify(listResponse())
      .replace('"buy_amount":100', `"buy_amount":${exact}`)
      .replace('"net_amount":60', `"net_amount":${exact}`);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(body)));

    const raw = await requestLhb<unknown>(disclosurePath(selection()));
    const result = validateDisclosureList(raw, selection());
    const firstSeat = result.data.items[0].seats[0];
    expect(firstSeat.buy_amount).toBe(exact);
    expect(firstSeat.net_amount).toBe(exact);
  });

  it('keeps an unquoted negative aggregated net amount exact on EX09', async () => {
    const exact = `-${'1'.repeat(36)}.25`;
    const body = JSON.stringify(historyResponse())
      .replace('"net_amount":20', `"net_amount":${exact}`);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(body)));

    const raw = await requestLhb<unknown>(seatHistoryPath(historySelection()));
    const result = validateSeatHistory(raw, historySelection());
    expect(result.data.items[0].net_amount).toBe(exact);
  });

  it('passes non-2xx status codes through without echoing the server body', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ code: 'FORBIDDEN' }), { status: 403 }),
    ));
    await expect(requestLhb<unknown>(disclosurePath(selection())))
      .rejects.toMatchObject({ status: 403, code: 'FORBIDDEN' });

    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ code: 'NOT_FOUND' }), { status: 404 }),
    ));
    await expect(requestLhb<unknown>(seatHistoryPath(historySelection())))
      .rejects.toMatchObject({ status: 404, code: 'NOT_FOUND' });
  });
});

describe('EX08 disclosure list validation', () => {
  it('keeps same-name distinct seats separate and preserves unknown vs zero', () => {
    const result = validateDisclosureList(listResponse(), selection());
    const seats = result.data.items[0].seats;
    expect(seats.map(item => item.seat_id)).toEqual(['SEAT-A', 'SEAT-B']);
    expect(seats.map(item => item.source_seat_name))
      .toEqual(['同名席位演示', '同名席位演示']);

    expect(seats[0].buy_amount).toBe('100');
    expect(seats[0].sell_amount).toBe('40');
    expect(seats[0].net_amount).toBe('60');
    expect(seats[1].buy_amount).toBeNull();
    expect(seats[1].sell_amount).toBe('0');
    expect(seats[1].net_amount).toBeNull();
  });

  it('returns the typed envelope with exact key sets', () => {
    const result = validateDisclosureList(listResponse(), selection());
    expect(Object.keys(result).sort()).toEqual([
      'as_of', 'basis', 'coverage', 'data', 'date',
      'quality_flags', 'source', 'source_version',
    ]);
    expect(Object.keys(result.basis).sort()).toEqual(['currency', 'unit']);
    expect(Object.keys(result.coverage).sort()).toEqual([
      'complete_seats', 'late_disclosures', 'seat_count',
      'selected_disclosures', 'total_disclosures', 'unknown_seats',
      'visible_disclosures',
    ]);
    expect(Object.keys(result.data).sort()).toEqual(['items', 'page', 'size', 'total']);
    expect(Object.keys(result.data.items[0]).sort()).toEqual([
      'available_at', 'disclosure_date', 'disclosure_id', 'reason', 'received_at',
      'revision', 'seats', 'security_id', 'source_event_at', 'source_id',
      'source_version',
    ]);
    expect(Object.keys(result.data.items[0].seats[0]).sort()).toEqual([
      'buy_amount', 'completeness', 'currency', 'mapping_version', 'net_amount',
      'rank', 'seat_id', 'sell_amount', 'side', 'source_row_id',
      'source_seat_name', 'unit',
    ]);
  });

  it('accepts the empty window contract (total zero iff no_data)', () => {
    const result = validateDisclosureList(emptyListResponse(), selection());
    expect(result.data.items).toEqual([]);
    expect(result.data.total).toBe(0);
    expect(result.quality_flags).toEqual(['no_data']);
    expect(result.source).toBeNull();
    expect(result.basis).toEqual({ unit: null, currency: null });
  });

  it('accepts an out-of-range page with total greater than zero and no no_data flag', () => {
    const response = listResponse();
    (response.data as Record<string, unknown>).items = [];
    Object.assign(response.data as object, { page: 2, size: 20 });
    const result = validateDisclosureList(response, selection({ page: 2 }));
    expect(result.data.items).toEqual([]);
    expect(result.data.total).toBe(1);
    expect(result.quality_flags).toEqual([]);
  });

  it.each([
    ['extra envelope key', (r: Record<string, unknown>) => { r.snapshot_id = null; }],
    ['missing envelope key', (r: Record<string, unknown>) => { delete r.basis; }],
    ['extra basis key', (r: Record<string, unknown>) => {
      (r.basis as Record<string, unknown>).price_type = 'raw';
    }],
    ['extra coverage key', (r: Record<string, unknown>) => {
      (r.coverage as Record<string, unknown>).expected = 1;
    }],
    ['missing coverage key', (r: Record<string, unknown>) => {
      delete (r.coverage as Record<string, unknown>).seat_count;
    }],
    ['extra data key', (r: Record<string, unknown>) => {
      (r.data as Record<string, unknown>).cursor = 'x';
    }],
    ['missing data key', (r: Record<string, unknown>) => {
      delete (r.data as Record<string, unknown>).total;
    }],
    ['extra item key', (r: Record<string, unknown>) => {
      const data = r.data as Record<string, unknown>;
      (data.items as Record<string, unknown>[])[0].person_name = '张三';
    }],
    ['fund attribution key', (r: Record<string, unknown>) => {
      const data = r.data as Record<string, unknown>;
      (data.items as Record<string, unknown>[])[0].fund_name = '某基金';
    }],
    ['holding attribution key', (r: Record<string, unknown>) => {
      const data = r.data as Record<string, unknown>;
      (data.items as Record<string, unknown>[])[0].current_holding = true;
    }],
    ['manager attribution key', (r: Record<string, unknown>) => {
      const data = r.data as Record<string, unknown>;
      (data.items as Record<string, unknown>[])[0].manager = '李四';
    }],
    ['missing item key', (r: Record<string, unknown>) => {
      const data = r.data as Record<string, unknown>;
      delete (data.items as Record<string, unknown>[])[0].reason;
    }],
    ['extra seat key', (r: Record<string, unknown>) => {
      const data = r.data as Record<string, unknown>;
      const item = (data.items as Record<string, unknown>[])[0];
      (item.seats as Record<string, unknown>[])[0].seat_owner = '王五';
    }],
    ['missing seat key', (r: Record<string, unknown>) => {
      const data = r.data as Record<string, unknown>;
      const item = (data.items as Record<string, unknown>[])[0];
      delete (item.seats as Record<string, unknown>[])[0].net_amount;
    }],
  ])('rejects %s', (_name, mutate) => {
    const response = listResponse();
    mutate(response);
    expect(() => validateDisclosureList(response, selection())).toThrow();
  });

  it('rejects duplicate seat_id within one disclosure', () => {
    const response = listResponse();
    const items = (response.data as { items: Record<string, unknown>[] }).items;
    const seats = items[0].seats as Record<string, unknown>[];
    seats[1].seat_id = 'SEAT-A';
    expect(() => validateDisclosureList(response, selection())).toThrow(/seat/i);
  });

  it('rejects a disclosure without seats', () => {
    const response = listResponse();
    (response.data as { items: Record<string, unknown>[] }).items[0].seats = [];
    expect(() => validateDisclosureList(response, selection())).toThrow(/seat/i);
  });

  it.each([
    ['complete with unknown buy', { completeness: 'complete', buy_amount: null, sell_amount: 1, net_amount: null }],
    ['buy_unknown with both amounts', { completeness: 'buy_unknown', buy_amount: 1, sell_amount: 1, net_amount: 0 }],
    ['sell_unknown with both amounts', { completeness: 'sell_unknown', buy_amount: 1, sell_amount: 1, net_amount: 0 }],
    ['both_unknown with sell present', { completeness: 'both_unknown', buy_amount: null, sell_amount: 1, net_amount: null }],
    ['net null with both amounts', { completeness: 'complete', buy_amount: 1, sell_amount: 1, net_amount: null }],
    ['net present while buy null', { completeness: 'buy_unknown', buy_amount: null, sell_amount: 1, net_amount: 0 }],
  ])('rejects completeness/null contradiction (%s)', (_name, patch) => {
    const response = listResponse();
    const seats = (response.data as { items: { seats: Record<string, unknown>[] }[] })
      .items[0].seats;
    Object.assign(seats[0], patch);
    expect(() => validateDisclosureList(response, selection())).toThrow();
  });

  it('accepts a negative net computed from smaller buys than sells', () => {
    const response = listResponse();
    const firstSeat = (response.data as {
      items: { seats: Record<string, unknown>[] }[];
    }).items[0].seats[0];
    firstSeat.buy_amount = 10;
    firstSeat.sell_amount = 30;
    firstSeat.net_amount = -20;
    const result = validateDisclosureList(response, selection());
    expect(result.data.items[0].seats[0].net_amount).toBe('-20');
  });

  it('rejects negative buy or sell and non-RFC8259 amount tokens', () => {
    for (const patch of [
      { buy_amount: -1 },
      { sell_amount: -0.01 },
      { buy_amount: '01' },
      { buy_amount: '.5' },
      { net_amount: 'Infinity' },
      { sell_amount: Number.NaN },
    ]) {
      const response = listResponse();
      Object.assign(
        (response.data as { items: { seats: Record<string, unknown>[] }[] })
          .items[0].seats[0],
        patch,
      );
      expect(() => validateDisclosureList(response, selection())).toThrow();
    }
  });

  it('rejects negative underflow tokens while accepting negative zero spellings', () => {
    for (const negativeUnderflow of ['-1e-400', '-0.0001']) {
      const response = listResponse();
      Object.assign(
        (response.data as { items: { seats: Record<string, unknown>[] }[] })
          .items[0].seats[0],
        { buy_amount: negativeUnderflow },
      );
      expect(() => validateDisclosureList(response, selection())).toThrow(/amount/i);
    }
    for (const negativeZero of ['-0', '-0.0', '-0e0']) {
      const response = listResponse();
      Object.assign(
        (response.data as { items: { seats: Record<string, unknown>[] }[] })
          .items[0].seats[0],
        { buy_amount: negativeZero },
      );
      expect(validateDisclosureList(response, selection())
        .data.items[0].seats[0].buy_amount).toBe(negativeZero);
    }
  });

  it('rejects unknown side, rank or revision values', () => {
    const response = listResponse();
    const seats = (response.data as { items: { seats: Record<string, unknown>[] }[] })
      .items[0].seats;
    seats[0].side = 'holder';
    expect(() => validateDisclosureList(response, selection())).toThrow(/side/i);
    seats[0].side = 'buy';
    seats[0].rank = 0;
    expect(() => validateDisclosureList(response, selection())).toThrow(/rank/i);
    seats[0].rank = 1;
    (response.data as { items: Record<string, unknown>[] }).items[0].revision = 0;
    expect(() => validateDisclosureList(response, selection())).toThrow(/revision/i);
  });

  it('rejects date and security selection mismatches', () => {
    const wrongEnvelopeDate = listResponse({ date: '2026-09-09' });
    expect(() => validateDisclosureList(wrongEnvelopeDate, selection()))
      .toThrow(/date/i);

    const response = listResponse();
    (response.data as { items: Record<string, unknown>[] }).items[0]
      .disclosure_date = '2026-09-09';
    expect(() => validateDisclosureList(response, selection())).toThrow(/date/i);

    const wrongSecurity = listResponse();
    (wrongSecurity.data as { items: Record<string, unknown>[] }).items[0]
      .security_id = 'TEST002';
    expect(() => validateDisclosureList(wrongSecurity, selection()))
      .toThrow(/security/i);
  });

  it('rejects invalid calendar dates and naive timestamps', () => {
    expect(() => validateDisclosureList(listResponse({ date: '2026-02-30' }), selection()))
      .toThrow(/date/i);
    const response = listResponse();
    const item = (response.data as { items: Record<string, unknown>[] }).items[0];
    item.available_at = '2026-09-10T07:30:00';
    expect(() => validateDisclosureList(response, selection())).toThrow(/timestamp/i);
    item.available_at = '2026-09-10T07:30:00.0000001Z';
    expect(() => validateDisclosureList(response, selection())).toThrow(/timestamp/i);
  });

  it('enforces source_event <= received <= available <= as_of at one microsecond', () => {
    const response = listResponse();
    response.as_of = '2026-09-10T07:30:00.000001Z';
    const item = (response.data as { items: Record<string, unknown>[] }).items[0];
    item.available_at = '2026-09-10T07:30:00.000001Z';
    expect(validateDisclosureList(response, selection({
      asOf: '2026-09-10T15:30:00.000001+08:00',
    })).data.items[0].available_at).toBe('2026-09-10T07:30:00.000001Z');

    item.available_at = '2026-09-10T07:30:00.000002Z';
    expect(() => validateDisclosureList(response, selection({
      asOf: '2026-09-10T15:30:00.000001+08:00',
    }))).toThrow(/as.of|available/i);

    const reordered = listResponse();
    const reorderedItem = (reordered.data as { items: Record<string, unknown>[] }).items[0];
    reorderedItem.received_at = '2026-09-10T07:09:59Z';
    expect(() => validateDisclosureList(reordered, selection())).toThrow(/timestamp/i);
    reorderedItem.received_at = '2026-09-10T07:20:00Z';
    reorderedItem.available_at = '2026-09-10T07:19:59Z';
    expect(() => validateDisclosureList(reordered, selection())).toThrow(/timestamp/i);
  });

  it('compares the selected as_of at backend microsecond precision', () => {
    const response = listResponse();
    response.as_of = '2026-09-10T08:01:00.000001Z';
    expect(validateDisclosureList(response, selection({
      asOf: '2026-09-10T08:01:00.000001Z',
    })).as_of).toBe('2026-09-10T08:01:00.000001Z');
    expect(() => validateDisclosureList(response, selection({
      asOf: '2026-09-10T08:01:00Z',
    }))).toThrow(/as.of/i);
  });

  it('rejects echo page/size values that differ from the request', () => {
    const response = listResponse();
    Object.assign(response.data as object, { page: 2 });
    expect(() => validateDisclosureList(response, selection())).toThrow(/page/i);
    const wrongSize = listResponse();
    Object.assign(wrongSize.data as object, { size: 50 });
    expect(() => validateDisclosureList(wrongSize, selection())).toThrow(/size/i);
  });

  it('rejects total smaller than the returned item count and bad text fields', () => {
    const response = listResponse();
    Object.assign(response.data as object, { total: 0 });
    expect(() => validateDisclosureList(response, selection())).toThrow(/total/i);

    const blankReason = listResponse();
    (blankReason.data as { items: Record<string, unknown>[] }).items[0].reason = '  ';
    expect(() => validateDisclosureList(blankReason, selection())).toThrow(/reason/i);

    const controlName = listResponse();
    const seats = (controlName.data as {
      items: { seats: Record<string, unknown>[] }[];
    }).items[0].seats;
    seats[0].source_seat_name = '席位\u0000';
    expect(() => validateDisclosureList(controlName, selection())).toThrow(/seat/i);

    const oversize = listResponse();
    (oversize.data as { items: Record<string, unknown>[] }).items[0].reason
      = 'x'.repeat(4097);
    expect(() => validateDisclosureList(oversize, selection())).toThrow(/reason/i);
  });

  it('rejects malformed identifiers and currency/unit text', () => {
    const response = listResponse();
    const firstSeat = (response.data as {
      items: { seats: Record<string, unknown>[] }[];
    }).items[0].seats[0];
    firstSeat.seat_id = '.leading-dot';
    expect(() => validateDisclosureList(response, selection())).toThrow(/seat/i);
    firstSeat.seat_id = 'SEAT-A';
    firstSeat.currency = '';
    expect(() => validateDisclosureList(response, selection())).toThrow(/currency/i);
    firstSeat.currency = 'CNY';
    firstSeat.unit = 'CN\u0007Y';
    expect(() => validateDisclosureList(response, selection())).toThrow(/unit/i);
  });

  it.each([
    ['selected above visible', { selected_disclosures: 2 }],
    ['visible above total', { visible_disclosures: 2 }],
    ['late not equal total minus visible', { late_disclosures: 1 }],
    ['seat_count not complete plus unknown', { seat_count: 3 }],
    ['negative count', { complete_seats: -1 }],
  ])('rejects coverage that is not self-consistent (%s)', (_name, patch) => {
    const response = listResponse();
    Object.assign(response.coverage as object, patch);
    expect(() => validateDisclosureList(response, selection())).toThrow(/coverage/i);
  });

  it('binds EX08 coverage to visible revisions and the seats on the current page', () => {
    const selectedMismatch = listResponse();
    Object.assign(selectedMismatch.coverage as object, { selected_disclosures: 0 });
    expect(() => validateDisclosureList(selectedMismatch, selection()))
      .toThrow(/coverage/i);

    const totalMismatch = listResponse();
    Object.assign(totalMismatch.data as object, { total: 2 });
    expect(() => validateDisclosureList(totalMismatch, selection()))
      .toThrow(/coverage|total/i);

    const tooFewSeats = listResponse();
    Object.assign(tooFewSeats.coverage as object, { seat_count: 1, unknown_seats: 0 });
    expect(() => validateDisclosureList(tooFewSeats, selection()))
      .toThrow(/coverage/i);

    const tooFewComplete = listResponse();
    Object.assign(tooFewComplete.coverage as object, {
      complete_seats: 0,
      unknown_seats: 2,
    });
    expect(() => validateDisclosureList(tooFewComplete, selection()))
      .toThrow(/coverage/i);
  });

  it('keeps the same seat_id from two authorized sources apart in full-page coverage', () => {
    // Backend coverage counts distinct (source_id, seat_id) pairs: two
    // authorized feeds may reuse one seat identifier on the same date and
    // security without merging (service.aggregate_seats).
    const sharedSeat = (rowId: string, partial: boolean) => ({
      source_row_id: rowId,
      seat_id: 'SEAT-X',
      source_seat_name: '同名席位演示',
      mapping_version: 'map-v1',
      side: partial ? null : 'buy',
      rank: partial ? null : 1,
      buy_amount: partial ? null : 100,
      sell_amount: partial ? 0 : 40,
      currency: 'CNY',
      unit: '元',
      completeness: partial ? 'buy_unknown' : 'complete',
      net_amount: partial ? null : 60,
    });
    const response = listResponse({
      source: null,
      source_version: null,
      coverage: {
        total_disclosures: 2,
        visible_disclosures: 2,
        late_disclosures: 0,
        selected_disclosures: 2,
        seat_count: 2,
        complete_seats: 1,
        unknown_seats: 1,
      },
      data: {
        items: [
          disclosureItem({
            disclosure_id: 'disc-a',
            source_id: 'feed-a',
            source_version: 'v1',
            source_event_at: '2026-09-10T07:10:00Z',
            received_at: '2026-09-10T07:20:00Z',
            available_at: '2026-09-10T07:30:00Z',
            seats: [sharedSeat('row-a1', false)],
          }),
          disclosureItem({
            disclosure_id: 'disc-b',
            source_id: 'feed-b',
            source_version: 'v9',
            source_event_at: '2026-09-10T07:11:00Z',
            received_at: '2026-09-10T07:21:00Z',
            available_at: '2026-09-10T07:31:00Z',
            seats: [sharedSeat('row-b1', true)],
          }),
        ],
        total: 2,
        page: 1,
        size: 20,
      },
    });

    const result = validateDisclosureList(response, selection());
    const seats = result.data.items.map(item => item.seats[0]);
    expect(seats.map(item => `${item.source_row_id}`)).toEqual(['row-a1', 'row-b1']);
    expect(seats.map(item => item.completeness)).toEqual(['complete', 'buy_unknown']);
    expect(result.coverage.seat_count).toBe(2);
  });

  it('counts one composite (source, seat) key once across two disclosures', () => {
    // Control case: the SAME source reusing the same seat_id across two of its
    // own disclosures is still a single aggregated seat on the backend.
    const response = listResponse({
      source: null,
      source_version: null,
      coverage: {
        total_disclosures: 2,
        visible_disclosures: 2,
        late_disclosures: 0,
        selected_disclosures: 2,
        seat_count: 1,
        complete_seats: 1,
        unknown_seats: 0,
      },
      data: {
        items: [
          disclosureItem({
            disclosure_id: 'disc-a',
            source_event_at: '2026-09-10T07:10:00Z',
            received_at: '2026-09-10T07:20:00Z',
            available_at: '2026-09-10T07:30:00Z',
            seats: [seat({ source_row_id: 'row-a1', seat_id: 'SEAT-X' })],
          }),
          disclosureItem({
            disclosure_id: 'disc-b',
            source_event_at: '2026-09-10T07:11:00Z',
            received_at: '2026-09-10T07:21:00Z',
            available_at: '2026-09-10T07:31:00Z',
            seats: [seat({ source_row_id: 'row-a2', seat_id: 'SEAT-X' })],
          }),
        ],
        total: 2,
        page: 1,
        size: 20,
      },
    });

    const result = validateDisclosureList(response, selection());
    expect(result.coverage.seat_count).toBe(1);
    expect(result.data.items).toHaveLength(2);
  });

  it('requires no_data and null source/basis exactly for an empty window', () => {
    const flagMismatch = emptyListResponse();
    flagMismatch.quality_flags = [];
    expect(() => validateDisclosureList(flagMismatch, selection())).toThrow();

    const sourceLeak = emptyListResponse();
    sourceLeak.source = source;
    expect(() => validateDisclosureList(sourceLeak, selection())).toThrow();

    const flagOnData = listResponse();
    flagOnData.quality_flags = ['no_data'];
    expect(() => validateDisclosureList(flagOnData, selection())).toThrow();

    const unknownFlag = listResponse();
    unknownFlag.quality_flags = ['late_window'];
    expect(() => validateDisclosureList(unknownFlag, selection())).toThrow();
  });

  it('binds a uniform envelope source/version to every item and accepts mixed pages', () => {
    const changed = listResponse();
    (changed.data as { items: Record<string, unknown>[] }).items[0].source_id = 'other-feed';
    expect(() => validateDisclosureList(changed, selection())).toThrow(/source/i);

    const mixed = listResponse({ source: null, source_version: null });
    expect(validateDisclosureList(mixed, selection()).source).toBeNull();
  });

  it('binds basis currency/unit to the uniform page set and allows mixed values as null', () => {
    const wrongBasis = listResponse();
    (wrongBasis.basis as Record<string, unknown>).currency = 'USD';
    expect(() => validateDisclosureList(wrongBasis, selection())).toThrow(/basis/i);

    const response = listResponse();
    const seats = (response.data as {
      items: { seats: Record<string, unknown>[] }[];
    }).items[0].seats;
    seats[1].currency = 'USD';
    seats[1].unit = '美元';
    response.basis = { unit: null, currency: null };
    expect(validateDisclosureList(response, selection()).basis)
      .toEqual({ unit: null, currency: null });
  });
});

describe('EX09 seat history validation', () => {
  it('accepts the known identity history and keeps signed aggregated amounts', () => {
    const result = validateSeatHistory(historyResponse(), historySelection());
    expect(result.data.identity_state).toBe('known');
    expect(Object.keys(result.data).sort()).toEqual(['identity_state', 'items', 'seat', 'total']);
    expect(Object.keys(result.data.seat).sort()).toEqual([
      'mapping_version', 'seat_id', 'source_id', 'source_seat_name',
    ]);
    expect(Object.keys(result.data.items[0]).sort()).toEqual([
      'buy_amount', 'completeness', 'contributions', 'currency', 'mapping_version',
      'net_amount', 'seat_id', 'sell_amount', 'source_id', 'source_seat_name',
      'source_version', 'unit',
    ]);
    expect(Object.keys(result.data.items[0].contributions[0]).sort()).toEqual([
      'disclosure_date', 'disclosure_id', 'mapping_version', 'permission_state',
      'reason', 'revision', 'security_id', 'source_row_id', 'source_seat_name',
    ]);
  });

  it('accepts selected revision counts larger than the aggregated item count', () => {
    const result = validateSeatHistory(historyResponse(), historySelection());
    expect(result.data.items).toHaveLength(1);
    expect(result.coverage.selected_disclosures).toBe(2);
  });

  it.each(['authorized', 'revoked', 'unavailable'] as const)(
    'accepts the captured permission_state %s without filtering',
    state => {
      const response = historyResponse();
      const item = (response.data as { items: Record<string, unknown>[] }).items[0];
      (item.contributions as Record<string, unknown>[])[0].permission_state = state;
      const result = validateSeatHistory(response, historySelection());
      expect(result.data.items[0].contributions[0].permission_state).toBe(state);
    },
  );

  it.each([
    ['captured_at on contribution', (r: Record<string, unknown>) => {
      (r.data as { items: { contributions: Record<string, unknown>[] }[] })
        .items[0].contributions[0].captured_at = '2026-09-10T07:00:00Z';
    }],
    ['grant on contribution', (r: Record<string, unknown>) => {
      (r.data as { items: { contributions: Record<string, unknown>[] }[] })
        .items[0].contributions[0].grant = 'g1';
    }],
    ['snapshot on contribution', (r: Record<string, unknown>) => {
      (r.data as { items: { contributions: Record<string, unknown>[] }[] })
        .items[0].contributions[0].snapshot = 's1';
    }],
    ['fund attribution on aggregated seat', (r: Record<string, unknown>) => {
      (r.data as { items: Record<string, unknown>[] }).items[0].fund_name = '某基金';
    }],
    ['holding attribution on aggregated seat', (r: Record<string, unknown>) => {
      (r.data as { items: Record<string, unknown>[] }).items[0].current_holding = true;
    }],
    ['seat owner on history data', (r: Record<string, unknown>) => {
      (r.data as Record<string, unknown>).seat_owner = '张三';
    }],
    ['person name on seat identity', (r: Record<string, unknown>) => {
      (r.data as { seat: Record<string, unknown> }).seat.person_name = '李四';
    }],
    ['missing contribution key', (r: Record<string, unknown>) => {
      const contributions = (r.data as {
        items: { contributions: Record<string, unknown>[] }[];
      }).items[0].contributions;
      delete contributions[0].permission_state;
    }],
    ['missing aggregated seat key', (r: Record<string, unknown>) => {
      delete (r.data as { items: Record<string, unknown>[] }).items[0].contributions;
    }],
  ])('rejects %s', (_name, mutate) => {
    const response = historyResponse();
    mutate(response);
    expect(() => validateSeatHistory(response, historySelection())).toThrow();
  });

  it('rejects unknown permission and identity enum values', () => {
    const response = historyResponse();
    const contribution = (response.data as {
      items: { contributions: Record<string, unknown>[] }[];
    }).items[0].contributions[0];
    contribution.permission_state = 'granted';
    expect(() => validateSeatHistory(response, historySelection())).toThrow(/permission/i);

    const badIdentity = historyResponse();
    (badIdentity.data as Record<string, unknown>).identity_state = 'mapped';
    expect(() => validateSeatHistory(badIdentity, historySelection())).toThrow(/identity/i);
  });

  it('uses only identity_state to accept an unknown synthetic header', () => {
    const result = validateSeatHistory(emptyHistoryResponse(), historySelection({
      seatId: 'SEAT-B',
    }));
    expect(result.data.identity_state).toBe('unknown');
    expect(result.data.seat.source_id).toBe('SEAT-B');
    expect(result.data.seat.source_seat_name).toBe('SEAT-B');
    expect(result.data.seat.mapping_version).toBe('unknown');
    expect(result.data.items).toEqual([]);
  });

  it('enforces the synthetic header values when identity_state is unknown', () => {
    const response = emptyHistoryResponse();
    (response.data as { seat: Record<string, unknown> }).seat.source_id = source;
    expect(() => validateSeatHistory(response, historySelection({ seatId: 'SEAT-B' })))
      .toThrow(/identity/i);
  });

  it('never guesses identity from a literal mapping_version under known state', () => {
    const response = historyResponse();
    const identitySeat = (response.data as { seat: Record<string, unknown> }).seat;
    identitySeat.mapping_version = 'unknown';
    const result = validateSeatHistory(response, historySelection());
    expect(result.data.identity_state).toBe('known');
    expect(result.data.seat.mapping_version).toBe('unknown');
  });

  it('accepts no_data together with late-only candidates and a resolved identity', () => {
    const response = emptyHistoryResponse({
      coverage: {
        total_disclosures: 1,
        visible_disclosures: 0,
        late_disclosures: 1,
        selected_disclosures: 0,
        seat_count: 0,
        complete_seats: 0,
        unknown_seats: 0,
      },
    });
    const result = validateSeatHistory(response, historySelection({ seatId: 'SEAT-B' }));
    expect(result.coverage.late_disclosures).toBe(1);
    expect(result.quality_flags).toEqual(['no_data']);
  });

  it('binds non-empty coverage to the aggregated item counts', () => {
    const wrongSeatCount = historyResponse();
    Object.assign(wrongSeatCount.coverage as object, { seat_count: 2, unknown_seats: 1 });
    expect(() => validateSeatHistory(wrongSeatCount, historySelection()))
      .toThrow(/coverage/i);

    const wrongComplete = historyResponse();
    Object.assign(wrongComplete.coverage as object, {
      complete_seats: 0,
      unknown_seats: 1,
    });
    expect(() => validateSeatHistory(wrongComplete, historySelection()))
      .toThrow(/coverage/i);

    const selectedAboveVisible = historyResponse();
    Object.assign(selectedAboveVisible.coverage as object, {
      selected_disclosures: 3,
    });
    expect(() => validateSeatHistory(selectedAboveVisible, historySelection()))
      .toThrow(/coverage/i);

    const totalMismatch = historyResponse();
    Object.assign(totalMismatch.data as object, { total: 2 });
    expect(() => validateSeatHistory(totalMismatch, historySelection()))
      .toThrow(/total/i);
  });

  it('rejects completeness contradictions on aggregated seats', () => {
    const response = historyResponse();
    const item = (response.data as { items: Record<string, unknown>[] }).items[0];
    item.buy_amount = null;
    item.net_amount = null;
    expect(() => validateSeatHistory(response, historySelection()))
      .toThrow(/completeness|amount/i);
  });

  it('rejects revision problems in contributions but does not bind their dates', () => {
    const response = historyResponse();
    const target = (response.data as {
      items: { contributions: Record<string, unknown>[] }[];
    }).items[0].contributions[0];
    target.revision = 0;
    expect(() => validateSeatHistory(response, historySelection())).toThrow(/revision/i);
    target.revision = 1;
    target.disclosure_date = '2026-02-30';
    expect(() => validateSeatHistory(response, historySelection())).toThrow(/date/i);
    target.disclosure_date = '2026-09-11';
    expect(validateSeatHistory(response, historySelection())
      .data.items[0].contributions[0].disclosure_date).toBe('2026-09-11');
  });

  it('rejects a response from a different point in time or trade date', () => {
    expect(() => validateSeatHistory(historyResponse({ date: '2026-09-09' }),
      historySelection())).toThrow(/date/i);
    const wrongAsOf = historyResponse();
    wrongAsOf.as_of = '2026-09-10T09:00:00Z';
    expect(() => validateSeatHistory(wrongAsOf, historySelection())).toThrow(/as.of/i);
  });

  it('rejects empty-history leakage of source, basis, or seat counts', () => {
    const leakSource = emptyHistoryResponse({ source: 'demo-feed' });
    expect(() => validateSeatHistory(leakSource, historySelection({ seatId: 'SEAT-B' })))
      .toThrow();
    const leakSeats = emptyHistoryResponse({
      coverage: {
        total_disclosures: 0,
        visible_disclosures: 0,
        late_disclosures: 0,
        selected_disclosures: 0,
        seat_count: 1,
        complete_seats: 1,
        unknown_seats: 0,
      },
    });
    expect(() => validateSeatHistory(leakSeats, historySelection({ seatId: 'SEAT-B' })))
      .toThrow(/coverage/i);
  });
});

describe('LHB display Decimal helper', () => {
  it('shows unknown for null, zero for zero, and groups ordinary values', () => {
    expect(formatLhbDecimal(null)).toBe('未知');
    expect(formatLhbDecimal('0')).toBe('0');
    expect(formatLhbDecimal('-0')).toBe('0');
    expect(formatLhbDecimal('1234567.89')).toBe('1,234,567.89');
    expect(formatLhbDecimal('-1234567.89')).toBe('-1,234,567.89');
  });

  it('bounds extreme values instead of leaking giant strings', () => {
    expect(formatLhbDecimal(`1e${'9'.repeat(46)}`)).toBe('未知');
    expect(formatLhbDecimal('Infinity')).toBe('未知');
    expect(formatLhbDecimal('not-a-number')).toBe('未知');
    for (const extreme of ['1234567890'.repeat(30), '1e100000']) {
      expect(formatLhbDecimal(extreme).length).toBeLessThanOrEqual(80);
    }
  });
});

describe('LHB typed responses', () => {
  it('compiles the fixture into the declared response types', () => {
    const list: DisclosureListResponse = validateDisclosureList(
      listResponse(),
      selection(),
    );
    expect(list.data.items[0].seats[0].buy_amount).toBe('100');

    const history: SeatHistoryResponse = validateSeatHistory(
      historyResponse(),
      historySelection(),
    );
    expect(history.data.identity_state).toBe('known');
  });
});

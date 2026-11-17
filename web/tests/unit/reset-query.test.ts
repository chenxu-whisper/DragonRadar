/**
 * Task 26 slice 4a — whitelist query contract layer (EX12/EX13).
 *
 * Pure units only: wire validators (types.ts), the whitelisted HTTP client
 * (client.ts) and the zero-React preview model (preview.ts). The QueryPanel
 * itself belongs to slice 4b and intentionally has no render test here.
 */
import { afterEach, describe, expect, test, vi } from 'vitest';

import {
  QueryProtocolError,
  validateExecuteResponse,
  validateParseResponse,
  validateTypedAst,
} from '../../src/features/query/types';
import {
  QueryClientError,
  executeQuery,
  parseQuery,
  queryRequest,
} from '../../src/features/query/client';
import {
  BASIS_NOTE,
  FIELD_LABELS,
  OP_LABELS,
  ROLE_LABELS,
  UNSUPPORTED_LABELS,
  astToPreview,
  formatRatioPercent,
  groupByCode,
  isConfirmStale,
  isTypingBlocked,
} from '../../src/features/query/preview';
import { isTypingBlocked as settingsIsTypingBlocked } from '../../src/features/preferences/settings';
import type {
  ExecuteResponse,
  PredicateWire,
  QueryFieldDesc,
  SortWire,
  TypedAstWire,
  UnsupportedCode,
  UnsupportedItem,
} from '../../src/features/query/types';

const SNAPSHOT = '00000000-0000-4000-8000-000000000001';
const QUERY = '11111111-1111-4111-8111-111111111111';
const IDEMPOTENCY = '22222222-2222-4222-8222-222222222222';
const HASH = 'a'.repeat(64);

function ast(overrides: Partial<TypedAstWire> = {}): TypedAstWire {
  return {
    version: 1,
    ast_hash: HASH,
    predicates: [{ field: 'return_ratio', op: 'gt', value: 0.05 }],
    sort: { field: 'return_ratio', direction: 'desc' },
    limit: 50,
    ...overrides,
  };
}

function parsed(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    query_id: QUERY,
    typed_ast: ast(),
    explanation: '涨幅大于5%；按涨幅降序排列；最多返回 50 条。',
    unsupported: [],
    ...overrides,
  };
}

function unsupportedBody(items: UnsupportedItem[] = [
  { code: 'unknown_field', token: '市值', reason: '字段不在白名单内' },
]): Record<string, unknown> {
  return { query_id: null, typed_ast: null, explanation: null, unsupported: items };
}

const sixFields: QueryFieldDesc[] = [
  { key: 'security_id', label: '代码', source: 'role', basis: 'id' },
  { key: 'name', label: '名称', source: 'role', basis: 'name' },
  { key: 'sector_ids', label: '题材', source: 'role', basis: 'topic_id' },
  { key: 'roles', label: '角色', source: 'role', basis: 'external_role' },
  { key: 'boards', label: '板数', source: 'role', basis: 'streak' },
  {
    key: 'return_ratio',
    label: '涨幅',
    source: 'analysis',
    basis: '20 交易日 · 未复权 raw_close · 基准 B000300',
  },
];

function executed(overrides: Record<string, unknown> = {}): ExecuteResponse {
  return {
    trade_date: '2026-09-10',
    as_of: '2026-09-10T08:01:00Z',
    snapshot_id: SNAPSHOT,
    source: 'analysis_materialization',
    source_version: 'analysis-v1',
    coverage: { expected: 65, valid: 3, excluded: 60, missing: 2 },
    quality_flags: [],
    rule_version: 'role-v1',
    basis: {
      unit: 'ratio',
      currency: 'CNY',
      price_type: 'raw_close',
      benchmark: 'B000300',
      adjustment_version: 'adj-v1',
      rounding: 'ROUND_HALF_UP',
    },
    data: {
      fields: sixFields,
      rows: [
        {
          security_id: 'TEST001',
          name: '合成测试证券1',
          sector_ids: ['SEC001'],
          roles: ['trend_leader'],
          boards: 2,
          return_ratio: 0.0523,
        },
        {
          security_id: 'TEST002',
          name: null,
          sector_ids: [],
          roles: [],
          boards: null,
          return_ratio: null,
        },
      ],
      total: 3,
      unavailable: [],
    },
    ...overrides,
  } as ExecuteResponse;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

// ---------------------------------------------------------------------------
// preview.ts — pure Chinese presentation model
// ---------------------------------------------------------------------------

describe('preview: frozen Chinese vocabulary', () => {
  test('field/op/role maps cover exactly the frozen enums', () => {
    expect(Object.keys(FIELD_LABELS).sort()).toEqual(
      ['boards', 'return_ratio', 'role', 'sector_id', 'security_id'].sort(),
    );
    expect(FIELD_LABELS).toEqual({
      security_id: '证券代码',
      sector_id: '题材',
      return_ratio: '涨幅',
      boards: '连板数',
      role: '角色',
    });
    expect(OP_LABELS).toEqual({
      eq: '是',
      in: '属于',
      gt: '大于',
      gte: '大于等于',
      lt: '小于',
      lte: '小于等于',
    });
    expect(ROLE_LABELS).toEqual({
      trend_leader: '趋势',
      emotion_leader: '情绪',
      zhongjun: '中军',
      genfeng: '跟风',
      buZhang: '补涨',
      jieli: '接力',
    });
    expect(ROLE_LABELS.buZhang).toBe('补涨');
    expect(Object.keys(UNSUPPORTED_LABELS).sort()).toEqual([
      'empty_input',
      'model_ast_rejected',
      'model_unavailable',
      'orphan_token',
      'unknown_field',
      'unknown_operator',
      'unsupported_expression',
      'unsupported_logic',
      'unsupported_value',
    ]);
  });

  test('basis note pins the D1 raw 20-day B000300 wording', () => {
    expect(BASIS_NOTE).toBe('20 个交易日 · 未复权 raw_close · 基准 B000300');
    expect(BASIS_NOTE).toContain('20 个交易日');
    expect(BASIS_NOTE).toContain('未复权 raw_close');
    expect(BASIS_NOTE).toContain('基准 B000300');
  });
});

describe('preview: fraction -> percent text is exact (integer/string arithmetic)', () => {
  test.each<[number, string]>([
    [0.05, '5%'],
    [-1, '-100%'],
    [10, '1000%'],
    [0, '0%'],
    [-0, '0%'],
    [0.1234, '12.34%'],
    [0.123456, '12.3456%'],
    [0.0001, '0.01%'],
    [0.00001, '0.001%'],
    [0.000001, '0.0001%'],
    [9.999999, '999.9999%'],
    [-0.01, '-1%'],
  ])('formatRatioPercent(%s) === %s', (value, expected) => {
    expect(formatRatioPercent(value)).toBe(expected);
  });

  test('signed rendering only prefixes strictly positive values', () => {
    expect(formatRatioPercent(0.05, true)).toBe('+5%');
    expect(formatRatioPercent(-0.01, true)).toBe('-1%');
    expect(formatRatioPercent(0, true)).toBe('0%');
    expect(formatRatioPercent(-0, true)).toBe('0%');
  });
});

describe('preview: four deterministic anchor ASTs (spec 3.3 lines 140-143)', () => {
  test('anchor 1: 「涨幅大于5%的股票」', () => {
    const model = astToPreview(ast());
    expect(model.conditions).toEqual([
      { field: '涨幅', op: '大于', value: '5%' },
    ]);
    expect(model.sort).toBe('涨幅 从高到低');
    expect(model.limit).toBe(50);
    expect(model.basisNote).toBe(BASIS_NOTE);
  });

  test('anchor 2: 「2连板以上 且 角色是中军」', () => {
    const model = astToPreview(ast({
      predicates: [
        { field: 'boards', op: 'gte', value: 2 },
        { field: 'role', op: 'eq', value: 'zhongjun' },
      ],
      sort: null,
    }));
    expect(model.conditions).toEqual([
      { field: '连板数', op: '大于等于', value: '2' },
      { field: '角色', op: '是', value: '中军' },
    ]);
    expect(model.sort).toBeNull();
  });

  test('anchor 3: 「代码属于 TEST001,TEST002 的股票前20」', () => {
    const model = astToPreview(ast({
      predicates: [{ field: 'security_id', op: 'in', value: ['TEST001', 'TEST002'] }],
      sort: { field: 'security_id', direction: 'asc' },
      limit: 20,
    }));
    expect(model.conditions).toEqual([
      { field: '证券代码', op: '属于', value: 'TEST001、TEST002' },
    ]);
    expect(model.sort).toBe('证券代码 从低到高');
    expect(model.limit).toBe(20);
  });

  test('anchor 4: 「题材为 T01 的股票按板数从高到低」', () => {
    const model = astToPreview(ast({
      predicates: [{ field: 'sector_id', op: 'eq', value: 'T01' }],
      sort: { field: 'boards', direction: 'desc' } satisfies SortWire,
    }));
    expect(model.conditions).toEqual([
      { field: '题材', op: '是', value: 'T01' },
    ]);
    expect(model.sort).toBe('连板数 从高到低');
  });

  test('role IN and boards IN render Chinese enumeration with 、', () => {
    const model = astToPreview(ast({
      predicates: [
        { field: 'role', op: 'in', value: ['zhongjun', 'buZhang'] },
        { field: 'boards', op: 'in', value: [2, 3, 5] },
      ],
      sort: null,
      limit: 10,
    }));
    expect(model.conditions).toEqual([
      { field: '角色', op: '属于', value: '中军、补涨' },
      { field: '连板数', op: '属于', value: '2、3、5' },
    ]);
  });
});

describe('preview: confirmation staleness, IME reuse and unsupported grouping', () => {
  test('isConfirmStale is a strict text equality check', () => {
    expect(isConfirmStale('涨幅大于5%', '涨幅大于5%')).toBe(false);
    expect(isConfirmStale('涨幅大于5%', '涨幅大于6%')).toBe(true);
    expect(isConfirmStale('涨幅大于5%', '涨幅大于5% ')).toBe(true);
    expect(isConfirmStale('', '')).toBe(false);
  });

  test('isTypingBlocked is the Task25 settings helper reused, not reimplemented', () => {
    expect(isTypingBlocked).toBe(settingsIsTypingBlocked);
    expect(isTypingBlocked({ isComposing: true })).toBe(true);
    expect(isTypingBlocked({ keyCode: 229 })).toBe(true);
    expect(isTypingBlocked({ isComposing: false, keyCode: 13 })).toBe(false);
    expect(isTypingBlocked({})).toBe(false);
  });

  test('groupByCode keeps first-occurrence order across all nine codes', () => {
    const order: UnsupportedCode[] = [
      'model_unavailable',
      'empty_input',
      'unsupported_value',
      'unsupported_expression',
      'unsupported_logic',
      'orphan_token',
      'unknown_operator',
      'unknown_field',
      'model_ast_rejected',
    ];
    const item = (code: UnsupportedCode, token: string | null): UnsupportedItem =>
      ({ code, token, reason: `r-${code}` });
    const groups = groupByCode([
      item('orphan_token', '残词1'),
      item('unknown_field', '市值'),
      item('orphan_token', null),
      item('model_unavailable', null),
    ]);
    expect(groups.map(group => group.code)).toEqual([
      'orphan_token',
      'unknown_field',
      'model_unavailable',
    ]);
    expect(groups[0]!.items).toHaveLength(2);
    expect(groups[0]!.items[1]!.token).toBeNull();
    // The helper vocabulary covers all nine closed codes.
    expect(order.every(code => typeof UNSUPPORTED_LABELS[code] === 'string'
      && UNSUPPORTED_LABELS[code].length > 0)).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// types.ts — EX12 parse response
// ---------------------------------------------------------------------------

describe('EX12 validator: success / unsupported two-state mutex', () => {
  test('accepts the success body', () => {
    const ok = validateParseResponse(parsed());
    expect(ok.query_id).toBe(QUERY);
    expect(ok.typed_ast).not.toBeNull();
    expect(ok.typed_ast?.ast_hash).toBe(HASH);
    expect(ok.typed_ast?.predicates[0]).toEqual({
      field: 'return_ratio',
      op: 'gt',
      value: 0.05,
    } satisfies PredicateWire);
    expect(ok.explanation).toContain('涨幅');
    expect(ok.unsupported).toEqual([]);
  });

  test('accepts the unsupported body', () => {
    const failed = validateParseResponse(unsupportedBody());
    expect(failed.query_id).toBeNull();
    expect(failed.typed_ast).toBeNull();
    expect(failed.explanation).toBeNull();
    expect(failed.unsupported).toHaveLength(1);
    expect(failed.unsupported[0]).toEqual({
      code: 'unknown_field',
      token: '市值',
      reason: '字段不在白名单内',
    });
  });

  test.each([
    ['not an object', 'x'],
    ['null', null],
    ['array', []],
    ['missing query_id', () => { const v = parsed(); delete v.query_id; return v; }],
    ['extra top-level key', () => ({ ...parsed(), date: '2026-09-10' })],
    ['query_id not uuid', () => parsed({ query_id: 'not-a-uuid' })],
    ['success with unsupported items', () => parsed({
      unsupported: [{ code: 'empty_input', token: null, reason: 'x' }],
    })],
    ['null ast with a query_id', () => ({ ...unsupportedBody(), query_id: QUERY })],
    ['null ast with an explanation', () => ({ ...unsupportedBody(), explanation: 'x' })],
    ['null ast with an empty unsupported list', () => unsupportedBody([])],
    ['success body without an ast', () => parsed({ typed_ast: null })],
    ['unsupported body carrying an ast', () => ({ ...unsupportedBody(), typed_ast: ast() })],
    ['unsupported not an array', () => ({ ...unsupportedBody(), unsupported: {} })],
    ['bad unsupported code', () => unsupportedBody([
      { code: 'invented', token: null, reason: 'x' },
    ] as unknown as UnsupportedItem[])],
    ['unsupported missing reason', () => unsupportedBody([
      { code: 'orphan_token', token: null },
    ] as unknown as UnsupportedItem[])],
    ['blank reason', () => unsupportedBody([
      { code: 'orphan_token', token: null, reason: '   ' },
    ] as unknown as UnsupportedItem[])],
    ['unsupported extra key', () => unsupportedBody([
      { code: 'orphan_token', token: null, reason: 'x', extra: 1 },
    ] as unknown as UnsupportedItem[])],
    ['unsupported token non-string', () => unsupportedBody([
      { code: 'orphan_token', token: 7, reason: 'x' },
    ] as unknown as UnsupportedItem[])],
    ['explanation non-string', () => parsed({ explanation: 1 })],
  ])('rejects %s', (_name, value) => {
    const body = typeof value === 'function' ? (value as () => unknown)() : value;
    expect(() => validateParseResponse(body)).toThrow(QueryProtocolError);
  });

  test('error messages are deterministic', () => {
    expect(() => validateParseResponse(parsed({ typed_ast: { ...ast(), ast_hash: 'x' } })))
      .toThrow(/ast_hash/);
    expect(() => validateParseResponse(parsed({ typed_ast: 'x' }))).toThrow(/typed_ast/);
  });
});

describe('EX12 validator: typed_ast closed wire', () => {
  const astFaults: Array<[string, Record<string, unknown>]> = [
    ['bad version', { version: 2 }],
    ['version as string', { version: '1' }],
    ['missing ast_hash', { ast_hash: undefined }],
    ['short ast_hash', { ast_hash: 'a'.repeat(63) }],
    ['upper-case ast_hash', { ast_hash: 'A'.repeat(64) }],
    ['non-hex ast_hash', { ast_hash: 'g'.repeat(64) }],
    ['extra ast key', { extra: 1 }],
    ['zero predicates', { predicates: [] }],
    ['six predicates', { predicates: Array.from({ length: 6 }, () => ast().predicates[0]) }],
    ['predicates not array', { predicates: {} }],
    ['bad field enum', { predicates: [{ field: 'market_cap', op: 'gt', value: 1 }] }],
    ['bad op enum', { predicates: [{ field: 'boards', op: 'ne', value: 1 }] }],
    ['bad direction', { sort: { field: 'boards', direction: 'up' } }],
    ['sort on unknown field', { sort: { field: 'market_cap', direction: 'asc' } }],
    ['sort extra key', { sort: { field: 'boards', direction: 'asc', extra: 1 } }],
    ['limit zero', { limit: 0 }],
    ['limit 101', { limit: 101 }],
    ['limit float', { limit: 12.5 }],
    ['limit string', { limit: '50' }],
    ['predicate extra key', { predicates: [{ ...ast().predicates[0], extra: 1 }] }],
    ['predicate missing value', {
      predicates: [{ field: 'boards', op: 'eq' }],
    }],
  ];
  test.each(astFaults)('rejects typed_ast fault: %s', (_name, patch) => {
    const candidate: Record<string, unknown> = { ...ast(), ...patch };
    if ('ast_hash' in patch && patch.ast_hash === undefined) delete candidate.ast_hash;
    expect(() => validateTypedAst(candidate)).toThrow(QueryProtocolError);
    expect(() => validateParseResponse(parsed({ typed_ast: candidate })))
      .toThrow(QueryProtocolError);
  });

  test.each([
    ['security eq non-string', { field: 'security_id', op: 'eq', value: 1 }],
    ['security illegal char', { field: 'security_id', op: 'eq', value: 'bad id!' }],
    ['security 33 chars', { field: 'security_id', op: 'eq', value: 'A'.repeat(33) }],
    ['security in 101', {
      field: 'security_id', op: 'in', value: Array.from({ length: 101 }, (_, i) => `S${i}`),
    }],
    ['security in empty', { field: 'security_id', op: 'in', value: [] }],
    ['security in scalar', { field: 'security_id', op: 'in', value: 'TEST001' }],
    ['security gt rejected', { field: 'security_id', op: 'gt', value: 'TEST001' }],
    ['sector over 64 chars', { field: 'sector_id', op: 'eq', value: '中'.repeat(65) }],
    ['sector empty', { field: 'sector_id', op: 'eq', value: '' }],
    ['sector in 101', {
      field: 'sector_id', op: 'in', value: Array.from({ length: 101 }, (_, i) => `T${i}`),
    }],
    ['sector in number element', { field: 'sector_id', op: 'in', value: [1] }],
    ['ratio string', { field: 'return_ratio', op: 'gt', value: '0.05' }],
    ['ratio NaN', { field: 'return_ratio', op: 'gt', value: Number.NaN }],
    ['ratio Infinity', { field: 'return_ratio', op: 'gt', value: Number.POSITIVE_INFINITY }],
    ['ratio bool', { field: 'return_ratio', op: 'gt', value: true }],
    ['ratio in rejected', { field: 'return_ratio', op: 'in', value: [0.05] }],
    ['ratio eq rejected', { field: 'return_ratio', op: 'eq', value: 0.05 }],
    ['boards float', { field: 'boards', op: 'eq', value: 2.5 }],
    ['boards negative', { field: 'boards', op: 'eq', value: -1 }],
    ['boards 100', { field: 'boards', op: 'eq', value: 100 }],
    ['boards bool', { field: 'boards', op: 'eq', value: true }],
    ['boards string', { field: 'boards', op: 'eq', value: '2' }],
    ['boards in 101', {
      field: 'boards', op: 'in', value: Array.from({ length: 101 }, (_, i) => i),
    }],
    ['boards in string element', { field: 'boards', op: 'in', value: ['2'] }],
    ['role eq unknown', { field: 'role', op: 'eq', value: 'admin' }],
    ['role in seven', {
      field: 'role', op: 'in',
      value: ['trend_leader', 'emotion_leader', 'zhongjun', 'genfeng', 'buZhang', 'jieli', 'jieli'],
    }],
    ['role in number element', { field: 'role', op: 'in', value: [1] }],
    ['role gt rejected', { field: 'role', op: 'gt', value: 'trend_leader' }],
  ])('rejects field x op x value fault: %s', (_name, predicate) => {
    expect(() => validateTypedAst({ ...ast(), predicates: [predicate] }))
      .toThrow(QueryProtocolError);
  });

  test('accepts the legal matrix including list boundaries', () => {
    const legal: PredicateWire[] = [
      { field: 'security_id', op: 'eq', value: 'A_z.0-9:1' },
      { field: 'security_id', op: 'in', value: ['TEST001', 'TEST002'] },
      { field: 'security_id', op: 'in', value: Array.from({ length: 100 }, (_, i) => `S${i}`) },
      { field: 'sector_id', op: 'eq', value: 'T01' },
      { field: 'sector_id', op: 'in', value: Array.from({ length: 100 }, (_, i) => `T${i}`) },
      { field: 'return_ratio', op: 'gte', value: -1 },
      { field: 'return_ratio', op: 'lte', value: 10 },
      { field: 'boards', op: 'eq', value: 0 },
      { field: 'boards', op: 'in', value: [0, 99] },
      { field: 'role', op: 'eq', value: 'buZhang' },
      {
        field: 'role', op: 'in',
        value: ['emotion_leader', 'trend_leader', 'zhongjun', 'genfeng', 'buZhang', 'jieli'],
      },
    ];
    for (const predicate of legal) {
      expect(() => validateTypedAst({ ...ast(), predicates: [predicate], sort: null }))
        .not.toThrow();
    }
  });
});

// ---------------------------------------------------------------------------
// types.ts — EX13 execute response
// ---------------------------------------------------------------------------

describe('EX13 validator: frozen selection envelope', () => {
  test('accepts the frozen envelope and pins the D1 basis values', () => {
    const response = validateExecuteResponse(executed());
    expect(response.data.rows).toHaveLength(2);
    expect(response.data.total).toBe(3);
    expect(response.basis.unit).toBe('ratio');
    expect(response.basis.price_type).toBe('raw_close');
    expect(response.basis.benchmark).toBe('B000300');
    expect(response.coverage).toEqual({ expected: 65, valid: 3, excluded: 60, missing: 2 });
  });

  test('tolerates other known envelope keys but data stays exactly four keys', () => {
    expect(() => validateExecuteResponse(executed({ trace_id: 'x' }))).not.toThrow();
    const dataExtra = executed();
    (dataExtra.data as unknown as Record<string, unknown>).exclusions = [];
    expect(() => validateExecuteResponse(dataExtra)).toThrow(QueryProtocolError);
    const dataMissing = executed();
    delete (dataMissing.data as Partial<ExecuteResponse['data']>).unavailable;
    expect(() => validateExecuteResponse(dataMissing)).toThrow(QueryProtocolError);
  });

  test.each([
    ['not object', null],
    ['missing envelope key', () => {
      const v = executed() as unknown as Record<string, unknown>;
      delete v.rule_version;
      return v;
    }],
    ['snapshot id not uuid', () => executed({ snapshot_id: 'x' })],
    ['trade date bad shape', () => executed({ trade_date: '20260910' })],
    ['as_of non-string', () => executed({ as_of: 1 })],
    ['source blank', () => executed({ source: '' })],
    ['quality flags non-string', () => executed({ quality_flags: ['ok', 1] })],
    ['basis price_type raw', () => executed({
      basis: { ...executed().basis, price_type: 'raw' },
    })],
    ['basis benchmark csi300', () => executed({
      basis: { ...executed().basis, benchmark: 'CSI300' },
    })],
    ['basis unit percent', () => executed({
      basis: { ...executed().basis, unit: 'percent' },
    })],
    ['extra basis key', () => executed({
      basis: { ...executed().basis, window: 20 },
    })],
    ['basis non-object', () => executed({ basis: null })],
    ['coverage non-object', () => executed({ coverage: null })],
    ['data non-object', () => executed({ data: null })],
  ])('envelope fault: %s', (_name, value) => {
    const body = typeof value === 'function' ? (value as () => unknown)() : value;
    expect(() => validateExecuteResponse(body)).toThrow(QueryProtocolError);
  });

  test.each([
    ['extra key', { expected: 1, valid: 1, excluded: 0, missing: 0, extra: 1 }],
    ['negative', { expected: 1, valid: -1, excluded: 1, missing: 1 }],
    ['bool', { expected: 1, valid: true, excluded: 0, missing: 0 }],
    ['equation broken', { expected: 65, valid: 3, excluded: 60, missing: 1 }],
    ['float', { expected: 1.5, valid: 1, excluded: 0.5, missing: 0 }],
    ['missing slot', { expected: 1, valid: 1, excluded: 0 }],
  ])('coverage fault: %s', (_name, coverage) => {
    expect(() => validateExecuteResponse(executed({ coverage })))
      .toThrow(QueryProtocolError);
  });

  test('rejects over 100 rows but accepts exactly 100', () => {
    const many = executed();
    many.data.rows = Array.from({ length: 101 }, () => ({ ...executed().data.rows[0]! }));
    expect(() => validateExecuteResponse(many)).toThrow(QueryProtocolError);

    const hundred = executed();
    hundred.data.rows = Array.from({ length: 100 }, (_, index) => ({
      ...executed().data.rows[0]!,
      security_id: `T${String(index).padStart(3, '0')}`,
    }));
    hundred.data.total = 100;
    expect(() => validateExecuteResponse(hundred)).not.toThrow();
  });

  test.each([
    ['row extra key', { ...executed().data.rows[0], extra: 1 }],
    ['row missing name', (() => {
      const r = { ...executed().data.rows[0]! } as Record<string, unknown>;
      delete r.name;
      return r;
    })()],
    ['bad security id', { ...executed().data.rows[0], security_id: 'bad id!' }],
    ['name number', { ...executed().data.rows[0], name: 1 }],
    ['sector scalar', { ...executed().data.rows[0], sector_ids: 'SEC001' }],
    ['sector element non-string', { ...executed().data.rows[0], sector_ids: [1] }],
    ['bad role', { ...executed().data.rows[0], roles: ['admin'] }],
    ['roles not array', { ...executed().data.rows[0], roles: 'trend_leader' }],
    ['boards 100', { ...executed().data.rows[0], boards: 100 }],
    ['boards string', { ...executed().data.rows[0], boards: '2' }],
    ['Decimal ratio as string', { ...executed().data.rows[0], return_ratio: '0.05' }],
    ['ratio NaN', { ...executed().data.rows[0], return_ratio: Number.NaN }],
  ])('row fault: %s', (_name, row) => {
    const body = executed();
    body.data.rows = [row] as unknown as ExecuteResponse['data']['rows'];
    expect(() => validateExecuteResponse(body)).toThrow(QueryProtocolError);
  });

  test('fields must cover the six closed columns and total must cover rows', () => {
    const missingColumn = executed();
    missingColumn.data.fields = sixFields.slice(1);
    expect(() => validateExecuteResponse(missingColumn)).toThrow(QueryProtocolError);

    const duplicate = executed();
    duplicate.data.fields = [sixFields[0]!, ...sixFields];
    expect(() => validateExecuteResponse(duplicate)).toThrow(QueryProtocolError);

    const badKey = executed();
    badKey.data.fields = [
      ...sixFields.slice(0, -1),
      { key: 'ratio', label: '涨幅' },
    ] as QueryFieldDesc[];
    expect(() => validateExecuteResponse(badKey)).toThrow(QueryProtocolError);

    const blankLabel = executed();
    blankLabel.data.fields = sixFields.map(field =>
      field.key === 'boards' ? { ...field, label: '' } : field);
    expect(() => validateExecuteResponse(blankLabel)).toThrow(QueryProtocolError);

    const totalTooSmall = executed();
    totalTooSmall.data.total = 1;
    expect(() => validateExecuteResponse(totalTooSmall)).toThrow(QueryProtocolError);
  });

  test('unavailable items are closed {code, reason} pairs', () => {
    const bad = executed();
    bad.data.unavailable = [{ code: 'return_ratio_unavailable' }] as unknown as
      ExecuteResponse['data']['unavailable'];
    expect(() => validateExecuteResponse(bad)).toThrow(QueryProtocolError);

    const extraKey = executed();
    extraKey.data.unavailable = [
      { code: 'return_ratio_unavailable', reason: 'x', severity: 'warn' },
    ] as unknown as ExecuteResponse['data']['unavailable'];
    expect(() => validateExecuteResponse(extraKey)).toThrow(QueryProtocolError);

    const declared = executed();
    declared.data.unavailable = [
      { code: 'return_ratio_unavailable', reason: 'insufficient_calendar_history' },
    ];
    expect(() => validateExecuteResponse(declared)).not.toThrow();
  });
});

// ---------------------------------------------------------------------------
// client.ts — whitelisted POST channel
// ---------------------------------------------------------------------------

describe('query client: request shape', () => {
  function stubFetch(response: unknown, init?: ResponseInit) {
    const fetcher = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        new Response(JSON.stringify(response), init),
    );
    vi.stubGlobal('fetch', fetcher);
    return fetcher;
  }

  test('parse POST sends exactly the three frozen keys with CSRF', async () => {
    vi.stubGlobal('document', { cookie: '__Host-dragon-private-csrf=query-csrf' });
    const fetcher = stubFetch(parsed());
    const result = await parseQuery('涨幅大于5%', SNAPSHOT, IDEMPOTENCY);

    expect(fetcher).toHaveBeenCalledTimes(1);
    const [path, options] = fetcher.mock.calls[0]!;
    expect(path).toBe('/api/query/parse');
    expect(options).toMatchObject({
      method: 'POST',
      credentials: 'same-origin',
      cache: 'no-store',
    });
    expect(options!.headers).toMatchObject({
      Accept: 'application/json',
      'Content-Type': 'application/json',
      'X-CSRF-Token': 'query-csrf',
    });
    expect(JSON.parse(options!.body as string)).toStrictEqual({
      text: '涨幅大于5%',
      snapshot_id: SNAPSHOT,
      idempotency_key: IDEMPOTENCY,
    });
    expect(result.query_id).toBe(QUERY);
  });

  test('missing CSRF cookie sends an empty token header', async () => {
    vi.stubGlobal('document', { cookie: '' });
    const fetcher = stubFetch(parsed());
    await parseQuery('涨幅大于5%', SNAPSHOT, IDEMPOTENCY);
    const [, options] = fetcher.mock.calls[0]!;
    expect((options!.headers as Record<string, string>)['X-CSRF-Token']).toBe('');
  });

  test('execute POST sends exactly the four frozen keys and never a date', async () => {
    vi.stubGlobal('document', { cookie: '' });
    const fetcher = stubFetch(executed());
    await executeQuery({
      queryId: QUERY,
      confirmedAstHash: HASH,
      snapshotId: SNAPSHOT,
      idempotencyKey: IDEMPOTENCY,
    });
    const [path, options] = fetcher.mock.calls[0]!;
    expect(path).toBe('/api/query/execute');
    expect(JSON.parse(options!.body as string)).toStrictEqual({
      query_id: QUERY,
      confirmed_ast_hash: HASH,
      snapshot_id: SNAPSHOT,
      idempotency_key: IDEMPOTENCY,
    });
    expect(Object.keys(JSON.parse(options!.body as string)).sort()).toStrictEqual(
      ['confirmed_ast_hash', 'idempotency_key', 'query_id', 'snapshot_id'].sort(),
    );
    expect(options!.body as string).not.toContain('date');
  });

  test('path whitelist rejects anything but parse/execute before fetch', async () => {
    const fetcher = stubFetch({});
    await expect(queryRequest('/api/owner/v1/notes', {})).rejects.toMatchObject({
      status: 400,
      code: 'INVALID_PARAM',
    });
    await expect(queryRequest('/api/query/parse?x=1', {})).rejects.toMatchObject({
      status: 400,
      code: 'INVALID_PARAM',
    });
    await expect(queryRequest('/api/query/parse\\x', {})).rejects.toMatchObject({
      status: 400,
      code: 'INVALID_PARAM',
    });
    await expect(queryRequest('/api/query/drop', {})).rejects.toMatchObject({
      status: 400,
    });
    expect(fetcher).not.toHaveBeenCalled();
  });

  test('pre-flight validation faults never touch the network', async () => {
    const fetcher = stubFetch(parsed());
    await expect(parseQuery('', SNAPSHOT, IDEMPOTENCY)).rejects.toMatchObject({
      status: 400, code: 'INVALID_PARAM',
    });
    await expect(parseQuery('x'.repeat(501), SNAPSHOT, IDEMPOTENCY)).rejects.toMatchObject({
      status: 400, code: 'INVALID_PARAM',
    });
    await expect(parseQuery('涨幅', 'not-a-uuid', IDEMPOTENCY)).rejects.toMatchObject({
      status: 400, code: 'INVALID_PARAM',
    });
    await expect(parseQuery('涨幅', SNAPSHOT, '')).rejects.toMatchObject({
      status: 400, code: 'INVALID_PARAM',
    });
    await expect(parseQuery('涨幅', SNAPSHOT, 'k'.repeat(201))).rejects.toMatchObject({
      status: 400, code: 'INVALID_PARAM',
    });
    await expect(executeQuery({
      queryId: 'x', confirmedAstHash: HASH, snapshotId: SNAPSHOT, idempotencyKey: IDEMPOTENCY,
    })).rejects.toMatchObject({ status: 400, code: 'INVALID_PARAM' });
    await expect(executeQuery({
      queryId: QUERY, confirmedAstHash: 'bad', snapshotId: SNAPSHOT, idempotencyKey: IDEMPOTENCY,
    })).rejects.toMatchObject({ status: 400, code: 'INVALID_PARAM' });
    await expect(executeQuery({
      queryId: QUERY, confirmedAstHash: HASH, snapshotId: SNAPSHOT, idempotencyKey: '',
    })).rejects.toMatchObject({ status: 400, code: 'INVALID_PARAM' });
    expect(fetcher).not.toHaveBeenCalled();
  });
});

describe('query client: timeout, abort and transport failures', () => {
  test('aborts after 15 seconds with a normalized timeout error', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('document', { cookie: '' });
    const fetcher = vi.fn(
      (_input: RequestInfo | URL, _init?: RequestInit) =>
        new Promise<Response>(() => {}),
    );
    vi.stubGlobal('fetch', fetcher);
    const pending = parseQuery('涨幅大于5%', SNAPSHOT, IDEMPOTENCY);
    const rejection = expect(pending).rejects.toMatchObject({
      status: 408, code: 'timeout',
    });
    await vi.advanceTimersByTimeAsync(15_001);
    await rejection;
    const signal = fetcher.mock.calls[0]![1]!.signal as AbortSignal;
    expect(signal.aborted).toBe(true);
  });

  test('401 dispatches the existing private-session-lock event', async () => {
    const dispatch = vi.fn();
    vi.stubGlobal('window', { dispatchEvent: dispatch });
    vi.stubGlobal('document', { cookie: '' });
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({ code: 'SESSION_UNAUTHORIZED', message: 'x' }), { status: 401 })));
    await expect(parseQuery('涨幅大于5%', SNAPSHOT, IDEMPOTENCY)).rejects.toMatchObject({
      status: 401,
      code: 'SESSION_UNAUTHORIZED',
    });
    expect(dispatch).toHaveBeenCalledTimes(1);
    expect(dispatch.mock.calls[0]![0]).toBeInstanceOf(Event);
    expect((dispatch.mock.calls[0]![0] as Event).type).toBe('private-session-lock');
  });

  test('network failure becomes status 0 network_error', async () => {
    vi.stubGlobal('document', { cookie: '' });
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('offline'); }));
    await expect(parseQuery('涨幅大于5%', SNAPSHOT, IDEMPOTENCY)).rejects.toMatchObject({
      status: 0, code: 'network_error',
    });
  });

  test('non-JSON or validator-failing 200 bodies become malformed_response', async () => {
    vi.stubGlobal('document', { cookie: '' });
    vi.stubGlobal('fetch', vi.fn(async () => new Response('not-json', { status: 200 })));
    await expect(parseQuery('涨幅大于5%', SNAPSHOT, IDEMPOTENCY)).rejects.toMatchObject({
      status: 502, code: 'malformed_response',
    });

    vi.stubGlobal('fetch', vi.fn(async () => new Response('{"unexpected":true}', { status: 200 })));
    await expect(executeQuery({
      queryId: QUERY, confirmedAstHash: HASH, snapshotId: SNAPSHOT, idempotencyKey: IDEMPOTENCY,
    })).rejects.toMatchObject({ status: 502, code: 'malformed_response' });
  });
});

describe('query client: DEC12 error family normalization', () => {
  const cases: Array<[number, string, string]> = [
    [400, 'INVALID_PARAM', 'INVALID_PARAM'],
    [403, 'FORBIDDEN', 'FORBIDDEN'],
    [404, 'NOT_FOUND', 'NOT_FOUND'],
    [409, 'VERSION_CONFLICT', 'VERSION_CONFLICT'],
    [413, 'INVALID_PARAM', 'INVALID_PARAM'],
    [503, 'STORAGE_UNAVAILABLE', 'STORAGE_UNAVAILABLE'],
    [503, 'DATA_UNAVAILABLE', 'DATA_UNAVAILABLE'],
  ];
  test.each(cases)('%s with code %s normalizes to %s', async (status, code) => {
    vi.stubGlobal('document', { cookie: '' });
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({ code, message: 'x' }), { status })));
    await expect(parseQuery('涨幅大于5%', SNAPSHOT, IDEMPOTENCY)).rejects.toMatchObject({
      status, code,
    });
  });

  test('missing or unexpected body codes fall back to the status family default', async () => {
    vi.stubGlobal('document', { cookie: '' });
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({}), { status: 503 })));
    await expect(parseQuery('涨幅大于5%', SNAPSHOT, IDEMPOTENCY)).rejects.toMatchObject({
      status: 503, code: 'STORAGE_UNAVAILABLE',
    });

    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({ code: 'BOGUS' }), { status: 503 })));
    await expect(parseQuery('涨幅大于5%', SNAPSHOT, IDEMPOTENCY)).rejects.toMatchObject({
      status: 503, code: 'STORAGE_UNAVAILABLE',
    });

    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({ code: 'BOGUS' }), { status: 404 })));
    await expect(parseQuery('涨幅大于5%', SNAPSHOT, IDEMPOTENCY)).rejects.toMatchObject({
      status: 404, code: 'NOT_FOUND',
    });
  });
});

// ---------------------------------------------------------------------------
// slice 4 review P2-1 — inner json() catch must classify, not blanket-502
// ---------------------------------------------------------------------------

describe('query client: json() 阶段异常分类（P2-1）', () => {
  function hangingJsonResponse(status = 200): Response {
    return {
      ok: status >= 200 && status < 300,
      status,
      json: () => new Promise<unknown>(() => {}),
    } as unknown as Response;
  }

  test('外部 abort：响应头已到、json() 永挂起时透传 AbortError 而非 502', async () => {
    vi.stubGlobal('document', { cookie: '' });
    const external = new AbortController();
    let internalSignal: AbortSignal | null | undefined;
    vi.stubGlobal('fetch', vi.fn(async (
      _input: RequestInfo | URL,
      init?: RequestInit,
    ): Promise<Response> => {
      internalSignal = init?.signal;
      // 让调用先拿到响应头、再卡在 json() 上。
      await Promise.resolve();
      return hangingJsonResponse();
    }));

    const pending = parseQuery('涨幅大于5%', SNAPSHOT, IDEMPOTENCY, external.signal);
    await Promise.resolve();
    await Promise.resolve();
    external.abort();
    const failure = await pending.then(() => null, error => error);

    expect(failure).not.toBeNull();
    expect(failure).not.toBeInstanceOf(QueryClientError);
    expect(failure).toBeInstanceOf(DOMException);
    expect((failure as Error).name).toBe('AbortError');
    // 外部 signal 必须级联到内部 fetch signal。
    expect(internalSignal?.aborted).toBe(true);
  });

  test('response.json() 自身 reject AbortError 时原样透传', async () => {
    vi.stubGlobal('document', { cookie: '' });
    vi.stubGlobal('fetch', vi.fn(async (): Promise<Response> => ({
      ok: true,
      status: 200,
      json: async () => { throw new DOMException('body stream aborted', 'AbortError'); },
    } as unknown as Response)));

    const failure = await parseQuery('涨幅大于5%', SNAPSHOT, IDEMPOTENCY)
      .then(() => null, error => error);

    expect(failure).not.toBeInstanceOf(QueryClientError);
    expect((failure as Error).name).toBe('AbortError');
  });

  test('2xx 但 json() reject SyntaxError 仍归一为 502 malformed_response（回归）', async () => {
    vi.stubGlobal('document', { cookie: '' });
    vi.stubGlobal('fetch', vi.fn(async (): Promise<Response> => ({
      ok: true,
      status: 200,
      json: async () => { throw new SyntaxError('Unexpected token'); },
    } as unknown as Response)));

    await expect(parseQuery('涨幅大于5%', SNAPSHOT, IDEMPOTENCY)).rejects.toMatchObject({
      status: 502, code: 'malformed_response',
    });
  });
});

// ---------------------------------------------------------------------------
// slice 4 review P2-2 — non-OK empty bodies keep their status; body-read
// timeout stays 408; CSRF cookie splits on the first equals only
// ---------------------------------------------------------------------------

describe('query client: 非 ok 空体与 json 挂起的分类（P2-2）', () => {
  function emptyBodyResponse(status: number): Response {
    return {
      ok: false,
      status,
      json: async () => { throw new SyntaxError('empty body'); },
    } as unknown as Response;
  }

  test('401 空体（json reject SyntaxError）归一 401 且 private-session-lock 恰好一次', async () => {
    const dispatch = vi.fn();
    vi.stubGlobal('window', { dispatchEvent: dispatch });
    vi.stubGlobal('document', { cookie: '' });
    vi.stubGlobal('fetch', vi.fn(async (): Promise<Response> => emptyBodyResponse(401)));

    await expect(parseQuery('涨幅大于5%', SNAPSHOT, IDEMPOTENCY)).rejects.toMatchObject({
      status: 401,
    });
    expect(dispatch).toHaveBeenCalledTimes(1);
    expect((dispatch.mock.calls[0]![0] as Event).type).toBe('private-session-lock');
  });

  test('503 空体归一 503 且 code 回落 STORAGE_UNAVAILABLE', async () => {
    vi.stubGlobal('document', { cookie: '' });
    vi.stubGlobal('fetch', vi.fn(async (): Promise<Response> => emptyBodyResponse(503)));

    await expect(parseQuery('涨幅大于5%', SNAPSHOT, IDEMPOTENCY)).rejects.toMatchObject({
      status: 503, code: 'STORAGE_UNAVAILABLE',
    });
  });

  test('响应头已到、json() 挂起时 15s 超时给 408 timeout 而非 502', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('document', { cookie: '' });
    vi.stubGlobal('fetch', vi.fn(async (): Promise<Response> => ({
      ok: true,
      status: 200,
      json: () => new Promise<unknown>(() => {}),
    } as unknown as Response)));

    const pending = parseQuery('涨幅大于5%', SNAPSHOT, IDEMPOTENCY);
    const rejection = expect(pending).rejects.toMatchObject({
      status: 408, code: 'timeout',
    });
    await vi.advanceTimersByTimeAsync(15_001);
    await rejection;
  });

  test('CSRF cookie 值含内部等号时只按第一个等号切分 token', async () => {
    // node 测试环境没有 DOM Document：注入最小访问器类，再按浏览器同款写法
    // vi.spyOn(Document.prototype, 'cookie', 'get') 桩其取值。
    class FakeDocument {
      get cookie(): string { return ''; }
    }
    vi.stubGlobal('Document', FakeDocument);
    vi.stubGlobal('document', new FakeDocument());
    vi.spyOn(Document.prototype, 'cookie', 'get')
      .mockReturnValue('__Host-dragon-private-csrf=tok%3D=x; foo=1');

    const fetcher = vi.fn(async (
      _input: RequestInfo | URL,
      _init?: RequestInit,
    ) => new Response(JSON.stringify(parsed())));
    vi.stubGlobal('fetch', fetcher);
    await parseQuery('涨幅大于5%', SNAPSHOT, IDEMPOTENCY);

    const [, options] = fetcher.mock.calls[0]!;
    expect((options!.headers as Record<string, string>)['X-CSRF-Token']).toBe('tok%3D=x');
  });
});

// ---------------------------------------------------------------------------
// slice 4 review P2-5 — coverage counts: safe integers, fail-closed ceiling,
// and closed-layer __proto__ pollution rejection
// ---------------------------------------------------------------------------

describe('EX13 validator: coverage 安全整数与 COVERAGE_MAX 上界（P2-5）', () => {
  test('expected=valid=2**53（非安全整数）拒绝于路径 coverage', () => {
    const body = executed({
      coverage: { expected: 2 ** 53, valid: 2 ** 53, excluded: 0, missing: 0 },
    });
    expect(() => validateExecuteResponse(body)).toThrow(QueryProtocolError);
    expect(() => validateExecuteResponse(body)).toThrow(/at coverage$/);
  });

  test('expected=valid=100001 超过 fail-closed 上界拒绝于 coverage', () => {
    const body = executed({
      coverage: { expected: 100001, valid: 100001, excluded: 0, missing: 0 },
    });
    expect(() => validateExecuteResponse(body)).toThrow(/at coverage$/);
  });

  test.each(['expected', 'valid', 'excluded', 'missing'] as const)(
    'coverage 任一字段 %s 超过 100000 都拒绝于 coverage',
    slot => {
      const coverage = { expected: 1, valid: 1, excluded: 0, missing: 0, [slot]: 100001 };
      const body = executed({ coverage });
      expect(() => validateExecuteResponse(body)).toThrow(QueryProtocolError);
      expect(() => validateExecuteResponse(body)).toThrow(/at coverage$/);
    },
  );

  test('合法等式在域内（含 100000 上界边界）仍通过', () => {
    expect(() => validateExecuteResponse(executed({
      coverage: { expected: 100000, valid: 100000, excluded: 0, missing: 0 },
    }))).not.toThrow();
    expect(() => validateExecuteResponse(executed({
      coverage: { expected: 100000, valid: 99997, excluded: 2, missing: 1 },
    }))).not.toThrow();
  });

  test('域内 valid+excluded+missing!==expected 仍拒绝于 coverage', () => {
    const body = executed({
      coverage: { expected: 10, valid: 4, excluded: 3, missing: 2 },
    });
    expect(() => validateExecuteResponse(body)).toThrow(/at coverage$/);
  });
});

describe('EX12/EX13 validator: 闭层拒绝自有 __proto__ 键（P2-5）', () => {
  test('JSON.parse 构造的带自有 __proto__ predicate 拒绝于 typed_ast.predicates[0]', () => {
    const pollutedPredicate = JSON.parse(
      '{"field":"return_ratio","op":"gt","value":0.05,"__proto__":{"polluted":true}}',
    ) as PredicateWire;
    expect(Object.hasOwn(pollutedPredicate, '__proto__')).toBe(true);

    expect(() => validateTypedAst({ ...ast(), predicates: [pollutedPredicate] }))
      .toThrow(QueryProtocolError);
    expect(() => validateTypedAst({ ...ast(), predicates: [pollutedPredicate] }))
      .toThrow(/at typed_ast\.predicates\[0\]$/);
    // 闭层拒绝不得伴随原型污染。
    expect(({} as Record<string, unknown>).polluted).toBeUndefined();
  });

  test('JSON.parse 构造的带自有 __proto__ coverage 对象拒绝于 coverage', () => {
    const pollutedCoverage = JSON.parse(
      '{"expected":1,"valid":1,"excluded":0,"missing":0,"__proto__":{"x":1}}',
    );
    expect(Object.hasOwn(pollutedCoverage, '__proto__')).toBe(true);

    const body = executed({ coverage: pollutedCoverage });
    expect(() => validateExecuteResponse(body)).toThrow(QueryProtocolError);
    expect(() => validateExecuteResponse(body)).toThrow(/at coverage$/);
    expect(({} as Record<string, unknown>).x).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// slice 4 review P2-6 — out-of-grid scientific ratios render as '—'
// ---------------------------------------------------------------------------

describe('preview: 科学计数法网格外涨幅 fail-closed 为 —（P2-6）', () => {
  test('1e-7 两种形态与 1e21 都渲染为 —', () => {
    expect(formatRatioPercent(1e-7)).toBe('—');
    expect(formatRatioPercent(1e-7, true)).toBe('—');
    expect(formatRatioPercent(1e21)).toBe('—');
    expect(formatRatioPercent(1e21, true)).toBe('—');
  });

  test('网格近邻与边界值渲染不变', () => {
    expect(formatRatioPercent(0.000001)).toBe('0.0001%');
    expect(formatRatioPercent(0.00001)).toBe('0.001%');
    expect(formatRatioPercent(10)).toBe('1000%');
    // 有限性与 -0 既有分支保留。
    expect(formatRatioPercent(Number.NaN)).toBe('—');
    expect(formatRatioPercent(Number.POSITIVE_INFINITY)).toBe('—');
    expect(formatRatioPercent(-0)).toBe('0%');
    expect(formatRatioPercent(-0, true)).toBe('0%');
  });

  test('astToPreview 中 return_ratio 谓词值 1e-7 渲染 value 为 —', () => {
    const model = astToPreview(ast({
      predicates: [{ field: 'return_ratio', op: 'gt', value: 1e-7 }],
      sort: null,
    }));
    expect(model.conditions[0]).toMatchObject({
      field: '涨幅', op: '大于', value: '—',
    });
  });
});

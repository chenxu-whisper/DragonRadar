/**
 * Task 28 — sentiment research EX16 wire contract (sentiment-research-v1).
 *
 * Pure units: fail-closed response validator, frozen closed sets, phase
 * bucket boundaries, reason/label maps, and the banned-copy source scan.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import {
  COMPONENT_KEYS,
  COMPONENT_LABELS,
  PHASE_LABELS,
  REASON_LABELS,
  REASON_NO_LIMIT_EVENTS,
  REASON_NO_TRADABLE_MEMBERS_FROM_YESTERDAY_GE2_SET,
  REASON_NO_VALID_DAILY_RETURNS,
  RESEARCH_PHASES,
  SENTIMENT_RESEARCH_METHOD,
  phaseForScore,
  validateSentimentResearch,
  type ResearchComponent,
  type ResearchComponentKey,
  type SentimentResearchData,
} from '../../src/features/research/types';

const coverage = { expected: 5, valid: 5, excluded: 0, missing: 0 };

function available(
  value: number,
  numerator: number,
  denominator: number,
  withCoverage = false,
): ResearchComponent {
  const component: ResearchComponent = {
    value,
    numerator,
    denominator,
    weight: { numerator: 1, denominator: 3 },
    reason: null,
  };
  if (withCoverage) {
    component.coverage = { ...coverage, expected: denominator, valid: denominator };
  }
  return component;
}

function unavailable(key: ResearchComponentKey): ResearchComponent {
  const reason = {
    breadth: REASON_NO_VALID_DAILY_RETURNS,
    limit_balance: REASON_NO_LIMIT_EVENTS,
    promotion: REASON_NO_TRADABLE_MEMBERS_FROM_YESTERDAY_GE2_SET,
  }[key];
  return {
    value: null,
    numerator: 0,
    denominator: 0,
    weight: { numerator: 1, denominator: 3 },
    reason,
  };
}

function point(
  tradeDate: string,
  score: number | null,
  phase: SentimentResearchData['phase'],
  components: SentimentResearchData['components'],
  reasons: string[] = [],
) {
  return { trade_date: tradeDate, score, phase, components, reasons };
}

function anchorComponents(): SentimentResearchData['components'] {
  return {
    breadth: available(0.6, 3, 5, true),
    limit_balance: available(1, 2, 2),
    promotion: available(0.5, 1, 2, true),
  };
}

function validData(): SentimentResearchData {
  const headlineComponents = anchorComponents();
  return {
    method_version: SENTIMENT_RESEARCH_METHOD,
    research_only: true,
    trade_date: '2026-09-08',
    score: 70.0,
    phase: 'repair',
    components: headlineComponents,
    reasons: [],
    series: [
      point('2026-09-07', 83.33, 'climax', {
        breadth: available(0.5, 1, 2, true),
        limit_balance: available(1, 1, 1),
        promotion: available(1, 1, 1, true),
      }),
      point('2026-09-08', 70.0, 'repair', headlineComponents),
    ],
  };
}

function envelope(data: unknown, qualityFlags: string[] = []) {
  return {
    trade_date: '2026-09-08',
    as_of: '2026-09-08T10:00:00Z',
    snapshot_id: '00000000-0000-4000-8000-000000000091',
    source: 'synthetic-market',
    source_version: 'market-v1',
    coverage: { expected: 5, valid: 5, excluded: 0, missing: 0 },
    quality_flags: qualityFlags,
    rule_version: 'market-v1',
    basis: { unit: 'ratio', currency: 'CNY', price_type: 'raw', benchmark: null },
    data,
  };
}

function reject(body: unknown): void {
  expect(() => validateSentimentResearch(body)).toThrowError(/Invalid sentiment research/);
}

function mutate(data: SentimentResearchData, apply: (clone: any) => void): unknown {
  // Guard: the untouched fixture must validate before each mutation is applied.
  expect(validateSentimentResearch(envelope(data)).data).not.toBeNull();
  const clone = JSON.parse(JSON.stringify(data)) as Record<string, unknown>;
  apply(clone);
  return clone;
}

describe('sentiment research validator', () => {
  it('accepts the frozen 70.00/repair anchor block', () => {
    const resource = validateSentimentResearch(envelope(validData()));
    expect(resource.data).not.toBeNull();
    const data = resource.data!;
    expect(data.score).toBe(70.0);
    expect(data.phase).toBe('repair');
    expect(data.research_only).toBe(true);
    expect(data.method_version).toBe('sentiment-research-v1');
    expect(data.series).toHaveLength(2);
    expect(data.components.promotion.weight).toEqual({ numerator: 1, denominator: 3 });
  });

  it('accepts a null data block for legacy/partial publications', () => {
    const resource = validateSentimentResearch(
      envelope(null, ['materialization_unavailable']),
    );
    expect(resource.data).toBeNull();
    expect(resource.quality_flags).toEqual(['materialization_unavailable']);
  });

  it('accepts an incomplete point whose missing components null the score', () => {
    const data = validData();
    const components = {
      breadth: available(0.5, 1, 2),
      limit_balance: unavailable('limit_balance'),
      promotion: unavailable('promotion'),
    };
    data.series = [point(
      '2026-09-08',
      null,
      null,
      components,
      [REASON_NO_LIMIT_EVENTS, REASON_NO_TRADABLE_MEMBERS_FROM_YESTERDAY_GE2_SET],
    )];
    data.trade_date = '2026-09-08';
    data.score = null;
    data.phase = null;
    data.components = components;
    data.reasons = [REASON_NO_LIMIT_EVENTS, REASON_NO_TRADABLE_MEMBERS_FROM_YESTERDAY_GE2_SET];
    expect(validateSentimentResearch(envelope(data)).data?.score).toBeNull();
  });

  it.each([
    ['wrong method_version', (d: any) => { d.method_version = 'sentiment-research-v9'; }],
    ['research_only false', (d: any) => { d.research_only = false; }],
    ['extra data key', (d: any) => { d.forecast = true; }],
    ['missing data key', (d: any) => { delete d.series; }],
    ['bad phase', (d: any) => { d.phase = 'moon'; }],
    ['phase mismatch', (d: any) => { d.phase = 'ice'; }],
    ['score above 100', (d: any) => { d.score = 100.01; }],
    ['negative score', (d: any) => { d.score = -0.01; }],
    ['non-numeric score', (d: any) => { d.score = '70.00'; }],
    ['missing component', (d: any) => { delete d.components.promotion; }],
    ['extra component', (d: any) => { d.components.extra = d.components.breadth; }],
    ['weight 1/2', (d: any) => { d.components.breadth.weight = { numerator: 1, denominator: 2 }; }],
    ['available component carrying a reason', (d: any) => { d.components.breadth.reason = REASON_NO_VALID_DAILY_RETURNS; }],
    ['unavailable value with denominator', (d: any) => { d.components.limit_balance.value = null; }],
    ['reason not in closed set', (d: any) => {
      d.series[1].reasons = ['made_up_reason'];
      d.reasons = ['made_up_reason'];
    }],
    ['reasons drift from components', (d: any) => { d.reasons = [REASON_NO_LIMIT_EVENTS]; }],
    ['incomplete point with score', (d: any) => {
      d.series[0].score = 10;
      d.series[0].phase = 'ice';
    }],
    ['empty series', (d: any) => { d.series = []; }],
    ['descending series', (d: any) => { d.series.reverse(); }],
    ['duplicate series dates', (d: any) => { d.series[0].trade_date = '2026-09-08'; }],
    ['headline not the last point', (d: any) => { d.score = 83.33; d.phase = 'climax'; }],
    ['bad coverage partition', (d: any) => { d.components.breadth.coverage.missing = 1; }],
    ['numerator above denominator', (d: any) => { d.components.breadth.numerator = 9; }],
    ['boolean score', (d: any) => { d.score = true; }],
    ['non-calendar date', (d: any) => { d.trade_date = '2026-02-30'; }],
  ])('rejects %s', (_name, apply) => {
    reject(envelope(mutate(validData(), apply)));
  });

  it('rejects envelope-level shape drift', () => {
    reject(null);
    reject({});
    reject({ ...envelope(validData()), quality_flags: ['ok', 1] });
    reject({ trade_date: '2026-09-08' });
  });
});

describe('frozen closed sets', () => {
  it('phase labels cover exactly the five wire phases', () => {
    expect(RESEARCH_PHASES).toEqual(['ice', 'ebb', 'divergent', 'repair', 'climax']);
    expect([...RESEARCH_PHASES].map(phase => PHASE_LABELS[phase])).toEqual(
      ['冰点', '退潮', '分歧', '修复', '高潮'],
    );
  });

  it('maps every component to its label and frozen reason', () => {
    expect(COMPONENT_KEYS).toEqual(['breadth', 'limit_balance', 'promotion']);
    expect(COMPONENT_KEYS.map(key => COMPONENT_LABELS[key])).toEqual(
      ['市场广度', '涨跌停平衡', '高位晋级'],
    );
    expect(REASON_LABELS[REASON_NO_VALID_DAILY_RETURNS]).toBe('缺少有效日收益');
    expect(REASON_LABELS[REASON_NO_LIMIT_EVENTS]).toBe('缺少涨跌停事件');
    expect(REASON_LABELS[REASON_NO_TRADABLE_MEMBERS_FROM_YESTERDAY_GE2_SET])
      .toBe('缺少昨 2 板以上可交易成员集合');
  });

  it.each([
    [0, 'ice'], [19.99, 'ice'], [20, 'ebb'], [39.99, 'ebb'],
    [40, 'divergent'], [59.99, 'divergent'], [60, 'repair'], [79.99, 'repair'],
    [80, 'climax'], [100, 'climax'],
  ])('buckets %s as %s', (score, phase) => {
    expect(phaseForScore(score)).toBe(phase);
  });
});

describe('research panel copy compliance', () => {
  const read = (file: string) => readFileSync(resolve(process.cwd(), file), 'utf8');
  const banned = ['胜率', '未来上涨概率', '必涨', '稳赚', '安全', '预测', '见顶', '见底'];

  it('panel and contract sources contain zero banned terms', () => {
    for (const file of [
      'src/features/research/SentimentPanel.tsx',
      'src/features/research/types.ts',
      'src/features/research/sentiment-research.css',
    ]) {
      const source = read(file);
      for (const term of banned) {
        expect(source, `${file} must not contain ${term}`).not.toContain(term);
      }
    }
  });
});

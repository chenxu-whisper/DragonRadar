/**
 * Task 28 sentiment research (method sentiment-research-v1) wire contract.
 *
 * Reset-only research view: the validator is fail-closed — any shape drift,
 * extra key, non-finite token, reason/phase mismatch, or reordered series
 * rejects the whole data block so the panel can never render half-values.
 * The read endpoint publishes materialized facts; the frontend never
 * recomputes scores, it only verifies and displays them.
 */
import type { WorkbenchEnvelope } from '../../types';

export const SENTIMENT_RESEARCH_PATH = '/api/market/sentiment/research';
export const SENTIMENT_RESEARCH_METHOD = 'sentiment-research-v1';
export const RESEARCH_DAYS_DEFAULT = 20;

export const REASON_NO_VALID_DAILY_RETURNS = 'no_valid_daily_returns';
export const REASON_NO_LIMIT_EVENTS = 'no_limit_events';
export const REASON_NO_TRADABLE_MEMBERS_FROM_YESTERDAY_GE2_SET =
  'no_tradable_members_from_yesterday_ge2_set';

export const COMPONENT_KEYS = ['breadth', 'limit_balance', 'promotion'] as const;
export type ResearchComponentKey = (typeof COMPONENT_KEYS)[number];

export const COMPONENT_LABELS: Record<ResearchComponentKey, string> = {
  breadth: '市场广度',
  limit_balance: '涨跌停平衡',
  promotion: '高位晋级',
};

export const COMPONENT_REASONS: Record<ResearchComponentKey, string> = {
  breadth: REASON_NO_VALID_DAILY_RETURNS,
  limit_balance: REASON_NO_LIMIT_EVENTS,
  promotion: REASON_NO_TRADABLE_MEMBERS_FROM_YESTERDAY_GE2_SET,
};

export const REASON_LABELS: Record<string, string> = {
  [REASON_NO_VALID_DAILY_RETURNS]: '缺少有效日收益',
  [REASON_NO_LIMIT_EVENTS]: '缺少涨跌停事件',
  [REASON_NO_TRADABLE_MEMBERS_FROM_YESTERDAY_GE2_SET]: '缺少昨 2 板以上可交易成员集合',
};

export const RESEARCH_PHASES = ['ice', 'ebb', 'divergent', 'repair', 'climax'] as const;
export type ResearchPhase = (typeof RESEARCH_PHASES)[number];

export const PHASE_LABELS: Record<ResearchPhase, string> = {
  ice: '冰点',
  ebb: '退潮',
  divergent: '分歧',
  repair: '修复',
  climax: '高潮',
};

export interface ResearchCoverage {
  expected: number;
  valid: number;
  excluded: number;
  missing: number;
}

export interface ResearchComponent {
  value: number | null;
  numerator: number;
  denominator: number;
  weight: { numerator: 1; denominator: 3 };
  reason: string | null;
  coverage?: ResearchCoverage;
}

export interface ResearchPoint {
  trade_date: string;
  score: number | null;
  phase: ResearchPhase | null;
  components: Record<ResearchComponentKey, ResearchComponent>;
  reasons: string[];
}

export interface SentimentResearchData {
  method_version: typeof SENTIMENT_RESEARCH_METHOD;
  research_only: true;
  trade_date: string;
  score: number | null;
  phase: ResearchPhase | null;
  components: Record<ResearchComponentKey, ResearchComponent>;
  reasons: string[];
  series: ResearchPoint[];
}

export type SentimentResearchResource = WorkbenchEnvelope<SentimentResearchData | null>;

const COMPONENT_KEY_SET = new Set<string>(COMPONENT_KEYS);
const PHASE_SET = new Set<string>(RESEARCH_PHASES);
const REASON_SET = new Set<string>(Object.values(COMPONENT_REASONS));
const COMPONENT_ALLOWED = ['value', 'numerator', 'denominator', 'weight', 'reason'] as const;
const POINT_KEYS = ['trade_date', 'score', 'phase', 'components', 'reasons'] as const;
const DATA_KEYS = [
  'method_version',
  'research_only',
  'trade_date',
  'score',
  'phase',
  'components',
  'reasons',
  'series',
] as const;
const COVERAGE_KEYS = ['expected', 'valid', 'excluded', 'missing'] as const;
const DATE = /^\d{4}-\d{2}-\d{2}$/;

function fail(field: string): never {
  throw new Error(`Invalid sentiment research ${field}`);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function exactKeys(record: Record<string, unknown>, keys: readonly string[], field: string): void {
  const actual = Object.keys(record).sort();
  const expected = [...keys].sort();
  if (actual.length !== expected.length
    || actual.some((key, index) => key !== expected[index])) {
    fail(`${field} keys`);
  }
}

function nonNegativeInteger(value: unknown, field: string): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0) fail(field);
  return value;
}

function finiteRatio(value: unknown, field: string): number {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0 || value > 1) {
    fail(field);
  }
  return value;
}

function finiteScore(value: unknown, field: string): number {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0 || value > 100) {
    fail(field);
  }
  return value;
}

function calendarDate(value: unknown, field: string): string {
  if (typeof value !== 'string' || !DATE.test(value)) fail(field);
  const parsed = new Date(`${value}T00:00:00Z`);
  if (!Number.isFinite(parsed.valueOf()) || parsed.toISOString().slice(0, 10) !== value) {
    fail(field);
  }
  return value;
}

export function phaseForScore(score: number): ResearchPhase {
  if (score < 20) return 'ice';
  if (score < 40) return 'ebb';
  if (score < 60) return 'divergent';
  if (score < 80) return 'repair';
  return 'climax';
}

function validateCoverage(value: unknown, field: string): ResearchCoverage {
  if (!isRecord(value)) fail(field);
  exactKeys(value, COVERAGE_KEYS, field);
  const coverage = {
    expected: nonNegativeInteger(value.expected, `${field}.expected`),
    valid: nonNegativeInteger(value.valid, `${field}.valid`),
    excluded: nonNegativeInteger(value.excluded, `${field}.excluded`),
    missing: nonNegativeInteger(value.missing, `${field}.missing`),
  };
  if (coverage.expected !== coverage.valid + coverage.excluded + coverage.missing) {
    fail(`${field} partition`);
  }
  return coverage;
}

function validateComponent(key: ResearchComponentKey, value: unknown): ResearchComponent {
  if (!isRecord(value)) fail(`component ${key}`);
  const allowed = value.coverage === undefined
    ? COMPONENT_ALLOWED
    : [...COMPONENT_ALLOWED, 'coverage'];
  exactKeys(value, allowed, `component ${key}`);
  const numerator = nonNegativeInteger(value.numerator, `component ${key}.numerator`);
  const denominator = nonNegativeInteger(value.denominator, `component ${key}.denominator`);
  if (numerator > denominator) fail(`component ${key} counts`);
  const weight = value.weight;
  if (!isRecord(weight) || weight.numerator !== 1 || weight.denominator !== 3
    || Object.keys(weight).length !== 2) {
    fail(`component ${key}.weight`);
  }
  const reason = value.reason;
  if (reason !== null && (typeof reason !== 'string' || reason !== COMPONENT_REASONS[key])) {
    fail(`component ${key}.reason`);
  }
  const component: ResearchComponent = {
    value: null,
    numerator,
    denominator,
    weight: { numerator: 1, denominator: 3 },
    reason,
  };
  if (denominator === 0) {
    if (reason === null || value.value !== null) fail(`component ${key} unavailable`);
  } else if (reason !== null || value.value === null) {
    fail(`component ${key} available`);
  } else {
    component.value = finiteRatio(value.value, `component ${key}.value`);
    // The wire value is the 4-decimal ROUND_HALF_UP display quantization of
    // the integer counts; any drift (the score itself is verified separately
    // from the raw counts) must fail closed.
    const numeratorBI = BigInt(numerator);
    const denominatorBI = BigInt(denominator);
    const expectedDisplay = Number(
      (numeratorBI * 20000n + denominatorBI) / (2n * denominatorBI),
    );
    if (Math.abs(component.value * 10000 - expectedDisplay) > 1e-4) {
      fail(`component ${key}.value quantize`);
    }
  }
  if (value.coverage !== undefined) {
    component.coverage = validateCoverage(value.coverage, `component ${key}.coverage`);
  }
  return component;
}

function validateComponents(value: unknown): Record<ResearchComponentKey, ResearchComponent> {
  if (!isRecord(value)) fail('components');
  if (Object.keys(value).length !== COMPONENT_KEYS.length
    || COMPONENT_KEYS.some(key => !(key in value))) {
    fail('components keys');
  }
  return Object.fromEntries(
    COMPONENT_KEYS.map(key => [key, validateComponent(key, value[key])]),
  ) as Record<ResearchComponentKey, ResearchComponent>;
}

function expectedComponentReasons(
  components: Record<ResearchComponentKey, ResearchComponent>,
): string[] {
  return COMPONENT_KEYS
    .map(key => components[key].reason)
    .filter((reason): reason is string => reason !== null);
}

// Independently verify the published score from the frozen integer counts.
// Mirrors backend Decimal math exactly: ROUND_HALF_UP of
// 100 * (n1/d1 + n2/d2 + n3/d3) / 3 at two decimals, using the unrounded
// ratios (never the 4-decimal display value). BigInt keeps the rational
// arithmetic exact regardless of member-count magnitude.
function expectedScoreCents(
  components: Record<ResearchComponentKey, ResearchComponent>,
): bigint {
  const nums = COMPONENT_KEYS.map(key => BigInt(components[key].numerator));
  const denoms = COMPONENT_KEYS.map(key => BigInt(components[key].denominator));
  const product = denoms[0] * denoms[1] * denoms[2];
  const numerator = nums[0] * denoms[1] * denoms[2]
    + nums[1] * denoms[0] * denoms[2]
    + nums[2] * denoms[0] * denoms[1];
  const divisor = 3n * product;
  return (20000n * numerator + divisor) / (2n * divisor);
}

function validatePoint(value: unknown, field: string): ResearchPoint {
  if (!isRecord(value)) fail(field);
  exactKeys(value, POINT_KEYS, field);
  const point: ResearchPoint = {
    trade_date: calendarDate(value.trade_date, `${field}.trade_date`),
    score: null,
    phase: null,
    components: validateComponents(value.components),
    reasons: [],
  };
  const reasons = value.reasons;
  if (!Array.isArray(reasons)
    || reasons.some(item => typeof item !== 'string' || !REASON_SET.has(item))) {
    fail(`${field}.reasons`);
  }
  const expectedReasons = expectedComponentReasons(point.components);
  if (reasons.length !== expectedReasons.length
    || reasons.some((item, index) => item !== expectedReasons[index])) {
    fail(`${field}.reasons mismatch`);
  }
  point.reasons = [...reasons];
  const phase = value.phase;
  const score = value.score;
  if (expectedReasons.length > 0) {
    if (score !== null || phase !== null) fail(`${field} incomplete score`);
    return point;
  }
  point.score = finiteScore(score, `${field}.score`);
  const expectedCents = Number(expectedScoreCents(point.components));
  if (Math.abs(point.score * 100 - expectedCents) > 1e-6) {
    fail(`${field}.score recompute`);
  }
  if (typeof phase !== 'string' || !PHASE_SET.has(phase) || phaseForScore(point.score) !== phase) {
    fail(`${field}.phase`);
  }
  point.phase = phase as ResearchPhase;
  return point;
}

function sameComponents(
  left: Record<ResearchComponentKey, ResearchComponent>,
  right: Record<ResearchComponentKey, ResearchComponent>,
): boolean {
  return COMPONENT_KEYS.every(key => JSON.stringify(left[key]) === JSON.stringify(right[key]));
}

function samePoint(left: ResearchPoint, right: ResearchPoint): boolean {
  return left.trade_date === right.trade_date
    && left.score === right.score
    && left.phase === right.phase
    && JSON.stringify(left.reasons) === JSON.stringify(right.reasons)
    && sameComponents(left.components, right.components);
}

function validateData(value: unknown): SentimentResearchData {
  if (!isRecord(value)) fail('data');
  exactKeys(value, DATA_KEYS, 'data');
  if (value.method_version !== SENTIMENT_RESEARCH_METHOD) fail('data.method_version');
  if (value.research_only !== true) fail('data.research_only');
  const series = value.series;
  if (!Array.isArray(series) || series.length === 0) fail('data.series');
  const points = series.map((point, index) => validatePoint(point, `series[${index}]`));
  for (let index = 1; index < points.length; index += 1) {
    if (points[index].trade_date <= points[index - 1].trade_date) {
      fail('data.series order');
    }
  }
  const headline = validatePoint(
    {
      trade_date: value.trade_date,
      score: value.score,
      phase: value.phase,
      components: value.components,
      reasons: value.reasons,
    },
    'data headline',
  );
  if (headline.trade_date !== points[points.length - 1].trade_date
    || !samePoint(headline, points[points.length - 1])) {
    fail('data headline');
  }
  return {
    method_version: SENTIMENT_RESEARCH_METHOD,
    research_only: true,
    trade_date: headline.trade_date,
    score: headline.score,
    phase: headline.phase,
    components: headline.components,
    reasons: headline.reasons,
    series: points,
  };
}

export function validateSentimentResearch(value: unknown): SentimentResearchResource {
  if (!isRecord(value)) fail('envelope');
  const flags = value.quality_flags;
  if (!Array.isArray(flags) || flags.some(item => typeof item !== 'string')) {
    fail('envelope.quality_flags');
  }
  if (!('data' in value)) fail('envelope.data');
  const data = value.data === null ? null : validateData(value.data);
  return { ...value, data } as SentimentResearchResource;
}

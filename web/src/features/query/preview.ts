/**
 * Pure presentation model for the whitelist query panel. No React, no IO:
 * every label is a frozen constant so unit tests can pin the Chinese wording
 * against the server parser (parser.py FIELD_LABELS/OP_LABELS/explain_ast).
 *
 * Note: the front-end OP_LABELS render eq as 「是」 per the Task 26 slice-4a
 * contract/tests; parser.py's explanation text uses 「等于」 server-side.
 */

import type {
  PredicateValue,
  QueryField,
  QueryOp,
  RoleValue,
  TypedAstWire,
  UnsupportedCode,
  UnsupportedItem,
} from './types';

// Re-exported (not reimplemented) so IME handling stays identical to the
// Task25 settings helper; tests pin function identity with toBe.
export { isTypingBlocked } from '../preferences/settings';

export const MAX_TEXT_LENGTH = 500;

export const FIELD_LABELS: Record<QueryField, string> = {
  security_id: '证券代码',
  sector_id: '题材',
  return_ratio: '涨幅',
  boards: '连板数',
  role: '角色',
};

export const OP_LABELS: Record<QueryOp, string> = {
  eq: '是',
  in: '属于',
  gt: '大于',
  gte: '大于等于',
  lt: '小于',
  lte: '小于等于',
};

export const ROLE_LABELS: Record<RoleValue, string> = {
  trend_leader: '趋势',
  emotion_leader: '情绪',
  zhongjun: '中军',
  genfeng: '跟风',
  buZhang: '补涨',
  jieli: '接力',
};

export const UNSUPPORTED_LABELS: Record<UnsupportedCode, string> = {
  unknown_field: '未知字段',
  unknown_operator: '未知操作符',
  orphan_token: '无法消费的残词',
  unsupported_logic: '不支持的逻辑（OR/否定/括号）',
  unsupported_expression: '不支持的表达（算术/时间窗口/语气）',
  unsupported_value: '取值不合法',
  empty_input: '空输入',
  model_ast_rejected: '模型建议未通过白名单校验',
  model_unavailable: '模型暂不可用',
};

/**
 * D1 frozen basis wording (spec 3.5; parser.py _BASIS_LINE carries the same
 * facts in its longer sentence): 20 trading days, unadjusted raw_close,
 * benchmark B000300.
 */
export const BASIS_NOTE = '20 个交易日 · 未复权 raw_close · 基准 B000300';

/**
 * Fraction -> percent text via decimal-string point shifting (mirrors
 * parser.py _format_percent: scale 100, four decimal places, strip trailing
 * zeros). Server ratio values live on a six-decimal grid, so the shifted
 * result never needs rounding; float artefacts such as 0.30000000000000004
 * are avoided entirely. signed prefixes only strictly positive values.
 *
 * Fail-closed: inputs outside the six-decimal grid whose string form uses
 * scientific notation (String(value) contains 'e'/'E', e.g. 1e-7 or 1e21)
 * render as '—' instead of shifting exponent characters; the finite check
 * and the -0 -> '0%' branch are unchanged.
 */
export function formatRatioPercent(value: number, signed = false): string {
  if (!Number.isFinite(value)) return '—';
  if (value === 0) return '0%'; // Object.is(-0, 0) is true, so -0 lands here too
  const raw = String(value);
  // 网格外输入（科学计数法，如 1e-7/1e21）fail-closed 为「—」。
  if (raw.includes('e') || raw.includes('E')) return '—';
  const negative = raw.startsWith('-');
  const unsigned = negative ? raw.slice(1) : raw;
  const dot = unsigned.indexOf('.');
  const integerPart = dot === -1 ? unsigned : unsigned.slice(0, dot);
  const fractionPart = dot === -1 ? '' : unsigned.slice(dot + 1);

  let shiftedInteger: string;
  let shiftedFraction: string;
  if (fractionPart.length >= 2) {
    shiftedInteger = integerPart + fractionPart.slice(0, 2);
    shiftedFraction = fractionPart.slice(2);
  } else {
    shiftedInteger = integerPart + fractionPart + '0'.repeat(2 - fractionPart.length);
    shiftedFraction = '';
  }
  // Grid inputs never reach this clamp; it merely keeps the 4-dp contract.
  if (shiftedFraction.length > 4) shiftedFraction = shiftedFraction.slice(0, 4);
  shiftedFraction = shiftedFraction.replace(/0+$/, '');
  shiftedInteger = shiftedInteger.replace(/^0+(?=\d)/, '');
  const digits = shiftedFraction
    ? `${shiftedInteger}.${shiftedFraction}`
    : shiftedInteger;
  const prefix = negative ? '-' : signed ? '+' : '';
  return `${prefix}${digits}%`;
}

function renderPredicateValue(field: QueryField, value: PredicateValue): string {
  if (Array.isArray(value)) {
    // IN lists render with the Chinese enumeration comma; roles take the
    // Chinese map, every other field uses the plain scalar text.
    return value
      .map(item => (field === 'role'
        ? ROLE_LABELS[item as RoleValue]
        : String(item)))
      .join('、');
  }
  if (field === 'return_ratio') return formatRatioPercent(value as number);
  if (field === 'role') return ROLE_LABELS[value as RoleValue];
  return String(value);
}

export interface PreviewCondition {
  field: string;
  op: string;
  value: string;
}

export interface QueryPreviewModel {
  conditions: PreviewCondition[];
  sort: string | null;
  limit: number;
  basisNote: string;
}

/** Deterministic two-stage confirmation card model, derived from the AST only. */
export function astToPreview(typedAst: TypedAstWire): QueryPreviewModel {
  return {
    conditions: typedAst.predicates.map(predicate => ({
      field: FIELD_LABELS[predicate.field],
      op: OP_LABELS[predicate.op],
      value: renderPredicateValue(predicate.field, predicate.value),
    })),
    sort: typedAst.sort
      ? `${FIELD_LABELS[typedAst.sort.field]} ${
        typedAst.sort.direction === 'desc' ? '从高到低' : '从低到高'
      }`
      : null,
    limit: typedAst.limit,
    basisNote: BASIS_NOTE,
  };
}

/** The confirmed AST is valid only while the textarea still holds the parsed text. */
export function isConfirmStale(parsedText: string, currentText: string): boolean {
  return parsedText !== currentText;
}

export interface UnsupportedGroup {
  code: UnsupportedCode;
  items: UnsupportedItem[];
}

/** Groups unsupported items by code in first-occurrence order (stable display). */
export function groupByCode(items: UnsupportedItem[]): UnsupportedGroup[] {
  const groups = new Map<UnsupportedCode, UnsupportedGroup>();
  for (const item of items) {
    let group = groups.get(item.code);
    if (!group) {
      group = { code: item.code, items: [] };
      groups.set(item.code, group);
    }
    group.items.push(item);
  }
  return [...groups.values()];
}

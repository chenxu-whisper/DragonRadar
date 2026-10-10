import Decimal from 'decimal.js';

const Exact = Decimal.clone({ precision: 80, rounding: Decimal.ROUND_HALF_EVEN, toExpNeg: -80, toExpPos: 80 });
const MAX_LEXICAL_LENGTH = 4096;
const MAX_ABSOLUTE_EXPONENT = 1_000_000;
const MAX_PLAIN_EXPONENT = 40;
const MAX_PLAIN_SIGNIFICANT_DIGITS = 48;
const MAX_DISPLAY_LENGTH = 80;
const DECIMAL_TEXT = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e([+-]?\d+))?$/i;
export function decimal(value: string | null | undefined): Decimal | null {
  if (typeof value !== 'string') return null;
  const text = value.trim();
  if (!text || text.length > MAX_LEXICAL_LENGTH) return null;
  const match = DECIMAL_TEXT.exec(text);
  if (!match) return null;
  const exponent = match[1];
  if (exponent) {
    const digits = exponent.replace(/^[+-]/, '');
    if (digits.length > 7 || Math.abs(Number(exponent)) > MAX_ABSOLUTE_EXPONENT) {
      return null;
    }
  }
  try {
    const result = new Exact(text);
    return result.isFinite() && Math.abs(result.e) <= MAX_ABSOLUTE_EXPONENT
      ? result
      : null;
  } catch {
    return null;
  }
}
function grouped(value: Decimal): string {
  if (value.isZero()) return '0';
  if (Math.abs(value.e) > MAX_PLAIN_EXPONENT
    || value.sd() > MAX_PLAIN_SIGNIFICANT_DIGITS) {
    return value.toSignificantDigits(12).toExponential();
  }
  const [whole, fraction] = value.toFixed().split('.');
  const result = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
    + (fraction ? `.${fraction}` : '');
  return result.length <= MAX_DISPLAY_LENGTH
    ? result
    : value.toSignificantDigits(12).toExponential();
}
export function formatDecimal(value: string | null | undefined): string {
  const number = decimal(value);
  return number ? grouped(number) : '未知';
}
export function formatPercent(value: string | null | undefined, signed = true): string {
  const number = decimal(value);
  if (!number) return '未知';
  return `${signed && number.gt(0) ? '+' : ''}${grouped(number.times(100))}%`;
}
export function compareDecimal(a: string | null | undefined, b: string | null | undefined, order: 'asc' | 'desc'): number {
  const left = decimal(a), right = decimal(b);
  if (!left) return right ? 1 : 0;
  if (!right) return -1;
  return left.cmp(right) * (order === 'asc' ? 1 : -1);
}
export function signClass(value: string | null | undefined): string {
  const number = decimal(value);
  return !number ? 'unknown' : number.gt(0) ? 'positive' : number.lt(0) ? 'negative' : 'neutral';
}
export function timestamp(value: string | null): string {
  if (!value || !Number.isFinite(Date.parse(value))) return '未知';
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).format(new Date(value));
}
const labels: Record<string, string> = {
  raw_close: '未复权收盘价', adjusted_close: '复权收盘价', raw_daily_return: '未复权日收益',
  snapshot_summary: '快照摘要', suspension: '停牌', missing_price: '价格缺失',
  missing_amount: '成交额缺失', missing_volume: '成交量缺失', missing_ohlc: '开高低价不完整',
  missing_previous_close: '昨收缺失', effective_state: '有效证券状态缺失',
  unit_mismatch: '单位不一致', unknown_id: '不在快照证券池', duplicate_bar: '行情重复',
  incomplete_pages: '采集分页不完整', calendar_version: '日历版本不一致',
  corporate_action_caution: '昨收取前一交易日，注意公司行动', 'currency/share': '货币/股',
  currency: '货币', share: '股', lot: '手', CNY: '人民币', SSE: '上交所', SZSE: '深交所',
  BSE: '北交所', main: '主板', STAR: '科创板', ChiNext: '创业板',
};
export function label(value: string | null | undefined): string {
  return value ? labels[value] || `代码：${value}` : '未知';
}
export function reasons(values: string[]): string {
  return values.length ? values.map(label).join('；') : '无已报告缺失';
}

import Decimal from 'decimal.js';

const Exact = Decimal.clone({ precision: 80, rounding: Decimal.ROUND_HALF_EVEN, toExpNeg: -80, toExpPos: 80 });
export function decimal(value: string | null | undefined): Decimal | null {
  if (typeof value !== 'string' || !value.trim()) return null;
  try { const result = new Exact(value); return result.isFinite() ? result : null; } catch { return null; }
}
function grouped(value: Decimal): string {
  if (value.e < -40 || value.e > 40 || value.sd() > 48) {
    return value.toSignificantDigits(12).toExponential();
  }
  const [whole, fraction] = value.toFixed().split('.');
  return whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',') + (fraction ? `.${fraction}` : '');
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

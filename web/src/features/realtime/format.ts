import Decimal from 'decimal.js';

const MAX_ABSOLUTE_EXPONENT = 1_000_000;
const MAX_PLAIN_EXPONENT = 40;
const MAX_PLAIN_SIGNIFICANT_DIGITS = 48;
const MAX_DISPLAY_LENGTH = 80;
const DECIMAL_TEXT = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e([+-]?\d+))?$/i;

export function formatRealtimeDecimal(value: string | null): string {
  if (typeof value !== 'string') return '未知';
  const match = DECIMAL_TEXT.exec(value);
  const exponent = match?.[1];
  if (!match || (exponent !== undefined
    && (exponent.replace(/^[+-]/, '').length > 7
      || Math.abs(Number(exponent)) > MAX_ABSOLUTE_EXPONENT))) {
    return '未知';
  }
  try {
    const number = new Decimal(value);
    if (!number.isFinite() || Math.abs(number.e) > MAX_ABSOLUTE_EXPONENT) return '未知';
    if (number.isZero()) return '0';
    if (Math.abs(number.e) > MAX_PLAIN_EXPONENT
      || number.sd() > MAX_PLAIN_SIGNIFICANT_DIGITS) {
      return number.toSignificantDigits(12).toExponential();
    }
    const [whole, fraction] = number.toFixed().split('.');
    const displayed = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
      + (fraction ? `.${fraction}` : '');
    return displayed.length <= MAX_DISPLAY_LENGTH
      ? displayed
      : number.toSignificantDigits(12).toExponential();
  } catch {
    return '未知';
  }
}

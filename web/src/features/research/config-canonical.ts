/** Python model JSON parity: sorted ASCII, explicit defaults, exact decimals. */
import Decimal from 'decimal.js';

export function asciiCanonical(value: unknown): string {
  if (value === null || typeof value === 'boolean' || typeof value === 'string') {
    return JSON.stringify(value).replace(/[\u007f-\uffff]/g,
      char => `\\u${char.charCodeAt(0).toString(16).padStart(4, '0')}`);
  }
  if (typeof value === 'number' && Number.isSafeInteger(value)) return String(value);
  if (Array.isArray(value)) return `[${value.map(asciiCanonical).join(',')}]`;
  if (typeof value === 'object' && value !== null) {
    const data = value as Record<string, unknown>;
    return `{${Object.keys(data).sort().map(key =>
      `${asciiCanonical(key)}:${asciiCanonical(data[key])}`).join(',')}}`;
  }
  throw new Error('invalid canonical JSON');
}

export function pythonDecimal(value: unknown): string {
  if (typeof value === 'number' && Number.isSafeInteger(value)) value = String(value);
  if (typeof value !== 'string') throw new Error('exact decimal required');
  const match = /^([+-]?)(\d+)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/.exec(value.trim());
  if (!match) throw new Error('invalid decimal');
  const exponent = Number(match[4] ?? 0) - (match[3]?.length ?? 0);
  if (!Number.isSafeInteger(exponent) || Math.abs(exponent) > 1000) throw new Error('decimal exponent');
  const digits = (match[2] + (match[3] ?? '')).replace(/^0+/, '') || '0';
  const sign = match[1] === '-' ? '-' : '';
  const adjusted = digits.length + exponent - 1;
  if (exponent > 0 || adjusted < -6) {
    return sign + digits[0] + (digits.length > 1 ? `.${digits.slice(1)}` : '')
      + `E${adjusted >= 0 ? '+' : ''}${adjusted}`;
  }
  if (exponent === 0) return sign + digits;
  const position = digits.length + exponent;
  return sign + (position > 0
    ? `${digits.slice(0, position)}.${digits.slice(position)}`
    : `0.${'0'.repeat(-position)}${digits}`);
}

export function normalizedConfig(value: Record<string, unknown>): Record<string, unknown> {
  const data = structuredClone(value);
  const parameters = data.parameters as Record<string, unknown>;
  const cost = data.cost_model as Record<string, unknown>;
  const execution = data.execution_model as Record<string, unknown>;
  data.schema_version ??= 'dragon-radar.research-backtest/v1';
  data.walk_forward ??= null;
  parameters.factor_id ??= 'FAC-R01';
  parameters.weighting ??= 'equal_weight';
  cost.slippage_model ??= 'fixed_rate';
  cost.stamp_tax_side ??= 'sell';
  execution.fill_timing ??= 'next_tradable_point';
  execution.unknown_limit_policy ??= 'reject_untradable';
  for (const key of ['commission_rate', 'stamp_tax_rate', 'slippage_rate', 'min_commission']) {
    cost[key] = pythonDecimal(cost[key]);
    const number = new Decimal(cost[key] as string);
    if (!number.isPositive() || number.gt(key === 'min_commission' ? 100 : '0.01')) {
      throw new Error('fee bounds');
    }
  }
  return data;
}

export function canonicalConfig(value: Record<string, unknown>): string {
  return asciiCanonical(normalizedConfig(value));
}

export async function configHash(value: Record<string, unknown>): Promise<string> {
  const bytes = new TextEncoder().encode(canonicalConfig(value));
  const hash = await crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(hash), byte => byte.toString(16).padStart(2, '0')).join('');
}

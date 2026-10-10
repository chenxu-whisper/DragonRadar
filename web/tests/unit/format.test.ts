import { describe, expect, it } from 'vitest';
import { formatDecimal, formatPercent, compareDecimal, signClass } from '../../src/format';

describe('exact decimal display', () => {
  it.each([null, undefined, '', 'NaN', 'Infinity', 'abc'])('marks %s unknown, never zero', value => {
    expect(formatDecimal(value)).toBe('未知');
    expect(formatPercent(value)).toBe('未知');
  });
  it('preserves integers beyond JavaScript precision', () => {
    expect(formatDecimal('9007199254740993.1234')).toBe('9,007,199,254,740,993.1234');
  });
  it('converts fractions to signed percent without Number', () => {
    expect(formatPercent('0.012345')).toBe('+1.2345%');
    expect(formatPercent('-0.00001')).toBe('-0.001%');
    expect(formatPercent('0')).toBe('0%');
  });
  it('does not round small nonzero values to zero', () => {
    expect(formatDecimal('0.00000000001')).toBe('0.00000000001');
  });
  it('bounds extreme exponents and coefficients with scientific notation', () => {
    const started = performance.now();
    const outputs = [
      formatDecimal('1e100000'),
      formatDecimal('-1e100000'),
      formatDecimal('1e-100000'),
      formatDecimal('-1e-100000'),
      formatDecimal('9'.repeat(1_000)),
    ];

    expect(outputs).toEqual(outputs.map(output => expect.stringMatching(/^-?\d(?:\.\d+)?e[+-]\d+$/)));
    expect(outputs.every(output => output.length <= 80)).toBe(true);
    expect(performance.now() - started).toBeLessThan(2_000);
  }, 30_000);
  it('keeps normal, negative-zero, and percent formatting semantics under the bound', () => {
    expect(formatDecimal('1234567.8900')).toBe('1,234,567.89');
    expect(formatDecimal('-0')).toBe('0');
    expect(formatPercent('0.012345')).toBe('+1.2345%');
    expect(formatPercent('-0')).toBe('0%');
    expect(formatPercent('1e100000')).toMatch(/^\+\d(?:\.\d+)?e\+\d+%$/);
    expect(formatPercent('1e100000').length).toBeLessThanOrEqual(81);
  });
  it('keeps null distinct from lexical zero', () => {
    expect(formatDecimal(null)).toBe('未知');
    expect(formatDecimal('0')).toBe('0');
    expect(formatDecimal('-0')).toBe('0');
  });
  it('sorts close exact numbers distinctly and nulls last in either order', () => {
    expect(compareDecimal('9007199254740993', '9007199254740992', 'asc')).toBe(1);
    expect(compareDecimal(null, '1', 'desc')).toBe(1);
    expect(compareDecimal('1', null, 'asc')).toBe(-1);
  });
  it('uses semantic sign classes without treating unknown as neutral', () => {
    expect(signClass(null)).toBe('unknown');
    expect(signClass('-0.1')).toBe('negative');
    expect(signClass('0.1')).toBe('positive');
    expect(signClass('0')).toBe('neutral');
  });
});

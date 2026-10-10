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
  it('bounds exponent expansion and total display length', () => {
    expect(formatDecimal('1e100000')).toBe('1e+100000');
    expect(formatDecimal('-1e100000')).toBe('-1e+100000');
    expect(formatDecimal('1e-100000')).toBe('1e-100000');
    expect(formatDecimal('-1e-100000')).toBe('-1e-100000');
    expect(formatDecimal('1e1000001')).toBe('未知');
    expect(formatDecimal('1e-1000001')).toBe('未知');
    expect(formatDecimal(`${'9'.repeat(100)}e1000000`)).toBe('未知');
    expect(formatDecimal(`0.${'0'.repeat(100)}1e-1000000`)).toBe('未知');
    for (const value of ['1e60', '1e-60', '1234567890'.repeat(40)]) {
      expect(formatDecimal(value).length).toBeLessThanOrEqual(80);
      expect(formatDecimal(value)).toMatch(/e[+-]\d+$/);
    }
  });
  it('keeps null distinct from lexical zero under bounded formatting', () => {
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

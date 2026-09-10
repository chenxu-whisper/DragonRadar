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

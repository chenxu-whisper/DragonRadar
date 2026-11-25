import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import StockHistoryTable from '../../src/features/workbench/StockHistoryTable';
import type { ResetKlinePoint } from '../../src/types';

const point = {
  trade_date: '2026-09-14', open: 11, high: 12, low: 10, close: 11,
  volume: null, amount: null,
  currency: 'USD', price_unit: 'USD/share', volume_unit: 'lot', amount_unit: 'USD',
};
const render = (points: ResetKlinePoint[]) =>
  renderToStaticMarkup(createElement(StockHistoryTable, { points }));

describe('B8 raw stock history unit context', () => {
  it('retains actual nondefault units in each record', () => {
    const html = render([point]);
    expect(html).toContain('USD/share');
    expect(html).toContain('lot');
    expect(html).toContain('data-label="币种 / 单位"');
    expect(html).not.toContain('CNY');
  });

  it('does not merge mixed daily units or substitute defaults for absent metadata', () => {
    const html = render([point, {
      ...point, trade_date: '2026-09-15', currency: 'EUR', price_unit: 'EUR/unit',
      volume_unit: 'contract', amount_unit: 'EUR',
    }, {
      trade_date: '2026-09-16', open: 0, high: 0, low: 0, close: 0, volume: 0, amount: 0,
    }]);
    expect(html).toContain('USD/share');
    expect(html).toContain('EUR/unit');
    expect(html).toContain('contract');
    expect(html).toContain('单位未知');
    expect(html).not.toContain('currency/share');
  });

  it('preserves null markers and numerical zero distinctly', () => {
    const html = render([point, { ...point, trade_date: '2026-09-15', volume: 0, amount: 0 }]);
    expect(html).toContain('data-label="成交量">—</td>');
    expect(html).toContain('data-label="成交额">—</td>');
    expect(html).toContain('data-label="成交量">0</td>');
    expect(html).toContain('data-label="成交额">0</td>');
  });
});

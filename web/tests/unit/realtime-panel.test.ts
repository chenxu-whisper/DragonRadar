import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import {
  FlowLedger,
  MinuteLedger,
  RealtimeResourceState,
} from '../../src/features/realtime/RealtimePanel';
import { formatRealtimeDecimal } from '../../src/features/realtime/format';
import type {
  MinuteResponse,
  RealtimeEnvelope,
  SectorFlowResponse,
} from '../../src/features/realtime/types';

function response(
  data: { status: 'incomplete' } | null,
  qualityFlags: string[],
): RealtimeEnvelope<{ status: 'incomplete' }> {
  return {
    trade_date: '2026-10-09',
    as_of: '2026-10-09T02:00:00Z',
    snapshot_id: null,
    source: 'licensed-feed',
    source_version: 'v1',
    coverage: { expected: 3, valid: 2, excluded: 0, missing: 1 },
    quality_flags: qualityFlags,
    rule_version: null,
    basis: {
      unit: null,
      currency: null,
      price_type: 'raw',
      benchmark: null,
    },
    data,
  };
}

describe('RealtimeResourceState', () => {
  it.each(['实时报价', '分钟记录', '异动证据', '分时资金流'])(
    'renders unavailable evidence for null %s data',
    name => {
      const markup = renderToStaticMarkup(createElement(RealtimeResourceState, {
        name,
        response: response(null, ['no_data']),
      }));

      expect(markup).toContain(`${name}状态`);
      expect(markup).toContain('不可用');
      expect(markup).toContain('有效 2/3');
      expect(markup).toContain('缺失 1');
      expect(markup).toContain('质量标记：no_data');
      expect(markup).toContain('无可用数据');
    },
  );

  it('renders partial status and quality evidence without adding a card', () => {
    const markup = renderToStaticMarkup(createElement(RealtimeResourceState, {
      name: '分钟记录',
      response: response({ status: 'incomplete' }, ['stream_gap']),
    }));

    expect(markup).toContain('部分');
    expect(markup).toContain('质量标记：stream_gap');
    expect(markup).toContain('class="leader-state');
    expect(markup).not.toContain('class="card');
    expect(markup).not.toContain('无可用数据');
  });
});

describe('realtime Decimal display', () => {
  it('keeps ordinary precision exact while bounding extreme wire values', () => {
    expect(formatRealtimeDecimal('9007199254740993.1234'))
      .toBe('9,007,199,254,740,993.1234');
    for (const value of ['1e100000', '1e-100000', '1234567890'.repeat(40)]) {
      expect(formatRealtimeDecimal(value).length).toBeLessThanOrEqual(80);
    }
    expect(formatRealtimeDecimal(`1e${'9'.repeat(46)}`)).toBe('未知');
    expect(formatRealtimeDecimal(null)).toBe('未知');
  });
});

describe('MinuteLedger', () => {
  function minuteResponse(sources: string[]): MinuteResponse {
    return {
      ...response(null, []),
      source: sources.length === 1 ? sources[0] : null,
      source_version: sources.length === 1 ? 'v1' : null,
      coverage: {
        expected: sources.length,
        valid: sources.length,
        excluded: 0,
        missing: 0,
      },
      data: {
        status: 'complete',
        bars: sources.map((source, index) => ({
          batch_id: `batch-${index + 1}`,
          source_id: source,
          source_version: 'v1',
          security_id: '600000.SH',
          event_at: '2026-10-09T01:30:00Z',
          received_at: '2026-10-09T01:30:00Z',
          available_at: '2026-10-09T01:30:00Z',
          open: String(10 + index),
          high: String(10 + index),
          low: String(10 + index),
          close: String(10 + index),
          volume: '0',
          amount: '0',
          revision: 1,
          final: true,
        })),
      },
    };
  }

  it('keeps same-minute evidence from each source in the chart and audit table', () => {
    const markup = renderToStaticMarkup(createElement(MinuteLedger, {
      response: minuteResponse(['alpha-feed', 'beta-feed']),
    }));

    expect(markup).toContain('按来源拆分');
    expect(markup).toContain('alpha-feed');
    expect(markup).toContain('beta-feed');
    expect(markup).toContain('batch-1');
    expect(markup).toContain('batch-2');
    expect(markup.match(/2026-10-09T01:30:00Z/g)).toHaveLength(2);
  });

  it('retains the single-source minute ledger', () => {
    const markup = renderToStaticMarkup(createElement(MinuteLedger, {
      response: minuteResponse(['licensed-feed']),
    }));

    expect(markup).toContain('licensed-feed');
    expect(markup).toContain('batch-1');
    expect(markup).toContain('>10<');
  });
});

describe('FlowLedger', () => {
  it('explains mixed units and keeps every point unit in the audit table', () => {
    const flow: SectorFlowResponse = {
      ...response(null, []),
      basis: {
        unit: null,
        currency: null,
        price_type: 'raw',
        benchmark: null,
      },
      coverage: { expected: 2, valid: 2, excluded: 0, missing: 0 },
      data: {
        status: 'complete',
        points: [
          {
            batch_id: 'batch-1',
            source_id: 'licensed-feed',
            source_version: 'v1',
            sector: 'sector-1',
            event_at: '2026-10-09T01:30:00Z',
            available_at: '2026-10-09T01:30:00Z',
            net_inflow: '0',
            unit: 'CNY',
            unavailable_reason: null,
          },
          {
            batch_id: 'batch-1',
            source_id: 'licensed-feed',
            source_version: 'v1',
            sector: 'sector-1',
            event_at: '2026-10-09T01:31:00Z',
            available_at: '2026-10-09T01:31:00Z',
            net_inflow: '2',
            unit: 'USD',
            unavailable_reason: null,
          },
        ],
      },
    };

    const markup = renderToStaticMarkup(createElement(FlowLedger, { response: flow }));

    expect(markup).toContain('混合单位');
    expect(markup).toContain('按单位拆分');
    expect(markup).toContain('aria-label="题材分时资金流审计记录"');
    expect(markup).toContain('CNY');
    expect(markup).toContain('USD');
    expect(markup).toContain('>0<');
    expect(markup).toContain('>2<');
  });
});

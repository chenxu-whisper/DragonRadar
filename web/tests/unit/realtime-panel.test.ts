import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { RealtimeResourceState } from '../../src/features/realtime/RealtimePanel';
import type { RealtimeEnvelope } from '../../src/features/realtime/types';

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

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { SnapshotEvidence } from '../../src/components/Common';
import type { Snapshot } from '../../src/types';

describe('snapshot evidence copy', () => {
  it('states explicitly that unknown quality does not mean no risk', () => {
    const snapshot = {
      snapshot_id: 'snapshot-a',
      as_of: '2026-09-10T10:00:00Z',
      source_time: '2026-09-10T09:00:00Z',
      rule_version: 'v1',
      quality_flags: ['synthetic_quality_flag'],
      units: {
        currency: ['CNY'],
        price_unit: ['currency/share'],
        volume_unit: ['share'],
        amount_unit: ['currency'],
      },
      result: {
        published_at: '2026-09-10T11:00:00Z',
        earliest_date: '2026-09-01',
        supersedes: null,
      },
    } as unknown as Snapshot;

    expect(renderToStaticMarkup(createElement(SnapshotEvidence, { snapshot })))
      .toContain('未知不代表无风险');
  });
});

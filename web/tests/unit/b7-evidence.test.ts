import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { BacktestResultView, BacktestConfigForm, emptyBacktestDraft } from '../../src/features/research/BacktestPanel';
import type { BacktestRunRecord } from '../../src/features/research/backtest-client';
const root = resolve('..');
const golden = JSON.parse(execFileSync(resolve(root, '.venv/Scripts/python.exe'), ['-c',
  'import sys,json;sys.path.insert(0,"tests");from test_b7_result_contracts import actual_result;from test_b7_materialization import materialized;c,r=actual_result();v,x=materialized();print(json.dumps({"config":c.model_dump(mode="json"),"result":r,"v2config":v.model_dump(mode="json"),"v2":x}))',
], { cwd: root, env: { ...process.env, PYTHONPATH: resolve(root, 'src') }, encoding: 'utf8', timeout: 15000 }));
function record(v2 = false): BacktestRunRecord {
  const result = v2 ? golden.v2 : golden.result;
  return { run_id: '9ab0b878-5c20-449e-bd1b-ae3d96f4eb49', status: 'succeeded',
    config_hash: result.config_hash, research_only: true, result, failure: null,
    config_context: v2 ? golden.v2config : golden.config };
}
describe('BacktestResultView evidence', () => {
  it('does not enable enumerated production extensions without capabilities', () => {
    const html = renderToStaticMarkup(createElement(BacktestConfigForm, {
      draft: emptyBacktestDraft(), errors: {}, busy: false, manifestReady: true,
      onDraft: () => {}, onSubmit: () => {}, onCancel: () => {},
    }));
    expect(html).toContain('approved_method_unavailable');
    expect(html).toMatch(/<option[^>]*disabled=""[^>]*value="FAC-R02"/);
  });
  it('requires config and rejects malformed arrays before rendering numbers', () => {
    const missing = record(); delete missing.config_context;
    expect(renderToStaticMarkup(createElement(BacktestResultView, { record: missing }))).toContain('config_context_unavailable');
    const corrupt = record(); corrupt.result = { ...corrupt.result, trades: {} };
    expect(renderToStaticMarkup(createElement(BacktestResultView, { record: corrupt }))).toContain('malformed_response');
  });
  it('distinguishes full health from daily quality and legacy unavailable', () => {
    const legacy = renderToStaticMarkup(createElement(BacktestResultView, { record: record() }));
    expect(legacy).toContain('legacy/unverified');
    expect(legacy).toContain('factor_health_inputs_unavailable');
    const current = renderToStaticMarkup(createElement(BacktestResultView, { record: record(true) }));
    expect(current).toContain('Factor health');
    expect(current).toContain('Daily factor coverage and quality');
    expect(current).toContain('benchmark_inputs_unavailable');
    expect(current).toContain('factor-health-decimal34-v1');
    expect(current).toContain(golden.v2.lineage.frames[0].frame_sha256);
    expect(current).not.toContain('data-metric="benchmark_return"');
  });
});

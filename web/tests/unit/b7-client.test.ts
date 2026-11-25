import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { getBacktestConfig, getBacktestRun, getResearchCapabilities, researchRequest } from '../../src/features/research/backtest-client';
const root = resolve('..');
const golden = JSON.parse(execFileSync(resolve(root, '.venv/Scripts/python.exe'), ['-c',
  'import sys,json;sys.path.insert(0,"tests");from test_b7_result_contracts import actual_result;c,r=actual_result();print(json.dumps({"config":c.model_dump(mode="json"),"result":r,"hash":c.config_hash}))',
], { cwd: root, env: { ...process.env, PYTHONPATH: resolve(root, 'src') }, encoding: 'utf8', timeout: 15000 }));
const id = '9ab0b878-5c20-449e-bd1b-ae3d96f4eb49';
afterEach(() => vi.unstubAllGlobals());
describe('owner frozen config client', () => {
  it('reads only exact window-scoped capability query', async () => {
    const capability = (id: string, reason: string) => ({ id, status: 'disabled', reason, method_version: null, approval_evidence_sha256: null });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      snapshot_id: golden.config.snapshot_id, period: golden.config.period,
      factors: ['FAC-R01', 'FAC-R02', 'FAC-R03', 'FAC-C01', 'FAC-C02'].map(x => capability(x, 'approved_method_unavailable')),
      walk_forward: capability('walk-forward-anchored-v1', 'phase_series_unavailable'),
      ai_variant: capability('ai_variant', 'real_evaluation_inputs_unavailable'),
    }))));
    expect((await getResearchCapabilities(golden.config.snapshot_id, golden.config.period)).ai_variant.status).toBe('disabled');
    await expect(researchRequest('/api/research/capabilities?snapshot_id=x&start=x&end=x&owner=x')).rejects.toThrow();
  });
  it('loads authoritative config and deeply validates exact EX15', async () => {
    const fetcher = vi.fn((path: string) => Promise.resolve(new Response(JSON.stringify(path.endsWith('/config')
      ? { run_id: id, config_hash: golden.hash, config: golden.config }
      : { run_id: id, status: 'succeeded', config_hash: golden.hash, research_only: true, result: golden.result, failure: null }))));
    vi.stubGlobal('fetch', fetcher);
    expect((await getBacktestRun(id)).config_context).toEqual(golden.config);
    expect(fetcher.mock.calls.map(x => x[0])).toContain(`/api/research/backtests/${id}/config`);
  });
  it('refuses wrong config and recognizes DATA_UNAVAILABLE', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ run_id: id, config_hash: 'a'.repeat(64), config: golden.config }))));
    await expect(getBacktestConfig(id)).rejects.toThrow();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: 'DATA_UNAVAILABLE' }), { status: 503 })));
    await expect(researchRequest(`/api/research/backtests/${id}`)).rejects.toMatchObject({ code: 'DATA_UNAVAILABLE' });
  });
});

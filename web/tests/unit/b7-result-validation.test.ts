import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { validateResearchResult, validateResearchEnvelope } from '../../src/features/research/result-validation';

const root = resolve('..');
const golden = JSON.parse(execFileSync(resolve(root, '.venv/Scripts/python.exe'), ['-c',
  'import sys,json;sys.path.insert(0,"tests");from test_b7_result_contracts import actual_result;from test_b7_materialization import materialized;c,r=actual_result();v,x=materialized();print(json.dumps({"config":c.model_dump(mode="json"),"result":r,"v2config":v.model_dump(mode="json"),"v2":x,"hash":c.config_hash}))',
], { cwd: root, env: { ...process.env, PYTHONPATH: resolve(root, 'src') }, encoding: 'utf8', timeout: 15000 }));
const id = '9ab0b878-5c20-449e-bd1b-ae3d96f4eb49';

describe('validateResearchResult', () => {
  it('validates actual Python v1 and v2 artifacts', () => {
    expect(validateResearchResult(golden.result, golden.config, golden.hash).metrics).toEqual(golden.result.metrics);
    expect(validateResearchResult(golden.v2, golden.v2config, golden.v2.config_hash).lineage).toEqual(golden.v2.lineage);
  });
  it.each(['float', 'extra', 'cash', 'manifest', 'array', 'version', 'ai', 'health', 'sequence'])('rejects %s tampering', mutation => {
    const v2 = mutation === 'health' || mutation === 'sequence';
    const result = structuredClone(v2 ? golden.v2 : golden.result);
    if (mutation === 'float') result.metrics.total_return = 0.01;
    if (mutation === 'extra') result.metrics.uplift = '1.000000';
    if (mutation === 'cash') result.trades[0].net_cash_delta = '0.00';
    if (mutation === 'manifest') result.manifests.domains[0].snapshot_id = id;
    if (mutation === 'array') result.trades = {};
    if (mutation === 'version') result.versions.cost_method = 'guessed';
    if (mutation === 'ai') result.ablation.ai_variant.metrics = result.metrics;
    if (mutation === 'health') result.health.report.factors[0].valid_rate = '0.000000';
    if (mutation === 'sequence') result.lineage.frames[0].publication_sequence = 99999;
    expect(() => validateResearchResult(result, v2 ? golden.v2config : golden.config, result.config_hash)).toThrow();
  });
  it('requires immutable context and matching run ID/status', async () => {
    const envelope = {
      run_id: id, status: 'succeeded', config_hash: golden.hash, research_only: true,
      result: golden.result, failure: null,
    };
    expect(() => validateResearchResult(golden.result, null, golden.hash)).toThrow('config_context_unavailable');
    await expect(validateResearchEnvelope(envelope, golden.config, id)).resolves.toMatchObject({ status: 'succeeded' });
    await expect(validateResearchEnvelope(envelope, golden.config, 'wrong')).rejects.toThrow();
    await expect(validateResearchEnvelope({ ...envelope, status: 'running' }, golden.config, id)).rejects.toThrow();
  });
});

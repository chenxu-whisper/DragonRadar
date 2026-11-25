import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { canonicalConfig, asciiCanonical, configHash } from '../../src/features/research/config-canonical';

const root = resolve('..');
const golden = JSON.parse(execFileSync(resolve(root, '.venv/Scripts/python.exe'), ['-c',
  'import sys,json;sys.path.insert(0,"tests");from test_b7_result_contracts import actual_result;c,r=actual_result();print(json.dumps({"config":c.model_dump(mode="json"),"canonical":c.canonical_json(),"hash":c.config_hash}))',
], { cwd: root, env: { ...process.env, PYTHONPATH: resolve(root, 'src') }, encoding: 'utf8', timeout: 15000 }));

describe('canonicalConfig', () => {
  it('matches Python canonical bytes and SHA256 without Number conversion', async () => {
    expect(canonicalConfig(golden.config)).toBe(golden.canonical);
    expect(await configHash(golden.config)).toBe(golden.hash);
  });
  it('normalizes defaults and ASCII supplementary code points', () => {
    const partial = structuredClone(golden.config);
    delete partial.execution_model.fill_timing;
    expect(canonicalConfig(partial)).toBe(golden.canonical);
    expect(asciiCanonical({ title: '\u4e2d\ud83d\ude00' })).toBe('{"title":"\\u4e2d\\ud83d\\ude00"}');
  });
  it('rejects imprecise floating fee inputs', () => {
    const value = structuredClone(golden.config);
    value.cost_model.commission_rate = 0.0003;
    expect(() => canonicalConfig(value)).toThrow();
  });
});

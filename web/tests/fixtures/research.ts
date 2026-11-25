import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';

/** Synthetic engineering capability evidence; never a production approval. */
export function researchCapabilities(snapshotId: string, period: { start: string; end: string }) {
  const disabled = (id: string, reason: string) => ({
    id, status: 'disabled', reason, method_version: null, approval_evidence_sha256: null,
  });
  return {
    snapshot_id: snapshotId, period,
    factors: ['FAC-R01', 'FAC-R02', 'FAC-R03', 'FAC-C01', 'FAC-C02'].map(id =>
      id === 'FAC-R01'
        ? { id, status: 'ready', reason: null, method_version: 'synthetic-engineering-v1',
            approval_evidence_sha256: 'e'.repeat(64) }
        : disabled(id, 'approved_method_unavailable')),
    walk_forward: disabled('walk-forward-anchored-v1', 'phase_series_unavailable'),
    ai_variant: disabled('ai_variant', 'real_evaluation_inputs_unavailable'),
  };
}

/** Execute the deterministic Python kernel using the browser's submitted config. */
export function materializedResearch(body: Record<string, unknown>) {
  const root = resolve('..');
  return JSON.parse(execFileSync(resolve(root, '.venv/Scripts/python.exe'), ['-c', [
    'import sys,json;sys.path.insert(0,"tests")',
    'from test_reset_backtest import BacktestConfig,_eng_frames,_factor_frames,_run_engine,ENG_DAYS,ENG_FLAT,ENG_VALUES_BY_DAY',
    'from dragon_radar.research.result_contracts import validate_result',
    'body=json.load(sys.stdin);body.pop("idempotency_key",None)',
    'c=BacktestConfig.model_validate_json(json.dumps(body))',
    'r=_run_engine(c,_eng_frames({d:dict(ENG_FLAT) for d in ENG_DAYS}),_factor_frames(ENG_VALUES_BY_DAY)).model_dump(mode="json")',
    'validate_result(r,c)',
    'print(json.dumps({"config":c.model_dump(mode="json"),"hash":c.config_hash,"result":r}))',
  ].join('\n')], {
    cwd: root, env: { ...process.env, PYTHONPATH: resolve(root, 'src') },
    input: JSON.stringify(body), encoding: 'utf8', timeout: 15000,
  }));
}

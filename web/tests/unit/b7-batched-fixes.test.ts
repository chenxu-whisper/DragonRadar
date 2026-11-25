import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, expect, it, vi } from 'vitest';
import { cancelBacktestRun, getBacktestConfig, getBacktestRun } from '../../src/features/research/backtest-client';
import { validateResearchResult } from '../../src/features/research/result-validation';

const root = resolve('..');
const golden = JSON.parse(execFileSync(resolve(root, '.venv/Scripts/python.exe'), ['-B', '-c',
  'import sys,json;sys.path.insert(0,"tests");from test_b7_batched_fixes import folded_materialized;c,r=folded_materialized();print(json.dumps({"config":c.model_dump(mode="json"),"result":r,"hash":c.config_hash}))',
], { cwd: root, env: { ...process.env, PYTHONPATH: resolve(root, 'src') }, encoding: 'utf8', timeout: 15000 }));
const id = '9ab0b878-5c20-449e-bd1b-ae3d96f4eb49';
afterEach(() => vi.unstubAllGlobals());

it.each(['trades', 'rejections'])('F3 rejects unarchived fold %s in the browser', kind => {
  const result = structuredClone(golden.result);
  expect(() => validateResearchResult(result, golden.config, golden.hash)).not.toThrow();
  const fold = result.walk_forward.folds[0];
  if (kind === 'trades') fold.trades[0].security_id = 'UNARCHIVED';
  else {
    const trade = fold.trades[0];
    fold.rejections.push({ security_id: 'UNARCHIVED', side: trade.side, quantity: trade.quantity,
      signal_date: trade.signal_date, trade_date: trade.trade_date, reason: 'suspended' });
  }
  result.folds = structuredClone(result.walk_forward.folds);
  expect(() => validateResearchResult(result, golden.config, golden.hash)).toThrow();
});

it.each(['read', 'config', 'cancel'])('F4 %s rejects abort during async digest validation', async operation => {
  const controller = new AbortController();
  let release!: (value: ArrayBuffer) => void;
  const hashBytes = Uint8Array.from(golden.hash.match(/../g)!.map((x: string) => parseInt(x, 16))).buffer;
  const digest = vi.fn().mockImplementationOnce(() => new Promise<ArrayBuffer>(resolve => { release = resolve; }))
    .mockImplementation(() => Promise.resolve(hashBytes));
  vi.stubGlobal('crypto', { subtle: { digest }, randomUUID: () => id });
  vi.stubGlobal('fetch', vi.fn((path: string) => Promise.resolve(new Response(JSON.stringify(
    path.endsWith('/config') ? { run_id: id, config_hash: golden.hash, config: golden.config }
      : { run_id: id, status: operation === 'cancel' ? 'cancelled' : 'running',
        config_hash: golden.hash, research_only: true, result: null, failure: null },
  )))));
  const pending = operation === 'read' ? getBacktestRun(id, controller.signal, golden.config)
    : operation === 'config' ? getBacktestConfig(id, controller.signal) : cancelBacktestRun(id, controller.signal);
  const assertion = expect(pending).rejects.toMatchObject({ name: 'AbortError' });
  await vi.waitFor(() => expect(digest).toHaveBeenCalled());
  controller.abort();
  release(hashBytes);
  await assertion;
});

it('F4 panel invalidates cancel/retry epochs and guards aborted continuations', () => {
  const source = readFileSync(resolve('src/features/research/BacktestPanel.tsx'), 'utf8');
  const cancel = source.slice(source.indexOf('const handleCancel'), source.indexOf('return <section className="backtest-panel"'));
  expect(cancel).toMatch(/generation\.current\s*\+=\s*1/);
  const refresh = source.slice(source.indexOf('const refreshRecord'), source.indexOf('const manifestFingerprint'));
  expect(refresh).toContain('!controller.signal.aborted');
  expect(source).toMatch(/refreshRecord\(record.run_id,\s*true\)/);
});

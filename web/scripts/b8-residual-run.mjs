import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdirSync, readFileSync, writeFileSync, readdirSync } from 'node:fs';
import { resolve } from 'node:path';

const root = resolve('..');
const output = resolve(root, '.temp/b8-residual', `run-${new Date().toISOString().replace(/[:.]/g, '-')}`);
mkdirSync(output, { recursive: true });
const git = args => spawnSync('git', args, { cwd: root, encoding: 'utf8' }).stdout.trim();
const paths = [...new Set([
  ...git(['ls-files', 'web/src', 'src/dragon_radar', 'web/tests/fixtures', 'web/scripts/contract-c2-support.mjs']).split(/\r?\n/),
  ...git(['ls-files', '--others', '--exclude-standard', 'web/src', 'src/dragon_radar']).split(/\r?\n/),
  'web/playwright.b8-residual.config.ts', 'web/tests/e2e/b8-residual.spec.ts',
  'web/tests/fixtures/b8-residual.ts', 'web/scripts/b8-residual-run.mjs',
  'web/tests/unit/b8-stock-history.test.ts',
  '.temp/b8-residual/b8-q04-probe.py',
  '.temp/b8-residual/b8-d04-operator.py',
])].filter(Boolean).sort();
const hashes = () => Object.fromEntries(paths.map(path => [
  path, createHash('sha256').update(readFileSync(resolve(root, path))).digest('hex'),
]));
const argv = ['node_modules/@playwright/test/cli.js', 'test',
  '--config', 'playwright.b8-residual.config.ts', ...process.argv.slice(2)];
const before = hashes();
const started = new Date().toISOString();
const typecheck = spawnSync(process.execPath, ['node_modules/typescript/bin/tsc', '--noEmit'],
  { encoding: 'utf8', maxBuffer: 8 * 1024 * 1024 });
writeFileSync(resolve(output, 'b8-typescript.stdout.log'), typecheck.stdout ?? '');
writeFileSync(resolve(output, 'b8-typescript.stderr.log'), typecheck.stderr ?? '');
const probe = spawnSync(resolve(root, '.venv/Scripts/python.exe'),
  ['.temp/b8-residual/b8-q04-probe.py'], { cwd: root, encoding: 'utf8',
    env: { ...process.env, PYTHONPATH: 'src;tests' } });
writeFileSync(resolve(output, 'b8-q04-probe.json'), probe.stdout ?? '');
writeFileSync(resolve(output, 'b8-q04-probe.stderr.log'), probe.stderr ?? '');
if (probe.status !== 0) {
  console.error(probe.stderr);
  throw new Error(`B8 real-kernel prerequisite failed: ${probe.status}; ${output}`);
}
const run = spawnSync(process.execPath, argv, { encoding: 'utf8', maxBuffer: 32 * 1024 * 1024,
  env: { ...process.env, B8_OUTPUT_DIR: output } });
writeFileSync(resolve(output, 'browser.stdout.log'), run.stdout ?? '');
writeFileSync(resolve(output, 'browser.stderr.log'), run.stderr ?? '');
const after = hashes();
const sourceChanged = paths.filter(path => before[path] !== after[path]);
let stats = null;
try { stats = JSON.parse(readFileSync(resolve(output, 'browser.json'), 'utf8')).stats; } catch {}
const artifactHashes = directory => Object.fromEntries(readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
  const path = resolve(directory, entry.name);
  return entry.isDirectory() ? Object.entries(artifactHashes(path))
    : [[path, createHash('sha256').update(readFileSync(path)).digest('hex')]];
}));
writeFileSync(resolve(output, 'b8-run-receipt.json'), JSON.stringify({
  command: [process.execPath, ...argv], cwd: process.cwd(), started,
  ended: new Date().toISOString(), exit: run.status, error: run.error?.message,
  commit: git(['rev-parse', 'HEAD']), flags: { reset: true, privateReview: false },
  origin: 'http://127.0.0.1:5184', syntheticHTTP: true,
  stats, sourceChanged, before, after, typecheckExit: typecheck.status,
  artifactHashes: artifactHashes(output),
}, null, 2));
console.log(run.stdout);
console.error(run.stderr);
console.log(JSON.stringify({ output, exit: run.status, typecheckExit: typecheck.status, stats, sourceChanged }));
process.exitCode = run.status || typecheck.status || 0;

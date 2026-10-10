import { readdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { spawnSync } from 'node:child_process';

export function testPlan(args, nodeTests) {
  if (args.length > 0) return [{ runner: 'vitest', args }];
  return [
    { runner: 'vitest', args: ['run'] },
    { runner: 'node', args: ['--test', ...nodeTests] },
  ];
}

function run(step) {
  const vitest = resolve('node_modules/vitest/vitest.mjs');
  const args = step.runner === 'vitest' ? [vitest, ...step.args] : step.args;
  const result = spawnSync(process.execPath, args, { stdio: 'inherit' });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status ?? 1);
}

function main() {
  const nodeTests = readdirSync('scripts')
    .filter(name => name.endsWith('.test.mjs'))
    .sort()
    .map(name => `scripts/${name}`);
  testPlan(process.argv.slice(2), nodeTests).forEach(run);
}

if (process.argv[1]
  && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main();
}

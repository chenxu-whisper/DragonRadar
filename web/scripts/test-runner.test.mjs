import assert from 'node:assert/strict';
import test from 'node:test';

import { testPlan } from './test-runner.mjs';

test('focused npm arguments are forwarded only to Vitest', () => {
  assert.deepEqual(
    testPlan(['--run', 'tests/unit/reset-realtime.test.ts'], ['scripts/gate.test.mjs']),
    [{
      runner: 'vitest',
      args: ['--run', 'tests/unit/reset-realtime.test.ts'],
    }],
  );
});

test('full npm test retains Vitest and Node script gates', () => {
  assert.deepEqual(
    testPlan([], ['scripts/a.test.mjs', 'scripts/b.test.mjs']),
    [
      { runner: 'vitest', args: ['run'] },
      {
        runner: 'node',
        args: ['--test', 'scripts/a.test.mjs', 'scripts/b.test.mjs'],
      },
    ],
  );
});

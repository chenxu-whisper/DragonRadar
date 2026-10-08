import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  composeOverride,
  parseLoopbackBinding,
} from './reset-contract-runtime-support.mjs';

test('composeOverride lets Docker allocate loopback ports and pins internal owner authority', () => {
  const override = composeOverride({
    publicEdgeSubnet: '10.253.7.0/28',
  });

  assert.match(override, /DRAGON_TRUSTED_PROXY_CIDR: 10\.253\.7\.2\/32/);
  assert.match(override, /ipv4_address: 10\.253\.7\.3/);
  assert.match(override, /public-caddy:\n    ports: !override/);
  assert.match(override, /ipv4_address: 10\.253\.7\.2/);
  assert.match(
    override,
    /target: 8080\n        published: "0"\n        host_ip: 127\.0\.0\.1/,
  );
  assert.match(override, /owner-caddy:\n    environment:/);
  assert.match(override, /DRAGON_OWNER_AUTHORITY: localhost:8443/);
  assert.match(
    override,
    /target: 8443\n        published: "0"\n        host_ip: 127\.0\.0\.1/,
  );
  assert.match(override, /"https:\/\/localhost:8443"/);
  assert.match(override, /config: !override\n        - subnet: 10\.253\.7\.0\/28/);
});

test('parseLoopbackBinding accepts only a discovered loopback host binding', () => {
  assert.deepEqual(parseLoopbackBinding('127.0.0.1:49152\n'), {
    host: '127.0.0.1',
    port: 49152,
  });
  for (const output of [
    '',
    '0.0.0.0:49152',
    '[::]:49152',
    '127.0.0.1:0',
    '127.0.0.1:65536',
  ]) {
    assert.throws(
      () => parseLoopbackBinding(output),
      /loopback published port/,
    );
  }
});

test('runtime setup is cleanup-bounded and does not reserve host ports', () => {
  const root = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
  const source = readFileSync(
    resolve(root, 'web/scripts/test-reset-contract-runtime.mjs'),
    'utf8',
  );

  assert.doesNotMatch(source, /reserveLoopbackPorts|from 'node:net'/);
  assert.ok(source.indexOf('try {') < source.indexOf('mkdirSync(state'));
  assert.match(source, /compose\('port', 'public-caddy', '8080'\)/);
  assert.match(source, /compose\('port', 'owner-caddy', '8443'\)/);
  assert.match(source, /https:\/\/localhost:8443\/api\/owner\/v1\/session/);
  assert.match(source, /Host: localhost:8443/);
});

test('CI runs the contract runtime harness as an independent strict Linux job', () => {
  const root = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
  const workflow = readFileSync(resolve(root, '.github/workflows/ci.yml'), 'utf8');
  const job = workflow.match(
    /\n  reset_contract_runtime:\n(?<body>[\s\S]*?)(?=\n  [a-zA-Z0-9_]+:\n|$)/,
  )?.groups?.body;

  assert.ok(job, 'reset_contract_runtime job is missing');
  assert.match(job, /runs-on: ubuntu-latest/);
  assert.match(job, /npm run test:contract-runtime/);
  assert.doesNotMatch(job, /continue-on-error/);
});

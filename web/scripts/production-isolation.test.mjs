import assert from 'node:assert/strict';
import test from 'node:test';

import {
  composeChildEnvironment,
  parsePublishedPort,
  queryPublishedPort,
} from './production-isolation-support.mjs';

test('parsePublishedPort accepts Docker Compose IPv4 and IPv6 output', () => {
  assert.equal(parsePublishedPort('127.0.0.1:49152\n'), 49152);
  assert.equal(parsePublishedPort('[::1]:49153\r\n'), 49153);
  assert.equal(parsePublishedPort(':::49154\n'), 49154);
  assert.equal(
    parsePublishedPort('0.0.0.0:49155\n[::]:49155\n'),
    49155,
  );
});

test('parsePublishedPort rejects missing or invalid published ports', () => {
  for (const output of ['', 'web:8443', '127.0.0.1:0', '[::1]:65536']) {
    assert.throws(
      () => parsePublishedPort(output),
      /Could not parse a published port/,
    );
  }
});

test('queryPublishedPort asks Compose for the service container port', () => {
  const calls = [];
  const compose = (...args) => {
    calls.push(args);
    return '[::1]:51234\n';
  };

  assert.equal(queryPublishedPort(compose, 'web', 8443), 51234);
  assert.deepEqual(calls, [['port', 'web', '8443']]);
});

test('composeChildEnvironment strips stack-owned vars leaked by the parent shell', () => {
  // The Task 20 TLS drill publishes DRAGON_PRIVATE_PORT/PUBLISHED_PORT via
  // GITHUB_ENV for the whole acceptance job; shell variables override the
  // --env-file in Compose interpolation and would force a bind on 8030.
  const base = {
    PATH: '/usr/bin',
    DOCKER_HOST: 'unix:///var/run/docker.sock',
    PLAYWRIGHT_CHANNEL: 'chromium',
    DRAGON_DB_PASSWORD: 'leaked-task20-password',
    DRAGON_PRIVATE_PORT: '8030',
    DRAGON_PRIVATE_PUBLISHED_PORT: '8030',
    DRAGON_WEB_PORT: '8030',
    DRAGON_E2E_STATE_DIR: '/leaked/state',
  };

  assert.deepEqual(composeChildEnvironment(base), {
    PATH: '/usr/bin',
    DOCKER_HOST: 'unix:///var/run/docker.sock',
    PLAYWRIGHT_CHANNEL: 'chromium',
  });
});

test('composeChildEnvironment does not mutate the provided environment', () => {
  const base = { PATH: '/usr/bin', DRAGON_PRIVATE_PUBLISHED_PORT: '8030' };

  composeChildEnvironment(base);

  assert.equal(base.DRAGON_PRIVATE_PUBLISHED_PORT, '8030');
});

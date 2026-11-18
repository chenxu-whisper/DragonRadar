import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import {
  RESEARCH_EDGE_DENY_PROBES,
  RESEARCH_OWNER_READ_PROBES,
  RESEARCH_OWNER_READ_PATTERN,
  RESEARCH_OWNER_WRITE_PROBES,
  RESEARCH_OWNER_WRITE_PATTERN,
  RESEARCH_PUBLIC_DENY_PROBES,
  composeChildEnvironment,
  isResearchOwnerRead,
  isResearchOwnerWrite,
  isResearchPath,
  parsePublishedPort,
  queryPublishedPort,
} from './production-isolation-support.mjs';
import * as support from './production-isolation-support.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
const containersDir = resolve(root, 'containers');
const readContainer = (name) => readFileSync(resolve(containersDir, name), 'utf8');

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
    DRAGON_OWNER_PORT: '8030',
    DRAGON_PUBLIC_PORT: '8080',
    DRAGON_PUBLIC_BIND: '0.0.0.0',
    DRAGON_OWNER_SECRETS_DIR: '/leaked/secrets',
    DRAGON_AI_TOKEN: 'live-token',
    DRAGON_AI_PROVIDER: 'live-provider',
    DRAGON_AI_POLL_SECONDS: '99',
    COMPOSE_FILE: 'compose.private.yaml',
    COMPOSE_PROFILES: 'leaked',
    COMPOSE_PROJECT_NAME: 'leaked',
    COMPOSE_ENV_FILES: '/leaked/env',
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

// Task 17 — research namespace edge isolation. The owner/private Caddy edges
// whitelist exactly the manifest read, run read, submit and cancel routes;
// the workbench/public edge denies the whole /api/research namespace.
test('isResearchPath classifies only the research namespace', () => {
  for (const path of [
    ...RESEARCH_OWNER_READ_PROBES,
    ...RESEARCH_OWNER_WRITE_PROBES.map(probe => probe.path),
    ...RESEARCH_EDGE_DENY_PROBES,
    ...RESEARCH_PUBLIC_DENY_PROBES,
  ]) {
    assert.equal(isResearchPath(path), true, path);
  }
  for (const path of [
    '/api/market/overview',
    '/api/snapshots/list',
    '/api/owner/v1/session',
    '/api/ai/daily-review',
  ]) {
    assert.equal(isResearchPath(path), false, path);
  }
});

test('owner read probes match the anchored researchRead edge pattern', () => {
  for (const path of RESEARCH_OWNER_READ_PROBES) {
    assert.match(path, new RegExp(RESEARCH_OWNER_READ_PATTERN));
    assert.equal(isResearchOwnerRead(path), true);
  }
  // A query string, a traversal segment or a deeper sub-route must not match.
  for (const path of [
    `${RESEARCH_OWNER_READ_PROBES[0]}?expand=1`,
    '/api/research/manifests/../secrets',
    '/api/research/backtests/00000000-0000-4000-8000-0000000000aa/result',
    '/api/research/manifests',
  ]) {
    assert.equal(isResearchOwnerRead(path), false, path);
  }
});

test('owner write probes match the POST-only researchWrite edge pattern', () => {
  for (const probe of RESEARCH_OWNER_WRITE_PROBES) {
    assert.match(probe.path, new RegExp(RESEARCH_OWNER_WRITE_PATTERN));
    assert.equal(isResearchOwnerWrite(probe.method, probe.path), true);
    assert.equal(isResearchOwnerWrite('GET', probe.path), false);
  }
  assert.equal(isResearchOwnerWrite('POST', '/api/research/other'), false);
  assert.equal(isResearchOwnerWrite('POST', '/api/research/backtests?x=1'), false);
});

test('edge deny probes fall through every whitelist as GET requests', () => {
  // The drill issues these as GETs: the POST collection route stays a
  // whitelisted write even though a GET on the collection is edge-denied.
  for (const path of RESEARCH_EDGE_DENY_PROBES) {
    assert.equal(isResearchOwnerRead(path), false, path);
    assert.equal(isResearchOwnerWrite('GET', path), false, path);
  }
});

test('public deny probes cover the manifest read, run read, collection and cancel routes', () => {
  assert.ok(RESEARCH_PUBLIC_DENY_PROBES.some(path => path.startsWith('/api/research/manifests/')));
  assert.ok(RESEARCH_PUBLIC_DENY_PROBES.some(path => path.startsWith('/api/research/backtests/')));
  assert.ok(RESEARCH_PUBLIC_DENY_PROBES.includes('/api/research/backtests'));
  assert.ok(RESEARCH_PUBLIC_DENY_PROBES.some(path => path.endsWith('/cancel')));
  for (const path of RESEARCH_PUBLIC_DENY_PROBES) assert.equal(isResearchPath(path), true);
});

// Task 17 edge contract: the owner/private Caddy edges must embed the exact
// anchored patterns (so a regex rewrite cannot widen the namespace) and place
// both whitelists before the empty default-deny @api fallback.
test('owner Caddy edge whitelists exactly the anchored research patterns before the default-deny fallback', () => {
  for (const file of ['owner.Caddyfile']) {
    const config = readContainer(file);
    assert.ok(
      config.includes(`path_regexp researchRead ${RESEARCH_OWNER_READ_PATTERN}`),
      `${file} is missing the anchored researchRead matcher`,
    );
    assert.ok(
      config.includes(`path_regexp researchWrite ${RESEARCH_OWNER_WRITE_PATTERN}`),
      `${file} is missing the anchored researchWrite matcher`,
    );
    assert.match(config, /@researchRead\s*\{[\s\S]*?method GET HEAD/);
    assert.match(config, /@researchWrite\s*\{[\s\S]*?\n\s*method POST/);
    assert.ok(config.includes('reverse_proxy @researchRead 127.0.0.1:8001'));
    assert.ok(config.includes('reverse_proxy @researchWrite 127.0.0.1:8001'));
    const fallback = config.indexOf('@api path /api/*');
    assert.ok(fallback > config.indexOf('@researchRead'), `${file} researchRead must precede the @api fallback`);
    assert.ok(fallback > config.indexOf('@researchWrite'), `${file} researchWrite must precede the @api fallback`);
  }
});

test('read-only Caddy edges deny the whole research namespace without any research reverse proxy', () => {
  const publicEdge = readContainer('public.Caddyfile');
  assert.doesNotMatch(publicEdge, /path_regexp research/);
  assert.doesNotMatch(publicEdge, /reverse_proxy @research/);
});

test('production drill generates ephemeral unified-stack overrides and isolates owner state', () => {
  const source = readFileSync(resolve(root, 'web/scripts/test-production-isolation.mjs'), 'utf8');
  assert.doesNotMatch(source, /compose\.(private|workbench|reset)\.yaml/);
  assert.doesNotMatch(source, /web\/scripts\/compose\.production-isolation\.yaml/);
  assert.match(source, /composeOverride/);
  assert.match(source, /selectPublicEdgeSubnet/);
  assert.match(source, /owner-caddy/);
  assert.match(source, /owner-api/);
  assert.match(source, /public-caddy/);
  assert.match(source, /public-api/);
  assert.match(source, /productionOwnerOverride/);
  assert.match(source, /DRAGON_OWNER_SECRETS_DIR=/);
  assert.match(source, /denied\.status === 403 && denied\.body === ''/);
  assert.doesNotMatch(source, /denied\.status === 405/);
});

test('generated owner override waits for discovered authority and replaces shared secrets', () => {
  assert.equal(typeof support.productionOwnerOverride, 'function');
  const override = support.productionOwnerOverride({ state: 'E:/scratch/drill' });
  assert.match(override, /owner-caddy:/);
  assert.match(override, /while \[ ! -s \/run\/dragon-e2e\/authority \]/);
  assert.match(override, /export DRAGON_OWNER_AUTHORITY/);
  assert.match(override, /healthcheck:[\s\S]*cat \/run\/dragon-e2e\/authority/);
  assert.match(override, /source: "E:\/scratch\/drill"/);
  assert.match(override, /read_only: true/);
  assert.match(override, /owner-api:[\s\S]*volumes: !override/);
  assert.match(override, /source: "E:\/scratch\/drill\/owner-secrets"/);
  assert.match(override, /target: \/run\/dragon-owner/);
  assert.match(override, /https:\/\/localhost:\$\{DRAGON_OWNER_PORT/);
});

test('subnet selection avoids overlapping Docker networks, not just exact subnets', () => {
  assert.equal(typeof support.selectPublicEdgeSubnet, 'function');
  const calls = [];
  const run = (command, args) => {
    calls.push([command, args]);
    if (args[0] === 'network' && args[1] === 'ls') return 'net-one\nnet-two\n';
    return JSON.stringify([
      { IPAM: { Config: [{ Subnet: '10.240.0.0/16' }] } },
      { IPAM: { Config: [{ Subnet: '10.241.0.0/28' }, { Subnet: 'fd00::/64' }] } },
    ]);
  };
  assert.equal(support.selectPublicEdgeSubnet(run), '10.241.1.0/28');
  assert.deepEqual(calls[1], ['docker', ['network', 'inspect', 'net-one', 'net-two']]);
});

test('subnet selection handles an empty daemon and fails closed when no range is free', () => {
  assert.equal(typeof support.selectPublicEdgeSubnet, 'function');
  assert.equal(support.selectPublicEdgeSubnet(() => ''), '10.240.0.0/28');
  const run = (_, args) => args[1] === 'ls'
    ? 'net\n'
    : JSON.stringify([{ IPAM: { Config: [{ Subnet: '10.0.0.0/8' }] } }]);
  assert.throws(() => support.selectPublicEdgeSubnet(run), /No free private/);
});

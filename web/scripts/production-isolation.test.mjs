import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
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

async function pairingDiagnosticHarness() {
  const { readFileSync, writeFileSync, mkdirSync, existsSync } = await import('node:fs');
  const { resolve, dirname } = await import('node:path');
  const { pathToFileURL } = await import('node:url');
  const { createContext, SourceTextModule, SyntheticModule } = await import('node:vm');
  const [root, evidenceRoot, scenario] = process.argv.slice(1);
  const newsReviewScenario = scenario.startsWith('news-review-');
  const newsReviewPath = '/api/news/00000000-0000-4000-8000-000000000001/associations/review';
  const pairingCode = 'synthetic-pair-8f31a2';
  const diagnostic = [
    "locator.fill: Timeout 30000ms exceeded.",
    "Call log:",
    "  - waiting for getByLabel('one-time pairing code')",
    `  - locator resolved to <input ${scenario === 'readonly' ? 'readonly' : 'disabled'} type="password"/>`,
    `  - fill("${pairingCode}")`,
    "  - attempting fill action",
    "    - waiting for element to be visible, enabled and editable",
    `    - element is not ${scenario === 'readonly' ? 'editable' : 'enabled'}`,
  ].join('\n');
  const fixture = await import(pathToFileURL(resolve(root, 'web/scripts/contract-synthetic-fixture.mjs')));
  const runtime = await import(pathToFileURL(resolve(root, 'web/scripts/reset-contract-runtime-support.mjs')));
  const edge = await import(pathToFileURL(resolve(root, 'web/scripts/production-isolation-support.mjs')));
  let identity;
  let fillCalled = false;
  const consoleOutput = [];
  const fetchCalls = [];
  const permissions = { platform: 'win32', exec: () => ({
    status: 0, stdout: '{"ownerSid":"S-1-5-21-123","protected":true}',
  }) };
  const page = {
    async goto() {},
    async content() { return ''; },
    getByRole() { return { async waitFor() {}, async click() {} }; },
    getByLabel() {
      return { async fill(value) {
        fillCalled = true;
        if (value !== pairingCode) throw new Error('Incorrect synthetic pairing input');
        if (existsSync(resolve(identity.state, 'pair.txt'))) throw new Error('Pair file was not removed');
        if (scenario !== 'console' && !newsReviewScenario) throw new Error(diagnostic);
      } };
    },
    async evaluate(callback, args) {
      const publicPage = this.publicPage;
      context.fetch = async (path, options) => {
        fetchCalls.push({ publicPage, path, options });
        let status = 404;
        let body = '';
        if (publicPage) {
          status = options?.method === 'POST' ? 403 : 404;
        } else if (path === '/api/owner/v1/session' || path === '/api/snapshots/list') {
          status = 200;
        } else if (path === newsReviewPath) {
          status = 403;
          body = '{"code":"FORBIDDEN","message":"Access denied"}';
          if (options?.headers?.['Content-Type'] !== 'application/json' || options?.body !== '{}') {
            status = 400;
            body = '{"code":"INVALID_PARAM","message":"Invalid request"}';
          } else if (scenario === 'news-review-400') {
            status = 400;
          } else if (scenario === 'news-review-404') {
            status = 404;
            body = '';
          } else if (scenario === 'news-review-200') {
            status = 200;
          } else if (scenario === 'news-review-wrong-code') {
            body = '{"code":"csrf_forbidden","message":"Access denied"}';
          } else if (scenario === 'news-review-wrong-message') {
            body = '{"code":"FORBIDDEN","message":"Owner request forbidden"}';
          } else if (scenario === 'news-review-missing-message') {
            body = '{"code":"FORBIDDEN"}';
          } else if (scenario === 'news-review-extra-field') {
            body = '{"code":"FORBIDDEN","message":"Access denied","extra":true}';
          } else if (scenario === 'news-review-malformed-json') {
            body = 'not JSON';
          }
        } else if (path.startsWith('/api/ai/') || edge.RESEARCH_OWNER_READ_PROBES.includes(path)) {
          body = '{"code":"NOT_FOUND"}';
        } else if (options?.method === 'POST' && edge.RESEARCH_OWNER_WRITE_PROBES.some(probe => probe.path === path)) {
          status = 403;
          body = '{"code":"csrf_forbidden"}';
        }
        return { status, async text() { return body; } };
      };
      return callback(args);
    },
  };
  const browser = {
    version() { return '142.0.1'; },
    async newContext({ baseURL }) {
      const currentPage = { ...page, publicPage: baseURL.startsWith('http:') };
      return { async newPage() { return currentPage; }, async close() {} };
    },
    async close() { if (scenario === 'browser-close') throw new Error(`browser close: ${diagnostic}`); },
  };
  function spawn(_command, args) {
    let stdout = '';
    if (args[0] === 'version') stdout = '{"Client":{"Version":"29.9.2"},"Server":{"Version":"29.9.2"}}';
    else if (args.includes('--short')) stdout = '5.6.1';
    else if (args.includes('port')) stdout = '127.0.0.1:51234';
    else if (args.includes('cp')) writeFileSync(args.at(-1), `${pairingCode}\n`);
    else if (scenario === 'cleanup' && args.includes('down')) {
      return { status: 1, stdout: '', stderr: `cleanup: ${diagnostic}` };
    }
    return { status: 0, stdout, stderr: '' };
  }
  const overrides = {
    '@playwright/test': { chromium: { async launch() { return browser; } } },
    'node:child_process': { spawnSync: spawn },
    './production-isolation-support.mjs': {
      ...edge, selectPublicEdgeSubnet: () => '10.240.0.0/28',
      ...(newsReviewScenario ? {} : {
        RESEARCH_OWNER_READ_PROBES: [], RESEARCH_OWNER_WRITE_PROBES: [],
        RESEARCH_EDGE_DENY_PROBES: [], RESEARCH_PUBLIC_DENY_PROBES: [],
      }),
    },
    './contract-synthetic-fixture.mjs': {
      ...fixture,
      createFixtureIdentity(options) {
        identity = fixture.createFixtureIdentity({ ...options, evidenceRoot });
        return identity;
      },
      captureFixtureSource: () => ({
        head: 'synthetic-head',
        lockfiles: ['uv.lock', 'web/package-lock.json'].map(path => ({
          path, bytes: 1, sha256: 'a'.repeat(64),
        })),
      }),
      createPrivateDirectory(path) { mkdirSync(path); return { protected: true }; },
      writePrivateFile: (path, content) => fixture.writePrivateFile(path, content, permissions),
      protectPrivateFile: path => fixture.protectPrivateFile(path, permissions),
      protectOwnerSecretFiles: path => fixture.protectOwnerSecretFiles(path, permissions),
    },
    './reset-contract-runtime-support.mjs': {
      ...runtime,
      async finishProductionIsolationEvidence(...args) {
        const manifest = await runtime.finishProductionIsolationEvidence(...args);
        if (scenario === 'finalization') throw new Error(`finalization: ${diagnostic}`);
        if (scenario === 'console') manifest.result.diagnostic = diagnostic;
        return manifest;
      },
    },
  };
  const context = createContext({
    process, console: { log: value => consoleOutput.push(String(value)) },
  });
  const runnerPath = resolve(root, 'web/scripts/test-production-isolation.mjs');
  const runner = new SourceTextModule(readFileSync(runnerPath, 'utf8'), {
    context, identifier: pathToFileURL(runnerPath).href,
    initializeImportMeta(meta, module) { meta.url = module.identifier; },
  });
  await runner.link(async specifier => {
    const exports = overrides[specifier] ?? await import(specifier);
    return new SyntheticModule(Object.keys(exports), function () {
      for (const [name, value] of Object.entries(exports)) this.setExport(name, value);
    }, { context });
  });
  let rethrow;
  try { await runner.evaluate(); } catch (error) { rethrow = { message: error.message, stack: error.stack }; }
  const manifest = readFileSync(resolve(identity.state, 'manifest.json'), 'utf8');
  const commands = readFileSync(resolve(identity.state, 'commands.jsonl'), 'utf8');
  process.stdout.write(JSON.stringify({
    pairingCode, diagnostic, fillCalled, consoleOutput, fetchCalls, rethrow, manifest, commands,
    pairFileRetained: existsSync(resolve(identity.state, 'pair.txt')),
  }));
}

for (const scenario of [
  'valid', '400', '404', '200', 'wrong-code', 'wrong-message',
  'missing-message', 'extra-field', 'malformed-json',
]) {
  test(`production news-review probe requires JSON without CSRF and exact refusal: ${scenario}`, () => {
    const evidenceRoot = resolve(root, '..', 'contract-c-production-probe-repair', 'regression');
    const child = spawnSync(process.execPath, [
      '--experimental-vm-modules', '--input-type=module', '--eval',
      `await (${pairingDiagnosticHarness.toString()})();`, root, evidenceRoot, `news-review-${scenario}`,
    ], { encoding: 'utf8', timeout: 120_000 });
    assert.equal(child.status, 0, child.stderr);
    const observed = JSON.parse(child.stdout);
    const privateCalls = observed.fetchCalls.filter(call => !call.publicPage);
    assert.equal(privateCalls.find(call => call.path === '/api/owner/v1/session')?.options, undefined);
    const reviews = privateCalls.filter(call => call.path.endsWith('/associations/review'));
    assert.equal(reviews.length, 1);
    assert.deepEqual(reviews[0].options, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
    });
    for (const [path, options] of [
      ['/api/owner/v1/notes', undefined],
      ['/api/snapshots/publish', { method: 'POST' }],
    ]) {
      const calls = privateCalls.filter(call => call.path === path);
      assert.equal(calls.length, 1, path);
      assert.deepEqual(calls[0].options, options);
    }
    const manifest = JSON.parse(observed.manifest);
    if (scenario === 'valid') {
      assert.equal(observed.rethrow, undefined);
      assert.equal(manifest.status, 'passed');
      for (const path of [
        ...RESEARCH_OWNER_READ_PROBES,
        ...RESEARCH_OWNER_WRITE_PROBES.map(probe => probe.path),
        ...RESEARCH_EDGE_DENY_PROBES,
      ]) {
        assert.ok(privateCalls.some(call => call.path === path), path);
      }
    } else {
      assert.equal(manifest.status, 'failed');
      assert.equal(manifest.result, undefined);
      assert.ok(observed.rethrow, 'Unexpected refusal was accepted');
    }
  });
}

for (const scenario of ['disabled', 'readonly', 'cleanup', 'browser-close', 'finalization', 'console']) {
  test(`pairing diagnostics redact Playwright fill values at every sink: ${scenario}`, () => {
    const evidenceRoot = resolve(root, '..', 'contract-c-c0-pairing-fix', 'regression');
    const child = spawnSync(process.execPath, [
      '--experimental-vm-modules', '--input-type=module', '--eval',
      `await (${pairingDiagnosticHarness.toString()})();`, root, evidenceRoot, scenario,
    ], { encoding: 'utf8', timeout: 120_000 });
    assert.equal(child.status, 0, child.stderr);
    const observed = JSON.parse(child.stdout);
    assert.equal(observed.fillCalled, true);
    assert.equal(observed.pairFileRetained, false);
    const retainedSinks = Object.entries({
      manifest: observed.manifest, commands: observed.commands,
      console: JSON.stringify(observed.consoleOutput), rethrow: JSON.stringify(observed.rethrow),
    }).filter(([, value]) => value?.includes(observed.pairingCode)).map(([sink]) => sink);
    assert.deepEqual(retainedSinks, [], 'Diagnostic sinks retained the pairing code');
    const manifest = JSON.parse(observed.manifest);
    if (scenario === 'console') {
      assert.equal(manifest.status, 'passed');
      assert.equal(observed.rethrow, undefined);
      assert.match(observed.consoleOutput[0], /fill\(\\"?\[REDACTED\]/);
    } else {
      assert.equal(manifest.status, 'failed');
      assert.equal(manifest.result, undefined);
      assert.equal(observed.consoleOutput.length, 0);
      assert.match(observed.rethrow.message, /fill\("\[REDACTED\]"\)/);
      assert.match(manifest.error, /fill\("\[REDACTED\]"\)/);
      assert.match(manifest.error, /Timeout 30000ms exceeded/);
      if (scenario === 'cleanup') assert.match(manifest.cleanup.error, /cleanup/);
      if (scenario === 'browser-close') assert.match(manifest.error, /browser close/);
      if (scenario === 'finalization') assert.match(observed.rethrow.message, /finalization/);
    }
  });
}

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

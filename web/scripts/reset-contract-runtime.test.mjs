import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  composeOverride,
  ownerTransportRequestOptions,
  parseLoopbackBinding,
} from './reset-contract-runtime-support.mjs';
import * as runtimeSupport from './reset-contract-runtime-support.mjs';
import {
  createFixtureIdentity, finishFixtureEvidence, initializeFixtureEvidence,
} from './contract-synthetic-fixture.mjs';
import { existsSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';

test('composeOverride lets Docker allocate loopback ports and pins internal owner authority', () => {
  const override = composeOverride({
    publicEdgeSubnet: '10.253.7.0/28',
    password: 'a'.repeat(48),
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

test('fixture override pins dragon_test in database, healthcheck and all four backends', () => {
  const password = 'b'.repeat(48);
  const override = composeOverride({ publicEdgeSubnet: '10.253.8.0/28', password });
  assert.match(override, /POSTGRES_DB: dragon_test/);
  assert.match(override, /pg_isready -U dragon_radar -d dragon_test/);
  for (const service of ['public-api', 'owner-api', 'analysis-worker', 'ai-worker']) {
    const body = override.match(new RegExp(`^  ${service}:\\n([\\s\\S]*?)(?=^  [a-z-]+:|^networks:)`, 'm'))?.[1];
    assert.ok(body, `${service} override missing`);
    assert.ok(body.includes(`DRAGON_DATABASE_URL: postgresql+psycopg://dragon_radar:${password}@db:5432/dragon_test`));
  }
  assert.doesNotMatch(override, /@db:5432\/dragon_radar\b/);
  assert.throws(() => composeOverride({ publicEdgeSubnet: '10.253.8.0/28', password: 'daily' }), /fixture database/);
  assert.throws(() => composeOverride({ publicEdgeSubnet: '10.253.8.0/28' }), /fixture database/);
});

test('C0 wiring retains evidence, scopes cleanup and isolates fixture subprocesses', () => {
  const root = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
  const source = readFileSync(resolve(root, 'web/scripts/test-reset-contract-runtime.mjs'), 'utf8');
  assert.match(source, /from '\.\/contract-synthetic-fixture\.mjs'/);
  assert.match(source, /fixtureChildEnvironment/);
  assert.match(source, /writePrivateFile/);
  assert.match(source, /captureFixtureSource/);
  assert.match(source, /cleanupFixtureProject/);
  assert.match(source, /finishFixtureEvidence/);
  assert.doesNotMatch(source, /rmSync\(state/);
  assert.doesNotMatch(source, /mode:\s*0o600/);
  assert.doesNotMatch(source, /from '\.\/production-isolation-support\.mjs'/);
});

test('browser launch options isolate both Chromium and channel-selected child environments', () => {
  for (const channel of ['chromium', 'msedge']) {
    assert.equal(typeof runtimeSupport.fixtureBrowserLaunchOptions, 'function');
    const options = runtimeSupport.fixtureBrowserLaunchOptions(channel, {
      Path: 'tools', SystemRoot: 'windows', DRAGON_AI_TOKEN: 'private', NODE_OPTIONS: 'unsafe',
    });
    assert.deepEqual(options.env, { Path: 'tools', SystemRoot: 'windows' });
    assert.equal(options.channel, channel === 'chromium' ? undefined : channel);
  }
  const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
  const source = readFileSync(resolve(root, 'web/scripts/test-production-isolation.mjs'), 'utf8');
  assert.match(source, /chromium\.launch\(fixtureBrowserLaunchOptions\(channel\)\)/);
});

test('both runners prove all generated file ACLs immediately after owner-init', () => {
  const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
  for (const name of ['test-production-isolation.mjs', 'test-reset-contract-runtime.mjs']) {
    const source = readFileSync(resolve(root, 'web/scripts', name), 'utf8');
    assert.match(source, /compose\('run',[^\n]*'owner-init'\);\s*permissions\.ownerSecretFiles = protectOwnerSecretFiles\(identity\.secretsDir\);/);
  }
});

test('production teardown records label-verified residual cleanup and only reports finalized success', () => {
  const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
  const source = readFileSync(resolve(root, 'web/scripts/test-production-isolation.mjs'), 'utf8');
  assert.match(source, /cleanupFixtureProject\(\{ project, runId, kind: 'isolation', run, compose \}\)/);
  assert.doesNotMatch(source, /compose\('down'/);
  assert.doesNotMatch(source, /rmSync\(state/);
  assert.match(source, /recordFixtureCommand\(identity/);
  assert.match(source, /initializeFixtureEvidence\(identity/);
  assert.match(source, /finishProductionIsolationEvidence\(identity/);
  assert.ok(source.indexOf('console.log(') > source.indexOf('manifest?.status'));
  assert.match(source, /sanitizeFixtureEvidence\(primaryError\.message/);
});

test('browser-close failure still removes every secret and keeps command/result/cleanup evidence', async t => {
  const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
  const evidenceRoot = resolve(root, '..', 'contract-c-c0-metadata-fix', `close-unit-${process.pid}-${Date.now()}`);
  t.after(() => rmSync(evidenceRoot, { recursive: true, force: true }));
  const identity = createFixtureIdentity({ root, runId: '123-456-abcdef12', evidenceRoot });
  initializeFixtureEvidence(identity, {});
  mkdirSync(identity.secretsDir);
  writeFileSync(identity.envFile, identity.password);
  const extra = resolve(identity.state, 'pair.txt');
  writeFileSync(extra, 'pair-secret');
  assert.equal(typeof runtimeSupport.finishProductionIsolationEvidence, 'function');
  const manifest = await runtimeSupport.finishProductionIsolationEvidence(identity, {
    browser: { async close() { throw new Error('close failed'); } },
    error: new Error('probe failed'), cleanup: { status: 'removed' },
    result: { token: 'private-token', public: 'http://127.0.0.1:1234' },
    secretFiles: [extra],
  });
  assert.equal(manifest.status, 'failed');
  assert.match(manifest.error, /probe failed/);
  assert.match(manifest.error, /close failed/);
  assert.equal(manifest.probeResult.token, '[REDACTED]');
  assert.equal(manifest.cleanup.status, 'removed');
  assert.equal(existsSync(identity.envFile), false);
  assert.equal(existsSync(identity.secretsDir), false);
  assert.equal(existsSync(extra), false);
  assert.equal(existsSync(resolve(identity.state, 'commands.jsonl')), true);
  assert.equal(finishFixtureEvidence(identity, { cleanup: { status: 'failed', error: 'down failed' } }).status, 'failed');
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

test('owner transport request connects to the published port with trusted authority', () => {
  const options = ownerTransportRequestOptions({
    connectPort: 49152,
    ca: 'test-owner-ca',
  });

  assert.equal(options.hostname, '127.0.0.1');
  assert.equal(options.port, 49152);
  assert.notEqual(options.port, 8443);
  assert.equal(options.servername, 'localhost');
  assert.equal(options.headers.Host, 'localhost:8443');
  assert.equal(options.path, '/api/owner/v1/session');
  assert.equal(options.ca, 'test-owner-ca');
});

test('runtime setup is cleanup-bounded and does not reserve host ports', () => {
  const root = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
  const source = readFileSync(
    resolve(root, 'web/scripts/test-reset-contract-runtime.mjs'),
    'utf8',
  );

  assert.doesNotMatch(source, /reserveLoopbackPorts|from 'node:net'/);
  assert.ok(source.indexOf('try {') < source.indexOf('initializeFixtureEvidence(identity'));
  assert.match(source, /compose\('port', 'public-caddy', '8080'\)/);
  assert.match(source, /compose\('port', 'owner-caddy', '8443'\)/);
  assert.match(source, /import https from 'node:https'/);
  assert.match(
    source,
    /ownerTransportRequestOptions\(\{\s*connectPort: ownerBinding\.port,\s*ca: ownerCa/,
  );
  assert.match(source, /assertOwnerTransport\(ownerBinding/);
  assert.doesNotMatch(
    source,
    /owner:\s*`https:\/\/\$\{ownerBinding\.host\}:\$\{ownerBinding\.port\}`/,
  );
  assert.match(source, /publishedMapping:/);
  assert.match(source, /resolve\(root, 'compose\.yaml'\)/);
  assert.doesNotMatch(source, /compose\.reset\.yaml/);
  assert.match(source, /fixtureChildEnvironment/);
});

test('CI runs the contract runtime harness as an independent strict Linux job', () => {
  const root = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
  const workflow = readFileSync(
    resolve(root, '.github/workflows/ci.yml'),
    'utf8',
  ).replace(/\r\n/g, '\n');
  const job = workflow.match(
    /\n  reset_contract_runtime:\n(?<body>[\s\S]*?)(?=\n  [a-zA-Z0-9_]+:\n|$)/,
  )?.groups?.body;

  assert.ok(job, 'reset_contract_runtime job is missing');
  assert.match(job, /runs-on: ubuntu-latest/);
  assert.match(job, /npm run test:contract-runtime/);
  assert.doesNotMatch(job, /continue-on-error/);
});

test('both runners collect C0 identities before runtime and production records the launched browser', () => {
  const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
  for (const name of ['test-production-isolation.mjs', 'test-reset-contract-runtime.mjs']) {
    const source = readFileSync(resolve(root, 'web/scripts', name), 'utf8');
    assert.match(source, /recordFixtureToolIdentities\(identity, run\)/);
    assert.ok(source.indexOf('evidencePrepared = true') < source.indexOf('recordFixtureToolIdentities(identity, run)'));
    assert.ok(source.indexOf('recordFixtureToolIdentities(identity, run)') < source.indexOf("compose('config'"));
  }
  const transport = readFileSync(resolve(root, 'web/scripts/test-reset-contract-runtime.mjs'), 'utf8');
  assert.match(transport, /initializeFixtureEvidence\(identity,[^\n]*browserMode: 'browser'/);
  const production = readFileSync(resolve(root, 'web/scripts/test-production-isolation.mjs'), 'utf8');
  assert.match(production, /recordFixtureBrowserIdentity\(identity, \{ channel \}\);\s*browser = await chromium\.launch/);
  assert.match(production, /recordFixtureBrowserIdentity\(identity, \{ channel, version: browser\.version\(\) \}\)/);
});

test('C1 generator freezes two synthetic 61-session inputs without touching production or requiring a database', t => {
  const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
  const helper = resolve(root, 'web/scripts/contract-c1-fixture.py');
  assert.ok(existsSync(helper), 'C1 fixture generator is missing');
  const output = resolve(root, '..', `contract-c-c1-generator-${process.pid}-${Date.now()}`);
  t.after(() => rmSync(output, { recursive: true, force: true }));
  const python = process.platform === 'win32'
    ? resolve(root, '.venv/Scripts/python.exe') : resolve(root, '.venv/bin/python');
  const env = {
    ...runtimeSupport.fixtureBrowserLaunchOptions('chromium').env,
    PYTHONPATH: [resolve(root, 'src'), resolve(root, 'tests')].join(process.platform === 'win32' ? ';' : ':'),
    PYTHONDONTWRITEBYTECODE: '1',
  };
  const result = spawnSync(python, [helper, 'generate', '--output', output], {
    cwd: root, encoding: 'utf8', timeout: 30_000, env,
  });
  assert.equal(result.status, 0, result.stderr);
  const index = JSON.parse(readFileSync(resolve(output, 'inputs.json')));
  assert.equal(index.synthetic, true);
  assert.equal(index.publications.length, 2);
  assert.notEqual(index.publications[0].article_id, index.publications[1].article_id);
  const before = readFileSync(resolve(output, 'market-1.json'));
  for (const i of [1, 2]) {
    const market = JSON.parse(readFileSync(resolve(output, `market-${i}.json`)));
    const topic = JSON.parse(readFileSync(resolve(output, `topic-${i}.json`)));
    assert.equal(market.calendar.sessions.length, 61);
    assert.equal(market.bars.length, 61);
    assert.equal(topic.coverage_cells.length, 61);
    assert.equal(market.grant.sharing, true);
    assert.equal(market.grant.ai, true);
    assert.equal(topic.topics[0].topic_id, `SYNTH-SECTOR-${i}`);
    assert.equal(topic.memberships[0].security_id, `SYNTH-C1-${i}`);
    assert.match(market.security_states[0].name, /Synthetic/);
    assert.equal(index.publications[i - 1].source_version, `s${i}`);
    assert.match(index.files[`market-${i}.json`], /^[a-f0-9]{64}$/);
  }
  const retry = spawnSync(python, [helper, 'generate', '--output', output], {
    cwd: root, encoding: 'utf8', timeout: 30_000, env,
  });
  assert.notEqual(retry.status, 0, 'Frozen synthetic inputs must not be overwritten');
  assert.deepEqual(readFileSync(resolve(output, 'market-1.json')), before);
});

test('C1 browser configuration enforces real TLS and excludes insecure production isolation', () => {
  const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
  const path = resolve(root, 'web/playwright.contract.config.ts');
  assert.ok(existsSync(path), 'Dedicated strict C1 browser config missing');
  const config = readFileSync(path, 'utf8');
  assert.match(config, /ignoreHTTPSErrors: false/);
  assert.match(config, /headless: false/);
  assert.match(config, /trace: 'off'/);
  assert.match(config, /reset-contract-runtime\.spec\.ts/);
  assert.doesNotMatch(config, /ignore-certificate-errors|disable-web-security|production-isolation/);
});

test('C1 fixture database operations fail closed outside disposable dragon_test', () => {
  const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
  const python = process.platform === 'win32'
    ? resolve(root, '.venv/Scripts/python.exe') : resolve(root, '.venv/bin/python');
  const result = spawnSync(python, [
    resolve(root, 'web/scripts/contract-c1-fixture.py'), 'archive',
    '--publication', '00000000-0000-4000-8000-000000000001',
  ], {
    cwd: root, encoding: 'utf8', timeout: 30_000,
    env: { ...runtimeSupport.fixtureBrowserLaunchOptions('chromium').env,
      PYTHONPATH: resolve(root, 'src'), PYTHONDONTWRITEBYTECODE: '1',
      DRAGON_DATABASE_URL: 'postgresql+psycopg://private:secret@daily:5432/dragon_radar' },
  });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /C1 requires isolated dragon_test at db/);
  assert.doesNotMatch(result.stderr, /private:secret/);
});

test('C1 is wired after C0 probes with label-bounded live browser checkpoints', () => {
  const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
  const runner = readFileSync(resolve(root, 'web/scripts/test-reset-contract-runtime.mjs'), 'utf8');
  assert.match(runner, /runC1Contract/);
  assert.ok(runner.indexOf('await runC1Contract(') > runner.indexOf('assertPublicAssets();'));
  const path = resolve(root, 'web/scripts/contract-c1-runtime.mjs');
  assert.ok(existsSync(path), 'Live C1 orchestration missing');
  const runtime = readFileSync(path, 'utf8');
  assert.match(runtime, /fixtureEvidenceSecrets\(identity\)\.push/);
  assert.match(runtime, /com\.docker\.compose\.project/);
  assert.match(runtime, /container:\$\{ownerContainer\}/);
  assert.match(runtime, /source-revoke/);
  assert.match(runtime, /owner-revoke/);
  assert.match(runtime, /assertContractPublications/);
  assert.match(runtime, /owner-caddy', 'cat', '\/etc\/caddy\/Caddyfile'/);
  const spec = readFileSync(resolve(root, 'web/tests/e2e/reset-contract-runtime.spec.ts'), 'utf8');
  assert.match(spec, /data-member/);
  assert.match(spec, /引用来源/);
  assert.match(spec, /headers: owner && method !== 'GET' \? \{ Origin: origin \} : undefined/);
  assert.match(spec, /expect\(forbidden\)\.toEqual\(\{ code: 'FORBIDDEN', message: 'Access denied' \}\)/);
  assert.match(spec, /pendingRequests/);
  assert.match(spec, /error\.transition === 'source-revoked'/);
  assert.match(spec, /expect\(failure\.transition/);
  assert.match(spec, /code: 'source_forbidden', message: 'Source access denied'/);
  assert.match(spec, /initial\.ownerCaddyProof\?\.denyLhb/);
  assert.match(spec, /isContractLhbDenial\(error, knownPublications\)/);
  const support = readFileSync(resolve(root, 'web/scripts/reset-contract-runtime-support.mjs'), 'utf8');
  assert.match(support, /Date\.parse\(publication\.receipt\.published_at\)/);
  assert.doesNotMatch(spec, /\^\\\/api\\\/\(\?:search\|snapshots/);
  assert.doesNotMatch(spec, /\.route\(|routeFromHAR|ignoreHTTPSErrors|ignore-certificate-errors/);
});

test('C1 archive evidence reads market-owned topic_details and rejects topic-only substitution', () => {
  const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
  const python = process.platform === 'win32'
    ? resolve(root, '.venv/Scripts/python.exe') : resolve(root, '.venv/bin/python');
  const code = [
    'import importlib.util',
    'from datetime import date, datetime, UTC',
    'from uuid import UUID',
    `spec = importlib.util.spec_from_file_location("fixture", ${JSON.stringify(resolve(root, 'web/scripts/contract-c1-fixture.py'))})`,
    'module = importlib.util.module_from_spec(spec)',
    'spec.loader.exec_module(module)',
    'root = {"id": UUID("00000000-0000-4000-8000-000000000001"), "version": 1, "published_at": datetime.now(UTC), "trade_date": date(2026,9,8)}',
    'detail = {"members": [{"security_id": "SYNTH-C1-1"}], "topic_basis": {"source_version": "s1"}}',
    'block = {"topic_details": {"sectors": {"SYNTH-SECTOR-1": detail}}}',
    'rows = [{"domain": "market", "payload": block}, {"domain": "topic", "payload": {}}, {"domain": "news", "payload": {"articles": [{"title": "Synthetic C1 article 1", "article_id": "actual-article", "available_at": "2026-09-08T10:00:00+00:00"}]}}]',
    'proof = module.archive_evidence(root, rows, 1)',
    'assert proof["members"] == ["SYNTH-C1-1"]',
    'assert proof["source_version"] == "s1"',
    'rows[0]["payload"] = {}',
    'rows[1]["payload"] = block',
    'try:',
    '    module.archive_evidence(root, rows, 1)',
    'except KeyError:',
    '    pass',
    'else:',
    '    raise AssertionError("Topic-only substitution accepted")',
  ].join('\n');
  const result = spawnSync(python, ['-c', code], { cwd: root, encoding: 'utf8', timeout: 30_000,
    env: { ...runtimeSupport.fixtureBrowserLaunchOptions('chromium').env, PYTHONDONTWRITEBYTECODE: '1' } });
  assert.equal(result.status, 0, result.stderr);
});

test('C1 disposable browser package setup uses bounded signed HTTPS repositories', () => {
  assert.equal(typeof runtimeSupport.contractBrowserSetup, 'function');
  const setup = runtimeSupport.contractBrowserSetup('owner');
  assert.match(setup, /https:\/\/archive\.ubuntu\.com/);
  assert.match(setup, /https:\/\/security\.ubuntu\.com/);
  assert.match(setup, /APT::Update::Error-Mode=any/);
  assert.match(setup, /Acquire::Retries=0/);
  assert.match(setup, /Acquire::https::Timeout=20/);
  assert.match(setup, /ca-certificates libnss3-tools xvfb xauth/);
  assert.match(setup, /certutil -A/);
  assert.doesNotMatch(setup, /allow-unauthenticated|Verify-Peer=false|trusted=yes/);
  assert.doesNotMatch(runtimeSupport.contractBrowserSetup('public'), /certutil -A|update-ca-certificates/);
  assert.throws(() => runtimeSupport.contractBrowserSetup('daily'), /browser mode/);
});

test('C1 owner NSS setup replaces pretrust databases and cannot prompt indefinitely', () => {
  const setup = runtimeSupport.contractBrowserSetup('owner');
  assert.match(setup, /for d in \/home\/pwuser\/\.pki\/nssdb \/home\/pwuser\/\.local\/share\/pki\/nssdb/);
  assert.match(setup, /rm -rf -- "\$d"/);
  assert.ok(setup.indexOf('rm -rf -- "$d"') < setup.indexOf('certutil -N'));
  for (const operation of ['N', 'A', 'L']) {
    assert.match(setup, new RegExp(`timeout --kill-after=2s 15s runuser -u pwuser -- certutil -${operation}[^\\n]*-f /dev/null[^\\n]*< /dev/null`));
  }
  assert.match(setup, /certutil -N --empty-password/);
  assert.match(setup, /certutil -L[^\n]*-n dragon-fixture/);
  assert.doesNotMatch(setup, /ignore-certificate-errors|Verify-Peer=false/);
});

test('C1 synthetic provider result passes real draft verification with exact citation fields', () => {
  const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
  const python = process.platform === 'win32'
    ? resolve(root, '.venv/Scripts/python.exe') : resolve(root, '.venv/bin/python');
  const code = [
    'import importlib.util',
    'from dragon_radar.ai.verification import build_manifest_evidence, verify_output',
    `spec = importlib.util.spec_from_file_location("fixture", ${JSON.stringify(resolve(root, 'web/scripts/contract-c1-fixture.py'))})`,
    'module = importlib.util.module_from_spec(spec)',
    'spec.loader.exec_module(module)',
    'publication = {"snapshot_id": "00000000-0000-4000-8000-000000000001", "as_of": "2026-11-19T10:00:00+00:00", "version": "v1"}',
    'selected = {"article": {"content": "Synthetic archived fixture content.", "available_at": "2026-09-08T10:00:00+00:00"}}',
    'manifest = {"task_type": "summarize", "publication": publication, "input": selected, "evidence": build_manifest_evidence("summarize", publication, selected)}',
    'content, entry = module.synthetic_provider_content(manifest)',
    'result = verify_output(manifest, content)',
    'assert result.status == "passed", result',
    'assert content["citations"] == [entry]',
    'assert set(content["facts"][0]) == {"text", "evidence_id", "evidence_position", "value"}',
    'manifest["evidence"] = []',
    'try:',
    '    module.synthetic_provider_content(manifest)',
    'except ValueError:',
    '    pass',
    'else:',
    '    raise AssertionError("Missing archived citation accepted")',
  ].join('\n');
  const result = spawnSync(python, ['-c', code], { cwd: root, encoding: 'utf8', timeout: 30_000,
    env: { ...runtimeSupport.fixtureBrowserLaunchOptions('chromium').env, PYTHONPATH: resolve(root, 'src'), PYTHONDONTWRITEBYTECODE: '1' } });
  assert.equal(result.status, 0, result.stderr);
});

test('C1 retains actual database/schema query evidence in the runtime outcome', () => {
  const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
  const helper = readFileSync(resolve(root, 'web/scripts/contract-c1-fixture.py'), 'utf8');
  const runtime = readFileSync(resolve(root, 'web/scripts/contract-c1-runtime.mjs'), 'utf8');
  assert.match(helper, /SELECT current_database\(\).*current_schema\(\)/);
  assert.match(runtime, /helper\('database-proof'\)/);
  assert.match(runtime, /databaseProof\.database === 'dragon_test'/);
  assert.match(runtime, /databaseProof[, }]/);
  assert.match(runtime, /mounts: item\.Mounts\.map/);
  const runner = readFileSync(resolve(root, 'web/scripts/test-reset-contract-runtime.mjs'), 'utf8');
  assert.match(runner, /generatedFileCount: Object\.keys\(permissions\.ownerSecretFiles\)\.length/);
});

test('C1 cleanup merges primary and cleanup failures without dropping either diagnostic', () => {
  assert.equal(typeof runtimeSupport.mergeContractFailures, 'function');
  const primary = new Error('browser failed');
  assert.equal(runtimeSupport.mergeContractFailures(primary), primary);
  const failure = runtimeSupport.mergeContractFailures(primary, new Error('browser cleanup failed'));
  assert.match(failure.message, /browser failed/);
  assert.match(failure.message, /browser cleanup failed/);
});

test('C1 checkpoints publish complete JSON atomically without overwriting receipts', t => {
  const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
  const output = resolve(root, '..', `contract-c-c1-checkpoint-${process.pid}-${Date.now()}`);
  mkdirSync(output);
  t.after(() => rmSync(output, { recursive: true, force: true }));
  assert.equal(typeof runtimeSupport.publishContractCheckpoint, 'function');
  const path = resolve(output, 'paired.request.json');
  runtimeSupport.publishContractCheckpoint(path, '{"complete":true}');
  assert.deepEqual(JSON.parse(readFileSync(path)), { complete: true });
  assert.equal(existsSync(`${path}.pending`), false);
  assert.throws(() => runtimeSupport.publishContractCheckpoint(path, '{}'), /already published/);
  assert.deepEqual(JSON.parse(readFileSync(path)), { complete: true });
  const invalid = resolve(output, 'invalid.json');
  assert.throws(() => runtimeSupport.publishContractCheckpoint(invalid, '{'), SyntaxError);
  assert.equal(existsSync(invalid), false);
  assert.equal(existsSync(`${invalid}.pending`), false);
  const runtime = readFileSync(resolve(root, 'web/scripts/contract-c1-runtime.mjs'), 'utf8');
  const spec = readFileSync(resolve(root, 'web/tests/e2e/reset-contract-runtime.spec.ts'), 'utf8');
  assert.match(runtime, /publishContractCheckpoint\(resolve\(channel, `\$\{name\}\.response\.json`\)/);
  assert.match(spec, /publishContractCheckpoint\(`\$\{channel\}\/\$\{name\}\.request\.json`/);
});

test('C1 browser diagnostics redact cookies, CSRF headers and registered pairing secrets', () => {
  assert.equal(typeof runtimeSupport.sanitizeContractBrowserDiagnostic, 'function');
  const output = runtimeSupport.sanitizeContractBrowserDiagnostic(
    'API failed\n  - cookie: __Host-session=cookie-secret; other=value\n  - X-CSRF-Token: csrf-secret\n  - set-cookie: __Host-session=new-secret\nfill("pair-secret")',
    ['pair-secret'],
  );
  assert.match(output, /API failed/);
  for (const secret of ['cookie-secret', 'csrf-secret', 'new-secret', 'pair-secret']) assert.ok(!output.includes(secret));
});

// Execute the actual SPEC filter without launching Playwright or a browser.
function classifiedLhbError(error, { owner = false, denyLhb = true, publications } = {}) {
  const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
  const source = readFileSync(resolve(root, 'web/tests/e2e/reset-contract-runtime.spec.ts'), 'utf8');
  const start = source.indexOf('  const unexpectedHttpErrors = httpErrors.filter(');
  const end = source.indexOf('  expect(unexpectedHttpErrors,', start);
  assert.ok(start >= 0 && end > start, 'Actual SPEC HTTP error filter missing');
  const publication = {
    archive: { security_id: 'SYNTH-C1-1' },
    receipt: { snapshot_id: 'bound-publication', published_at: '2026-11-19T10:00:00+00:00' },
  };
  const evaluate = new Function('httpErrors', 'owner', 'initial', 'knownPublications', 'p1',
    'isContractLhbDenial', `${source.slice(start, end)}\nreturn unexpectedHttpErrors.length === 0;`);
  return evaluate([error], owner, { ownerCaddyProof: { denyLhb } },
    publications ?? [publication], publication, runtimeSupport.isContractLhbDenial);
}

function boundLhbError() {
  const query = new URLSearchParams({
    security_id: 'SYNTH-C1-1', date: '2026-09-08',
    as_of: '2026-11-19T10:00:00Z', page: '1', size: '20',
  });
  return { url: `https://localhost:8443/api/lhb?${query}`, method: 'GET',
    status: 404, body: '', transition: 'archive-reads' };
}

test('C1 LHB classification rejects unbound public requests', () => {
  const error = boundLhbError();
  assert.equal(classifiedLhbError({ ...error, url: 'https://localhost:8443/api/lhb' }), false);
  assert.equal(classifiedLhbError(error, { publications: [] }), false);
});

test('C1 LHB classification rejects unrelated public selections', () => {
  const error = boundLhbError();
  const url = new URL(error.url);
  url.searchParams.set('security_id', 'UNRELATED');
  url.searchParams.set('date', '2026-09-09');
  url.searchParams.set('as_of', '2026-11-19T11:00:00Z');
  assert.equal(classifiedLhbError({ ...error, url: url.href }), false);
});

for (const transition of ['startup', 'paired', 'source-revoked', 'owner-revoked']) {
  test(`C1 LHB classification rejects public ${transition} requests`, () => {
    assert.equal(classifiedLhbError({ ...boundLhbError(), transition }), false);
  });
}

for (const [key, value] of [
  ['security_id', 'UNRELATED'], ['date', '2026-09-09'],
  ['as_of', '2026-11-19T11:00:00Z'], ['as_of', 'invalid'],
  ['page', '2'], ['size', '50'], ['snapshot_id', 'unrelated-publication'],
]) {
  test(`C1 LHB classification rejects public query ${key}=${value}`, () => {
    const error = boundLhbError();
    const url = new URL(error.url);
    url.searchParams.set(key, value);
    assert.equal(classifiedLhbError({ ...error, url: url.href }), false);
  });
}

for (const key of ['security_id', 'date', 'as_of', 'page', 'size']) {
  test(`C1 LHB classification rejects public missing ${key}`, () => {
    const error = boundLhbError();
    const url = new URL(error.url);
    url.searchParams.delete(key);
    assert.equal(classifiedLhbError({ ...error, url: url.href }), false);
  });
}

for (const owner of [false, true]) {
  for (const [key, conflictingValue] of [
    ['security_id', 'UNRELATED'], ['date', '2026-09-09'],
    ['as_of', '2026-11-19T11:00:00Z'], ['page', '2'], ['size', '50'],
  ]) {
    for (const duplicate of ['conflicting', 'identical']) {
      test(`C1 LHB classification rejects ${owner ? 'owner' : 'public'} ${duplicate} duplicate ${key}`, () => {
        const error = boundLhbError();
        const url = new URL(error.url);
        url.searchParams.append(key, duplicate === 'identical' ? url.searchParams.get(key) : conflictingValue);
        assert.equal(classifiedLhbError({ ...error, url: url.href }, { owner, denyLhb: true }), false);
      });
    }
  }
}

test('C1 LHB classification accepts legitimate bound public denial', () => {
  assert.equal(classifiedLhbError(boundLhbError()), true);
});

test('C1 LHB classification preserves owner denyLhb proof and archive binding', () => {
  const error = boundLhbError();
  assert.equal(classifiedLhbError(error, { owner: true }), true);
  assert.equal(classifiedLhbError(error, { owner: true, denyLhb: false }), false);
  assert.equal(classifiedLhbError(error, { owner: true, publications: [] }), false);
  assert.equal(classifiedLhbError({ ...error, transition: 'startup' }, { owner: true }), false);
});

test('C1 LHB classification preserves exact response and request constraints', () => {
  const error = boundLhbError();
  for (const change of [{ method: 'POST' }, { status: 403 }, { body: '{}' }, { body: undefined },
    { url: error.url.replace('/api/lhb?', '/api/unrelated?') }]) {
    assert.equal(classifiedLhbError({ ...error, ...change }), false);
  }
});

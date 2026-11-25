import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import {
  existsSync, mkdirSync, readFileSync, rmSync, statSync, writeFileSync,
} from 'node:fs';
import { dirname, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const fixture = await import('./contract-synthetic-fixture.mjs').catch(error => {
  if (error.code !== 'ERR_MODULE_NOT_FOUND') throw error;
  return {};
});
function utility(name) {
  assert.equal(typeof fixture[name], 'function', `Missing reusable C0 utility: ${name}`);
  return fixture[name];
}
function scratch(t) {
  const path = resolve(root, '..', 'contract-c-c0-pairing-fix', `c0-unit-${process.pid}-${Date.now()}-${Math.random()}`);
  mkdirSync(path, { recursive: true });
  t.after(() => rmSync(path, { recursive: true, force: true }));
  return path;
}
const runId = '123-456-abcdef12';
const project = `dragon-radar-contract-${runId}`;

function completeSource() {
  return {
    head: 'test-head',
    lockfiles: ['uv.lock', 'web/package-lock.json'].map(path => {
      const bytes = readFileSync(resolve(root, path));
      return { path, bytes: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex') };
    }),
  };
}

function seedCompleteMetadata(identity) {
  const file = resolve(identity.state, 'manifest.json');
  const manifest = JSON.parse(readFileSync(file));
  Object.assign(manifest, {
    browserMode: 'browser',
    source: completeSource(),
    tools: {
      node: process.version, platform: process.platform,
      docker: { client: '29.9.2', server: '29.9.2' }, compose: '5.6.1',
      chromium: { status: 'recorded', channel: 'msedge', version: '142.0.1' },
    },
  });
  writeFileSync(file, JSON.stringify(manifest));
  return manifest;
}

test('fixture environment preserves OS essentials but no inherited configuration or tokens', () => {
  const input = {
    Path: 'tools', SystemRoot: 'windows', TEMP: 'temp', HOME: 'home',
    DRAGON_DATABASE_URL: 'daily', DRAGON_TEST_DATABASE_URL: 'daily-test',
    dragon_unknown: 'daily', COMPOSE_PROJECT_NAME: 'daily', compose_file: 'daily',
    OPENAI_API_KEY: 'secret', ANTHROPIC_API_KEY: 'secret', MODEL_TOKEN: 'secret',
    MODEL_CONFIG: 'daily', DOTENV_CONFIG_PATH: 'daily', PYTHONPATH: 'daily',
    NODE_OPTIONS: '--require daily', DOCKER_HOST: 'daily', RANDOM_CONFIG: 'daily',
  };
  assert.deepEqual(utility('fixtureChildEnvironment')(input), {
    Path: 'tools', SystemRoot: 'windows', TEMP: 'temp', HOME: 'home',
  });
  assert.equal(input.DRAGON_DATABASE_URL, 'daily');
});

test('fixture identity uses a sibling evidence directory and a random fixture-only password', () => {
  const make = utility('createFixtureIdentity');
  const worktreeRoot = resolve(root, '..', '.temp', 'DragonRadar-Fixture');
  const a = make({ root: worktreeRoot, runId });
  const b = make({ root: worktreeRoot, runId: '123-457-abcdef12' });
  assert.equal(a.state, resolve(worktreeRoot, '..', 'reset-contract-runtime', runId));
  assert.equal(a.project, project);
  assert.match(a.password, /^[a-f0-9]{48}$/);
  assert.notEqual(a.password, b.password);
  assert.equal(a.databaseUrl, `postgresql+psycopg://dragon_radar:${a.password}@db:5432/dragon_test`);
  for (const unsafe of ['daily', '../daily', '', '123-456-ABCDEF12']) {
    assert.throws(() => make({ root, runId: unsafe }), /identity/);
  }
});

test('main-shaped repository root also puts evidence under sibling .temp', () => {
  const mainRoot = resolve(root, '..', '..', 'DragonRadar');
  const identity = utility('createFixtureIdentity')({ root: mainRoot, runId });
  assert.equal(identity.state, resolve(mainRoot, '..', '.temp', 'reset-contract-runtime', runId));
});

test('fixture env file disables provider credentials explicitly', () => {
  const identity = utility('createFixtureIdentity')({ root, runId });
  const text = utility('fixtureEnvironmentFile')(identity);
  assert.match(text, new RegExp(`DRAGON_DB_PASSWORD=${identity.password}`));
  for (const key of ['DRAGON_AI_PROVIDER', 'DRAGON_AI_TOKEN', 'DRAGON_AI_MODEL_VERSION']) {
    assert.match(text, new RegExp(`^${key}=$`, 'm'));
  }
  assert.match(text, /^DRAGON_OWNER_SECRETS_DIR=.*owner-secrets$/m);
  assert.doesNotMatch(text, /dragon_radar$/m);
});

test('unsafe database URL, host and credentials are rejected before configuration', () => {
  const check = utility('assertFixtureDatabase');
  const password = 'a'.repeat(48);
  assert.doesNotThrow(() => check(`postgresql+psycopg://dragon_radar:${password}@db:5432/dragon_test`, password));
  for (const url of [
    `postgresql+psycopg://dragon_radar:${password}@db:5432/dragon_radar`,
    `postgresql+psycopg://dragon_radar:${password}@localhost:5432/dragon_test`,
    `postgresql+psycopg://dragon_radar:${password}@db:5432/dragon_test?options=daily`,
    'postgresql+psycopg://dragon_radar:daily@db:5432/dragon_test',
  ]) assert.throws(() => check(url, password), /fixture database/);
});

test('Windows ACL command owns by current SID, replaces inheritance and asserts the ACL', () => {
  const command = utility('windowsPrivateAclCommand')("E:\\fixture's\\secret", true);
  assert.match(command, /WindowsIdentity.*GetCurrent/);
  assert.match(command, /\.User/);
  assert.match(command, /SetOwner\(\$sid\)/);
  assert.match(command, /SetAccessRuleProtection\(\$true,\s*\$false\)/);
  assert.match(command, /FullControl/);
  assert.match(command, /ContainerInherit.*ObjectInherit/);
  assert.match(command, /AreAccessRulesProtected/);
  assert.match(command, /GetOwner.*SecurityIdentifier/);
  assert.match(command, /IsInherited/);
  assert.match(command, /throw/);
  assert.match(command, /fixture''s/);
});

test('private file contains no secret until ACL assertion succeeds', t => {
  const path = resolve(scratch(t), 'secret.env');
  const calls = [];
  const proof = utility('writePrivateFile')(path, 'sensitive', {
    platform: 'win32',
    exec(command, args) {
      calls.push([command, args]);
      assert.equal(readFileSync(path, 'utf8'), '');
      return { status: 0, stdout: '{"ownerSid":"S-1-5-21-123","protected":true}', stderr: '' };
    },
  });
  assert.equal(readFileSync(path, 'utf8'), 'sensitive');
  assert.equal(proof.ownerSid, 'S-1-5-21-123');
  assert.equal(calls.length, 1);
  assert.match(calls[0][0], /powershell/i);
  assert.ok(calls[0][1].includes('-NonInteractive'));
});

test('failed or unproven Windows ACL leaves no private file', t => {
  const path = resolve(scratch(t), 'secret.env');
  for (const result of [
    { status: 1, stderr: 'denied' },
    { status: 0, stdout: '{}' },
    { status: 0, stdout: '{"ownerSid":"wrong","protected":true}' },
  ]) {
    assert.throws(() => utility('writePrivateFile')(path, 'sensitive', {
      platform: 'win32', exec: () => result,
    }), /ACL/);
    assert.equal(existsSync(path), false);
  }
});

test('private directory and file have real native permission proof', t => {
  const directory = resolve(scratch(t), 'private');
  const dirProof = utility('createPrivateDirectory')(directory);
  const path = resolve(directory, 'compose.env');
  const proof = utility('writePrivateFile')(path, 'fixture');
  assert.equal(readFileSync(path, 'utf8'), 'fixture');
  if (process.platform === 'win32') {
    assert.match(proof.ownerSid, /^S-1-/);
    assert.equal(proof.protected, true);
    assert.equal(dirProof.ownerSid, proof.ownerSid);
    const command = utility('windowsPrivateAclCommand')(path, false);
    const actual = spawnSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', command], {
      encoding: 'utf8', env: utility('fixtureChildEnvironment')(), timeout: 30_000,
    });
    assert.equal(actual.status, 0, actual.stderr);
    assert.equal(JSON.parse(actual.stdout).ownerSid, proof.ownerSid);
  } else {
    assert.equal(statSync(directory).mode & 0o777, 0o700);
    assert.equal(statSync(path).mode & 0o777, 0o600);
    assert.equal(proof.mode, '0600');
  }
});

test('existing private files can be updated and copied files get immediate ACL proof', t => {
  const directory = resolve(scratch(t), 'private');
  utility('createPrivateDirectory')(directory);
  const path = resolve(directory, 'authority');
  utility('writePrivateFile')(path, 'first');
  utility('writePrivateFile')(path, 'second');
  assert.equal(readFileSync(path, 'utf8'), 'second');
  const proof = utility('protectPrivateFile')(path);
  assert.ok(proof.ownerSid || proof.mode === '0600');
});

test('production isolation uses fixture database/password, isolated env and native private writes', () => {
  const source = readFileSync(resolve(root, 'web/scripts/test-production-isolation.mjs'), 'utf8');
  assert.match(source, /fixtureChildEnvironment/);
  assert.match(source, /writePrivateFile\(\s*envFile/);
  assert.match(source, /writePrivateFile\(authorityFile/);
  assert.match(source, /protectPrivateFile\(pairFile\)/);
  assert.match(source, /initializeFixtureEvidence\(identity,[^\n]*privateState: true/);
  assert.match(source, /composeOverride\(\{[\s\S]*?password/);
  assert.match(source, /DRAGON_AI_PROVIDER=/);
  assert.doesNotMatch(source, /mode:\s*0o600/);
  assert.match(source, /ignoreHTTPSErrors: true/);
});

test('POSIX proof requires actual chmod and stat, not a requested write mode', t => {
  const path = resolve(scratch(t), 'posix.env');
  const calls = [];
  const proof = utility('writePrivateFile')(path, 'fixture', {
    platform: 'linux',
    chmod(file, mode) { calls.push([file, mode]); },
    stat(file) { assert.equal(file, path); return { mode: 0o600 }; },
  });
  assert.deepEqual(calls, [[path, 0o600]]);
  assert.equal(proof.mode, '0600');
  const denied = resolve(dirname(path), 'denied.env');
  assert.throws(() => utility('writePrivateFile')(denied, 'fixture', {
    platform: 'linux', chmod() {}, stat: () => ({ mode: 0o644 }),
  }), /chmod proof/);
  assert.equal(existsSync(denied), false);
});

function dockerDouble({ wrongLabel = false, lingering = false, downFailure = false, ownProject = project } = {}) {
  const commands = [];
  let down = false;
  return {
    commands,
    run(command, args) {
      commands.push([command, ...args]);
      const kind = args[0];
      if (args.includes('inspect')) {
        return JSON.stringify([{
          Id: 'own-id', Name: 'own-volume',
          Config: { Labels: { 'com.docker.compose.project': wrongLabel ? 'daily' : ownProject } },
          Labels: { 'com.docker.compose.project': wrongLabel ? 'daily' : ownProject },
        }]);
      }
      assert.ok(args.includes(`label=com.docker.compose.project=${ownProject}`));
      return down && !lingering ? '' : `${kind === 'volume' ? 'own-volume' : 'own-id'}\n`;
    },
    compose(...args) {
      commands.push(['compose', ...args]);
      down = true;
      if (downFailure) throw new Error('down failed');
    },
  };
}

test('cleanup rejects the wrong project identity before any Docker call', () => {
  const docker = dockerDouble();
  assert.throws(() => utility('cleanupFixtureProject')({
    project: 'dragon-radar-reset', runId, ...docker,
  }), /identity/);
  assert.deepEqual(docker.commands, []);
});

test('cleanup checks all resource labels before removing only its own Compose project', () => {
  const docker = dockerDouble();
  const result = utility('cleanupFixtureProject')({ project, runId, ...docker });
  assert.equal(result.status, 'removed');
  assert.deepEqual(result.before.containers, ['own-id']);
  assert.deepEqual(result.before.volumes, ['own-volume']);
  assert.deepEqual(result.after, { containers: [], volumes: [], networks: [] });
  const downIndex = docker.commands.findIndex(args => args[0] === 'compose');
  assert.equal(docker.commands.slice(0, downIndex).filter(args => args.includes('inspect')).length, 3);
  assert.deepEqual(docker.commands[downIndex], ['compose', 'down', '--volumes', '--remove-orphans', '--timeout', '10']);
});

test('cleanup refuses a foreign label even when the discovery query claimed ownership', () => {
  const docker = dockerDouble({ wrongLabel: true });
  assert.throws(() => utility('cleanupFixtureProject')({ project, runId, ...docker }), /identity/);
  assert.equal(docker.commands.some(args => args[0] === 'compose'), false);
});

test('cleanup records actual failure and residual resources rather than claiming removed', () => {
  for (const options of [{ lingering: true }, { downFailure: true }]) {
    const result = utility('cleanupFixtureProject')({ project, runId, ...dockerDouble(options) });
    assert.equal(result.status, 'failed');
    assert.ok(result.error);
    assert.ok(result.after.containers.length || options.downFailure);
  }
});

test('isolation cleanup accepts only its exact controlled kind and still verifies labels and residuals', () => {
  const ownProject = `dragon-radar-isolation-e2e-${runId}`;
  const cleanup = utility('cleanupFixtureProject');
  assert.equal(cleanup({
    project: ownProject, runId, kind: 'isolation', ...dockerDouble({ ownProject }),
  }).status, 'removed');
  for (const options of [{ wrongLabel: true }, { lingering: true }, { downFailure: true }]) {
    const docker = dockerDouble({ ownProject, ...options });
    if (options.wrongLabel) {
      assert.throws(() => cleanup({ project: ownProject, runId, kind: 'isolation', ...docker }), /identity/);
      assert.equal(docker.commands.some(args => args[0] === 'compose'), false);
    } else {
      assert.equal(cleanup({ project: ownProject, runId, kind: 'isolation', ...docker }).status, 'failed');
    }
  }
  for (const [candidate, kind] of [
    [ownProject, undefined], [project, 'isolation'], [ownProject, 'daily'],
    [`${ownProject}-foreign`, 'isolation'], ['dragon-radar-reset', 'isolation'],
  ]) {
    const docker = dockerDouble();
    assert.throws(() => cleanup({ project: candidate, runId, kind, ...docker }), /identity/);
    assert.deepEqual(docker.commands, []);
  }
});

test('sanitizer redacts quoted JSON credentials in objects and command output without losing negative fields', () => {
  const payload = {
    token: 'owner-token', pairing_code: 'pairing-value', csrf_token: 'csrf-value',
    nested: { API_KEY: 'api-value', password: 'password-value' },
    token_count: 7, tokenizer: 'keep-tokenizer', pairing_code_hint: 'keep-hint',
    secretCleanupError: 'keep-error', status: 'failed',
  };
  const sanitize = utility('sanitizeFixtureEvidence');
  const object = JSON.parse(sanitize(payload));
  assert.equal(object.token, '[REDACTED]');
  assert.equal(object.pairing_code, '[REDACTED]');
  for (const value of [payload, JSON.stringify(payload), {
    stdout: JSON.stringify(payload), stderr: '{"token":"escaped \\"secret\\" with spaces"}',
  }]) {
    const text = sanitize(value);
    assert.doesNotMatch(text, /owner-token|pairing-value|csrf-value|api-value|password-value|escaped/);
    assert.match(text, /keep-tokenizer/);
    assert.match(text, /keep-hint/);
    assert.match(text, /keep-error/);
    if (typeof value !== 'string') assert.doesNotThrow(() => JSON.parse(text));
  }
  assert.equal(object.token_count, 7);
});

test('all generated owner secret files get individual native permission proof, including nested files', t => {
  const directory = resolve(scratch(t), 'owner-secrets');
  mkdirSync(resolve(directory, 'nested'), { recursive: true });
  for (const name of ['session', 'pairing', 'nested/extra']) writeFileSync(resolve(directory, name), 'private');
  const proofs = utility('protectOwnerSecretFiles')(directory);
  assert.deepEqual(Object.keys(proofs).sort(), ['nested/extra', 'pairing', 'session']);
  for (const proof of Object.values(proofs)) {
    if (process.platform === 'win32') {
      assert.match(proof.ownerSid, /^S-1-/);
      assert.equal(proof.protected, true);
    } else assert.equal(proof.mode, '0600');
  }
  assert.throws(() => utility('protectOwnerSecretFiles')(resolve(directory, 'missing')), /ENOENT/);
});

test('generated file ACL failure is fail closed, not replaced by directory proof', t => {
  const directory = scratch(t);
  writeFileSync(resolve(directory, 'generated'), 'private');
  assert.throws(() => utility('protectOwnerSecretFiles')(directory, {
    platform: 'win32', exec: () => ({ status: 0, stdout: '{}' }),
  }), /ACL/);
});

test('evidence redacts credentials and retains manifest/logs while removing only secrets', t => {
  const state = scratch(t);
  const identity = utility('createFixtureIdentity')({ root, runId, evidenceRoot: state });
  utility('initializeFixtureEvidence')(identity, { head: 'test-head' });
  mkdirSync(identity.secretsDir);
  writeFileSync(resolve(identity.secretsDir, 'pairing-code'), 'private');
  writeFileSync(identity.envFile, identity.password);
  writeFileSync(identity.overrideFile, identity.password);
  const record = utility('finishFixtureEvidence')(identity, {
    error: new Error(`failed ${identity.password} postgresql://user:private@db/dragon_test DRAGON_AI_TOKEN=token`),
    cleanup: { status: 'removed', before: {}, after: {} },
  });
  assert.equal(record.status, 'failed');
  const manifest = readFileSync(resolve(identity.state, 'manifest.json'), 'utf8');
  assert.doesNotMatch(manifest, new RegExp(`${identity.password}|:private@|=token`));
  assert.equal(existsSync(identity.envFile), false);
  assert.equal(existsSync(identity.overrideFile), false);
  assert.equal(existsSync(identity.secretsDir), false);
  assert.equal(existsSync(resolve(identity.state, 'commands.jsonl')), true);
});

test('command evidence records sanitized output, failure and duration', t => {
  const identity = utility('createFixtureIdentity')({ root, runId, evidenceRoot: scratch(t) });
  utility('initializeFixtureEvidence')(identity, {});
  utility('recordFixtureCommand')(identity, {
    command: 'docker', args: ['exec', identity.password], status: 1, durationMs: 42,
    stdout: `postgresql://user:${identity.password}@db/dragon_test`,
    stderr: 'DRAGON_AI_TOKEN=private-token',
  });
  const text = readFileSync(resolve(identity.state, 'commands.jsonl'), 'utf8');
  assert.doesNotMatch(text, new RegExp(`${identity.password}|private-token`));
  const record = JSON.parse(text);
  assert.equal(record.status, 1);
  assert.equal(record.durationMs, 42);
});

test('failure and cleanup failure cannot retain stale success evidence', t => {
  const identity = utility('createFixtureIdentity')({ root, runId, evidenceRoot: scratch(t) });
  utility('initializeFixtureEvidence')(identity, {});
  seedCompleteMetadata(identity);
  const finish = utility('finishFixtureEvidence');
  assert.equal(finish(identity, { cleanup: { status: 'removed' }, result: { probe: 'ok' } }).status, 'passed');
  for (const outcome of [
    { error: new Error('probe failed'), cleanup: { status: 'removed' } },
    { cleanup: { status: 'failed', error: 'down failed' } },
  ]) {
    const record = finish(identity, outcome);
    assert.equal(record.status, 'failed');
    assert.equal(record.result, undefined);
    assert.equal(JSON.parse(readFileSync(resolve(identity.state, 'manifest.json'))).status, 'failed');
  }
});

test('failed evidence retains current sanitized probe and cleanup diagnostics, not stale success', t => {
  const identity = utility('createFixtureIdentity')({ root, runId, evidenceRoot: scratch(t) });
  utility('initializeFixtureEvidence')(identity, {});
  const manifest = utility('finishFixtureEvidence')(identity, {
    error: new Error('browser close failed'),
    cleanup: { status: 'failed', error: 'down failed', after: { containers: ['own-id'] } },
    result: { token: 'private-token', public: 'http://127.0.0.1:1234' },
  });
  assert.equal(manifest.status, 'failed');
  assert.equal(manifest.result, undefined);
  assert.deepEqual(manifest.probeResult, { token: '[REDACTED]', public: 'http://127.0.0.1:1234' });
  assert.equal(manifest.cleanup.error, 'down failed');
});

test('secret removal failure overrides removed resources and retains its actual error', t => {
  const identity = utility('createFixtureIdentity')({ root, runId, evidenceRoot: scratch(t) });
  utility('initializeFixtureEvidence')(identity, {});
  writeFileSync(identity.envFile, 'private');
  const manifest = utility('finishFixtureEvidence')(identity, {
    cleanup: { status: 'removed' }, result: { probe: 'ok' },
  }, {
    remove(path, options) {
      if (path === identity.envFile) throw new Error('secret removal denied');
      rmSync(path, options);
    },
  });
  assert.equal(manifest.status, 'failed');
  assert.equal(manifest.secretsRemoved, false);
  assert.equal(manifest.secretCleanupError, 'secret removal denied');
  assert.match(manifest.error, /secret removal denied/);
});

test('source evidence hashes dirty raw bytes including untracked files without recording their content', t => {
  const directory = scratch(t);
  const bytes = Buffer.from([0xff, 13, 10, 0, 65]);
  writeFileSync(resolve(directory, 'dirty.bin'), bytes);
  writeFileSync(resolve(directory, 'new.txt'), 'token-not-to-record');
  const snapshot = utility('captureFixtureSource')(directory, (_command, args) => {
    if (args.includes('rev-parse')) return 'abc123\n';
    if (args.includes('--others')) return 'new.txt\0';
    return 'dirty.bin\0';
  });
  assert.equal(snapshot.head, 'abc123');
  assert.equal(snapshot.files.find(file => file.path === 'dirty.bin').sha256,
    createHash('sha256').update(bytes).digest('hex'));
  assert.equal(snapshot.files.length, 2);
  assert.match(snapshot.dirtyRawDigest, /^[a-f0-9]{64}$/);
  assert.doesNotMatch(JSON.stringify(snapshot), /token-not-to-record/);
});

test('source evidence binds both current lockfiles even when Git reports them clean', t => {
  const directory = scratch(t);
  mkdirSync(resolve(directory, 'web'));
  const locks = { 'uv.lock': Buffer.from([0xff, 13, 10]), 'web/package-lock.json': Buffer.from('{"lockfileVersion":3}\r\n') };
  for (const [path, bytes] of Object.entries(locks)) writeFileSync(resolve(directory, path), bytes);
  const snapshot = utility('captureFixtureSource')(directory, (_command, args) =>
    args.includes('rev-parse') ? 'abc123\n' : '');
  assert.deepEqual(snapshot.lockfiles, Object.entries(locks).map(([path, bytes]) => ({
    path, bytes: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex'),
  })));
  assert.deepEqual(snapshot.files, []);
  writeFileSync(resolve(directory, 'uv.lock'), 'changed');
  const changed = utility('captureFixtureSource')(directory, () => '');
  assert.notEqual(changed.lockfiles[0].sha256, snapshot.lockfiles[0].sha256);
});

test('C5 clean HEAD source captures every tracked input, not only dirty paths', t => {
  const directory = scratch(t);
  writeFileSync(resolve(directory, 'clean.txt'), 'clean input');
  const snapshot = utility('captureFixtureSource')(directory, (_command, args) => {
    if (args.includes('rev-parse')) return 'abc123\n';
    if (args[0] === 'ls-files' && !args.includes('--others')) return 'clean.txt\0';
    return '';
  });
  assert.deepEqual(snapshot.files, [{
    path: 'clean.txt', bytes: 11,
    sha256: createHash('sha256').update('clean input').digest('hex'),
  }]);
});

test('C5 native C3 manifest branch explicitly imports its filesystem operations', () => {
  const source = readFileSync(resolve(root, 'web/scripts/test-reset-contract-runtime.mjs'), 'utf8');
  const filesystemImport = source.match(/import\s*\{([^}]+)\}\s*from\s*['"]node:fs['"]/);
  assert(filesystemImport, 'native manifest branch requires a node:fs import');
  assert.match(filesystemImport[1], /\breadFileSync\b/);
  assert.match(filesystemImport[1], /\bwriteFileSync\b/);
});

test('actual Docker client/server and Compose identities are queried through the supplied runner', t => {
  const identity = utility('createFixtureIdentity')({ root, runId, evidenceRoot: scratch(t) });
  utility('initializeFixtureEvidence')(identity, completeSource());
  const calls = [];
  utility('recordFixtureToolIdentities')(identity, (command, args, options) => {
    calls.push([command, args, options]);
    return args[0] === 'version'
      ? '{"Client":{"Version":"29.9.2"},"Server":{"Version":"29.9.3"}}\n' : '5.6.1\n';
  });
  const manifest = JSON.parse(readFileSync(resolve(identity.state, 'manifest.json')));
  assert.deepEqual(calls, [
    ['docker', ['version', '--format', '{{json .}}'], { timeout: 30_000 }],
    ['docker', ['compose', 'version', '--short'], { timeout: 30_000 }],
  ]);
  assert.deepEqual(manifest.tools.docker, { client: '29.9.2', server: '29.9.3' });
  assert.equal(manifest.tools.compose, '5.6.1');
  assert.equal(manifest.tools.node, process.version);
  assert.equal(manifest.tools.platform, process.platform);
});

test('identity collection rejects missing or pending versions before runtime', t => {
  const identity = utility('createFixtureIdentity')({ root, runId, evidenceRoot: scratch(t) });
  utility('initializeFixtureEvidence')(identity, completeSource());
  for (const docker of ['', '{}', '{"Client":{"Version":"pending"},"Server":{"Version":"29.9.2"}}',
    '{"Client":{"Version":"29.9.2"}}']) {
    assert.throws(() => utility('recordFixtureToolIdentities')(identity, () => docker), /identity/i);
  }
  for (const compose of ['', '  ', 'pending', 'C0 pending']) {
    assert.throws(() => utility('recordFixtureToolIdentities')(identity, (_command, args) =>
      args[0] === 'version' ? '{"Client":{"Version":"29.9.2"},"Server":{"Version":"29.9.2"}}' : compose), /identity/i);
  }
});

test('transport-only C0 explicitly records browser not applicable with a reason', t => {
  const identity = utility('createFixtureIdentity')({ root, runId, evidenceRoot: scratch(t) });
  const manifest = utility('initializeFixtureEvidence')(identity, completeSource(), { browserMode: 'transport-only' });
  assert.equal(manifest.browserMode, 'transport-only');
  assert.equal(manifest.tools.chromium.status, 'not-applicable');
  assert.match(manifest.tools.chromium.reason, /transport-only.*C1/i);
  utility('recordFixtureToolIdentities')(identity, (_command, args) =>
    args[0] === 'version' ? '{"Client":{"Version":"29.9.2"},"Server":{"Version":"29.9.2"}}' : '5.6.1');
  const finished = utility('finishFixtureEvidence')(identity, { cleanup: { status: 'removed' }, result: { probe: 'ok' } });
  assert.equal(finished.status, 'passed');
  assert.equal(finished.inputs.version, 'C1 pending');
  assert.deepEqual(finished.inputs.publications, []);
});

test('browser identity records selected channel before launch and actual version after launch', t => {
  const identity = utility('createFixtureIdentity')({ root, runId, kind: 'isolation', evidenceRoot: scratch(t) });
  utility('initializeFixtureEvidence')(identity, completeSource());
  const record = utility('recordFixtureBrowserIdentity');
  record(identity, { channel: 'msedge' });
  let manifest = JSON.parse(readFileSync(resolve(identity.state, 'manifest.json')));
  assert.deepEqual(manifest.tools.chromium, { status: 'pending', channel: 'msedge' });
  record(identity, { channel: 'msedge', version: '142.0.1234.5' });
  manifest = JSON.parse(readFileSync(resolve(identity.state, 'manifest.json')));
  assert.deepEqual(manifest.tools.chromium, { status: 'recorded', channel: 'msedge', version: '142.0.1234.5' });
});

test('missing metadata fails finalization while retaining cleanup and sanitized current probes', t => {
  const identity = utility('createFixtureIdentity')({ root, runId, evidenceRoot: scratch(t) });
  utility('initializeFixtureEvidence')(identity, {});
  writeFileSync(identity.envFile, 'private');
  const manifest = utility('finishFixtureEvidence')(identity, {
    cleanup: { status: 'removed' }, result: { token: 'private-token', probe: 'ok' },
  });
  assert.equal(manifest.status, 'failed');
  assert.match(manifest.error, /C0.*identity|C0.*metadata/i);
  assert.ok(manifest.metadataError);
  assert.equal(manifest.result, undefined);
  assert.equal(manifest.probeResult.token, '[REDACTED]');
  assert.equal(manifest.cleanup.status, 'removed');
  assert.equal(existsSync(identity.envFile), false);
});

test('metadata schema rejects empty/pending applicable proof and never retains stale PASS', t => {
  const identity = utility('createFixtureIdentity')({ root, runId, evidenceRoot: scratch(t) });
  utility('initializeFixtureEvidence')(identity, completeSource());
  const file = resolve(identity.state, 'manifest.json');
  const invalid = [
    m => { delete m.tools; },
    m => { m.tools.node = ''; },
    m => { m.tools.node = 'pending'; },
    m => { m.tools.platform = ''; },
    m => { m.tools.platform = 'pending'; },
    m => { m.tools.docker = 'pending'; },
    m => { m.tools.docker.client = ''; },
    m => { m.tools.docker.client = 'pending'; },
    m => { m.tools.docker.server = ''; },
    m => { m.tools.docker.server = 'pending'; },
    m => { m.tools.compose = ' '; },
    m => { m.tools.compose = 'pending'; },
    m => { m.tools.compose = 'C0 pending'; },
    m => { m.tools.chromium.status = 'pending'; },
    m => { m.tools.chromium = 'pending'; },
    m => { m.tools.chromium.channel = ''; },
    m => { m.tools.chromium.channel = 'pending'; },
    m => { m.tools.chromium.version = ''; },
    m => { m.tools.chromium.version = 'pending'; },
    m => { m.tools.chromium = { status: 'not-applicable', reason: 'bypass' }; },
    m => { m.browserMode = 'pending'; },
    m => { delete m.source.lockfiles; },
    m => { m.source = null; },
    m => { m.source.lockfiles = []; },
    m => { m.source.lockfiles.pop(); },
    m => { m.source.lockfiles[0].path = 'daily.lock'; },
    m => { m.source.lockfiles[0].sha256 = ''; },
    m => { m.source.lockfiles[0].sha256 = 'pending'; },
    m => { m.source.lockfiles[0].bytes = 0; },
    m => { m.source.lockfiles[0].missing = true; },
    m => {
      m.browserMode = 'transport-only';
      m.tools.chromium = { status: 'not-applicable', reason: '' };
    },
    m => {
      m.browserMode = 'transport-only';
      m.tools.chromium = { status: 'not-applicable', reason: 'pending' };
    },
  ];
  for (const mutate of invalid) {
    seedCompleteMetadata(identity);
    const repaired = utility('finishFixtureEvidence')(identity, {
      cleanup: { status: 'removed' }, result: { probe: 'previous success' },
    });
    assert.equal(repaired.status, 'passed');
    assert.equal(repaired.metadataError, undefined);
    const stale = JSON.parse(readFileSync(file));
    mutate(stale);
    writeFileSync(file, JSON.stringify(stale));
    const finished = utility('finishFixtureEvidence')(identity, {
      cleanup: { status: 'removed' }, result: { probe: 'current probe' },
    });
    assert.equal(finished.status, 'failed', mutate.toString());
    assert.ok(finished.metadataError);
    assert.equal(finished.result, undefined);
    assert.deepEqual(finished.probeResult, { probe: 'current probe' });
    assert.equal(JSON.parse(readFileSync(file)).status, 'failed');
  }
});

test('metadata failure preserves original errors and secret cleanup errors', t => {
  const identity = utility('createFixtureIdentity')({ root, runId, evidenceRoot: scratch(t) });
  utility('initializeFixtureEvidence')(identity, {});
  writeFileSync(identity.envFile, 'private');
  const manifest = utility('finishFixtureEvidence')(identity, {
    error: new Error('original probe failed'), cleanup: { status: 'failed', error: 'down failed' },
  }, {
    remove(path, options) {
      if (path === identity.envFile) throw new Error('secret removal denied');
      rmSync(path, options);
    },
  });
  assert.equal(manifest.status, 'failed');
  assert.equal(manifest.error, 'original probe failed');
  assert.equal(manifest.cleanup.error, 'down failed');
  assert.equal(manifest.secretCleanupError, 'secret removal denied');
  assert.ok(manifest.metadataError);
});

test('production isolation cannot exempt its applicable browser as transport-only', t => {
  const identity = utility('createFixtureIdentity')({ root, runId, kind: 'isolation', evidenceRoot: scratch(t) });
  utility('initializeFixtureEvidence')(identity, completeSource(), { browserMode: 'transport-only' });
  utility('recordFixtureToolIdentities')(identity, (_command, args) =>
    args[0] === 'version' ? '{"Client":{"Version":"29.9.2"},"Server":{"Version":"29.9.2"}}' : '5.6.1');
  const manifest = utility('finishFixtureEvidence')(identity, { cleanup: { status: 'removed' } });
  assert.equal(manifest.status, 'failed');
  assert.match(manifest.metadataError, /browser/i);
});

test('Compose query failure retains collected Docker identity and cannot finalize as success', t => {
  const identity = utility('createFixtureIdentity')({ root, runId, evidenceRoot: scratch(t) });
  utility('initializeFixtureEvidence')(identity, completeSource(), { browserMode: 'transport-only' });
  let error;
  try {
    utility('recordFixtureToolIdentities')(identity, (_command, args) => {
      if (args[0] === 'compose') throw new Error('compose version command failed');
      return '{"Client":{"Version":"29.9.2"},"Server":{"Version":"29.9.3"}}';
    });
  } catch (failure) { error = failure; }
  const manifest = utility('finishFixtureEvidence')(identity, { error, cleanup: { status: 'removed' } });
  assert.equal(manifest.status, 'failed');
  assert.equal(manifest.error, 'compose version command failed');
  assert.deepEqual(manifest.tools.docker, { client: '29.9.2', server: '29.9.3' });
  assert.equal(manifest.tools.compose, 'pending');
  assert.match(manifest.metadataError, /compose/);
});

function c1Publication(index) {
  const snapshot_id = `00000000-0000-4000-8000-00000000000${index}`;
  return {
    receipt: { snapshot_id, version: index, published_at: `2026-11-19T10:00:0${index}+00:00` },
    archive: {
      snapshot_id, version: index, published_at: `2026-11-19T10:00:0${index}+00:00`,
      trade_date: '2026-09-08', sector_id: `SYNTH-SECTOR-${index}`,
      members: [`SYNTH-C1-${index}`], security_id: `SYNTH-C1-${index}`,
      article_id: `00000000-0000-4000-8000-00000000001${index}`,
      source_version: `s${index}`, available_at: '2026-09-09T12:00:00+00:00',
      raw_sha256: `${index}`.repeat(64),
    },
  };
}

test('C1 positive binding rejects absent, guessed, mismatched or duplicate publications', () => {
  const validate = utility('assertContractPublications');
  const publications = [c1Publication(1), c1Publication(2)];
  assert.doesNotThrow(() => validate({ synthetic: true, publications }));
  for (const input of [
    undefined, {}, { synthetic: false, publications },
    { synthetic: true, publications: [] },
    { synthetic: true, publications: [publications[0]] },
    { synthetic: true, publications: [publications[0], publications[0]] },
    { synthetic: true, publications: [{ ...publications[0], receipt: {} }, publications[1]] },
    { synthetic: true, publications: [{ ...publications[0], archive: {
      ...publications[0].archive, snapshot_id: publications[1].receipt.snapshot_id,
    } }, publications[1]] },
  ]) assert.throws(() => validate(input), /C1.*publication/i);
});

test('C1 positive binding never substitutes current members for archived sector facts', () => {
  const validate = utility('assertContractPublications');
  for (const missing of ['sector_id', 'members', 'security_id', 'article_id', 'source_version', 'available_at', 'raw_sha256']) {
    const publications = [c1Publication(1), c1Publication(2)];
    delete publications[0].archive[missing];
    publications[0].current_members = ['SYNTH-C1-1'];
    assert.throws(() => validate({ synthetic: true, publications }), /C1.*archive/i, missing);
  }
  const publications = [c1Publication(1), c1Publication(2)];
  publications[0].archive.members = ['unrelated'];
  assert.throws(() => validate({ synthetic: true, publications }), /C1.*archive/i);
});

test('C1 citation requires exact bound archived manifest field, ready draft and real task identity', () => {
  const validate = utility('assertContractCitation');
  const publication = c1Publication(1);
  const entry = { evidence_id: 'ev-real', evidence_position: '/input/article/content', value: 'Synthetic archived article.' };
  const proof = {
    snapshot_id: publication.receipt.snapshot_id,
    task_id: '00000000-0000-4000-8000-000000000022', draft_revision: 1,
    status: 'ready', article_id: publication.archive.article_id,
    manifest: {
      publication: { snapshot_id: publication.receipt.snapshot_id },
      input: { article: { article_id: publication.archive.article_id, content: entry.value,
        revision_id: '00000000-0000-4000-8000-000000000033', revision: 1 } },
      evidence: [entry],
    },
    citation: entry,
    resolved: {
      status: 'ready', value: entry.value, task_id: '00000000-0000-4000-8000-000000000022',
      snapshot_id: publication.receipt.snapshot_id, revision: 1, ...entry,
      locator: { kind: 'news_article_field', article_id: publication.archive.article_id,
        article_revision_id: '00000000-0000-4000-8000-000000000033', article_revision: 1, field: 'content' },
    },
  };
  assert.doesNotThrow(() => validate(publication, proof));
  for (const mutate of [
    p => { p.status = 'pending'; }, p => { p.task_id = ''; },
    p => { p.draft_revision = 0; }, p => { p.manifest.evidence = []; },
    p => { delete p.manifest.input.article.content; },
    p => { p.manifest.input.article.article_id = 'unrelated-private-article'; },
    p => { p.manifest.publication.snapshot_id = c1Publication(2).receipt.snapshot_id; },
    p => { p.citation.value = 'current unarchived content'; },
    p => { p.resolved.value = 'current unarchived content'; },
    p => { p.resolved.locator.article_id = 'unrelated-private-article'; },
    p => { p.resolved.locator.field = 'summary'; },
    p => { p.resolved.task_id = '00000000-0000-4000-8000-000000000044'; },
    p => { p.resolved.snapshot_id = c1Publication(2).receipt.snapshot_id; },
    p => { p.resolved.revision = 2; },
    p => { p.resolved.evidence_id = 'unrelated-evidence'; },
    p => { p.resolved.evidence_position = '/input/article/summary'; },
    p => { p.resolved.locator.article_revision_id = '00000000-0000-4000-8000-000000000055'; },
    p => { p.resolved.locator.article_revision = 2; },
  ]) {
    const broken = structuredClone(proof);
    mutate(broken);
    assert.throws(() => validate(publication, broken), /C1.*citation/i);
  }
});

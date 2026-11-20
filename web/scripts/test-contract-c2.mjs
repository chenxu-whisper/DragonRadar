import assert from 'node:assert/strict';
import { createHash, randomUUID } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { cpSync, existsSync, mkdirSync, readFileSync, writeFileSync, readdirSync, statSync } from 'node:fs';
import { resolve, dirname, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { variants, assertBuildEvidence, assertRunEvidence, digest, finalResult } from './contract-c2-support.mjs';
import { captureFixtureSource } from './contract-synthetic-fixture.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const parent = resolve(root, '..');
assert(existsSync(parent), 'External state parent missing');
const runId = `dragon-c2-${randomUUID().slice(0, 12)}`;
const state = resolve(parent, runId);
mkdirSync(state);
const commands = [];
function run(command, args, cwd = root, timeout = 240_000, override = {}) {
  const started = Date.now();
  const result = spawnSync(command, args, { cwd, timeout, encoding: 'utf8', windowsHide: true,
    env: { ...Object.fromEntries(Object.entries(process.env).filter(([key]) =>
      !/^(VITE_|DRAGON_|COMPOSE_|.*TOKEN|.*SECRET|.*API_KEY)/i.test(key))), ...override } });
  const record = { command, args, status: result.status, durationMs: Date.now() - started,
    stdout: result.stdout, stderr: result.stderr, error: result.error?.message };
  commands.push(record);
  writeFileSync(resolve(state, 'commands.json'), JSON.stringify(commands, null, 2));
  console.log(`${command} ${args.join(' ')}: ${result.status}`);
  if (result.status !== 0) throw new Error(`Command failed; evidence: ${state}\n${result.stderr}\n${result.stdout}`);
  return result.stdout;
}
const image = 'mcr.microsoft.com/playwright:v1.63.0-noble';
const smokeOnly = process.argv.includes('--smoke');
const hostNative = process.argv.includes('--host-native');
assert(!(hostNative && smokeOnly), 'Host native subset cannot execute the Linux zoom smoke');
const grepIndex = process.argv.indexOf('--grep');
const grep = grepIndex >= 0 ? process.argv[grepIndex + 1] : undefined;
assert(grepIndex < 0 || grep, 'Missing focused grep value');
const reuseIndex = process.argv.indexOf('--builds-from');
const reuse = reuseIndex >= 0 ? resolve(process.argv[reuseIndex + 1]) : undefined;
function hashes(folder) {
  return readdirSync(folder).flatMap(name => {
    const path = resolve(folder, name);
    return statSync(path).isDirectory() ? hashes(path) : [{
      path: relative(state, path).replaceAll('\\', '/'), size: statSync(path).size,
      sha256: createHash('sha256').update(readFileSync(path)).digest('hex'),
    }];
  });
}
let id;
let failure;
let result = { schema: 'contract-c5-native-c2-v1', runId, argv: process.argv.slice(2),
  startedAt: new Date().toISOString(),
  scope: hostNative ? `ordinary host Chrome native only; NOT 320-cell/real-zoom acceptance: ${grep}`
  : grep ? `focused C2: ${grep}` : 'C2 matrix and lifecycle (mocked HTTP, not C1 TLS or C4)',
  cells: 0, fullMatrix: !grep && !smokeOnly };
const tools = { node: process.version, ...Object.fromEntries(['playwright', 'typescript', 'vite'].map(name =>
  [name, JSON.parse(readFileSync(resolve(root, 'web/node_modules', name, 'package.json'))).version])) };
const lockPackages = JSON.parse(readFileSync(resolve(root, 'web/package-lock.json'))).packages;
const readBytes = path => readFileSync(resolve(path.startsWith('builds/') ? state : root, path));
writeFileSync(resolve(state, 'tools.json'), JSON.stringify(tools, null, 2));
try {
  run(process.execPath, ['--test', 'web/scripts/contract-c2-support.test.mjs']);
  const source = captureFixtureSource(root, run).files.map(({ path, bytes, sha256 }) =>
    ({ path, size: bytes, sha256 }));
  writeFileSync(resolve(state, 'source.json'), JSON.stringify(source, null, 2));
  if (!smokeOnly) {
    run(process.execPath, ['node_modules/typescript/bin/tsc', '--noEmit'], resolve(root, 'web'));
    if (reuse) {
      assert.deepEqual(JSON.parse(readFileSync(resolve(reuse, 'source.json'))), source, 'Cannot reuse builds from changed source');
      const builds = JSON.parse(readFileSync(resolve(reuse, 'builds.json')));
      assertBuildEvidence({ builds, source, tools, lockPackages, readBytes });
      assert.deepEqual(builds.map(b => b.variant).sort(), Object.keys(variants).sort(), 'Reused exact build set required');
      for (const build of builds) {
        assert.equal(build.sourceSha256, digest(source), 'Reused source binding missing');
        assert.deepEqual(build.tools, tools, 'Reused tool binding mismatch');
        for (const asset of build.assets) {
          assert.equal(createHash('sha256').update(readFileSync(resolve(reuse, asset.path))).digest('hex'), asset.sha256, 'Reused asset hash mismatch');
        }
        cpSync(resolve(reuse, 'builds', build.variant), resolve(state, 'builds', build.variant), { recursive: true });
        build.assets = hashes(resolve(state, 'builds', build.variant));
        build.assetsSha256 = digest(build.assets);
      }
      writeFileSync(resolve(state, 'builds.json'), JSON.stringify(builds, null, 2));
      writeFileSync(resolve(state, 'build-reuse.json'), JSON.stringify({ origin: reuse, sourceAndAssetHashesVerified: true }));
    } else {
      const builds = [];
      for (const [variant, flags] of Object.entries(variants)) {
        const environment = Object.fromEntries(['RESET_WORKBENCH', 'PUBLIC_SHARING', 'PRIVATE_REVIEW', 'PRIVATE_AI']
          .map((name, index) => [`VITE_${name}`, String(flags[index])]));
        environment.DRAGON_API_PROXY = 'http://127.0.0.1:1';
        const output = resolve(state, 'builds', variant);
        run(process.execPath, ['node_modules/vite/bin/vite.js', 'build', '--configLoader', 'native', '--outDir', output],
          resolve(root, 'web'), 240_000, environment);
        const assets = hashes(output);
        builds.push({ variant, flags: environment, assets, assetsSha256: digest(assets),
          sourceSha256: digest(source), lockSha256: source.find(s => s.path === 'web/package-lock.json').sha256, tools });
      }
      writeFileSync(resolve(state, 'builds.json'), JSON.stringify(builds, null, 2));
    }
    assertBuildEvidence({ builds: JSON.parse(readFileSync(resolve(state, 'builds.json'))),
      source, tools, lockPackages, readBytes });
  }
  if (hostNative) {
    assert(grep, 'Host native is bounded evidence only; require explicit --grep');
    run(process.execPath, ['node_modules/playwright/cli.js', 'test', '--config', 'playwright.contract-c2.config.ts',
      '--grep', grep], resolve(root, 'web'), 600_000, { C2_HOST_NATIVE: '1', C2_EVIDENCE_DIR: state,
        TEMP: state, TMP: state });
  } else {
  const metadata = JSON.parse(run('docker', ['image', 'inspect', image]))[0];
  assert.equal(metadata.Id, 'sha256:eff16c30e6f3f4af0a03fa4b706120d5e9b0891c344a27d64559aff5900a4a27');
  assert.equal(metadata.Os, 'linux');
  assert.equal(metadata.Architecture, 'amd64');
  writeFileSync(resolve(state, 'image.json'), JSON.stringify({
    Id: metadata.Id, RepoDigests: metadata.RepoDigests, Os: metadata.Os, Architecture: metadata.Architecture,
  }, null, 2));
  id = run('docker', ['run', '-d', '--name', runId, '--label', `dragon.contract.c2=${runId}`,
    '--mount', `type=bind,src=${state},dst=/evidence`, '--shm-size', '1g', image, 'sleep', 'infinity']).trim();
  assert.match(id, /^[a-f0-9]{64}$/);
  run('docker', ['exec', id, 'mkdir', '-p', '/work/web/tests/fixtures', '/work/web/scripts', '/work/web/node_modules/@playwright']);
  for (const path of ['package.json', 'package-lock.json', 'tests/fixtures/browser-zoom-extension',
    'scripts/contract-c2-zoom-smoke.mjs']) {
    run('docker', ['cp', resolve(root, 'web', path), `${id}:/work/web/${path}`]);
  }
  // Playwright packages are platform-independent JS; copy locked packages read-only.
  for (const name of ['@playwright/test', 'playwright', 'playwright-core']) {
    const source = resolve(root, 'web/node_modules', name);
    assert.equal(JSON.parse(readFileSync(resolve(source, 'package.json'))).version, '1.63.0');
    run('docker', ['cp', source, `${id}:/work/web/node_modules/${name}`]);
  }
  run('docker', ['exec', '-w', '/work/web', id, 'xvfb-run', '-a', 'node', 'scripts/contract-c2-zoom-smoke.mjs']);
  assert.equal(JSON.parse(readFileSync(resolve(state, 'zoom-smoke.json'))).outcome, 'passed');
  if (!smokeOnly) {
    for (const path of ['tests', 'playwright.contract-c2.config.ts', 'scripts/contract-c2-server.mjs',
      'scripts/contract-c2-support.mjs', 'scripts/contract-c2-support.d.mts']) {
      const sourcePath = resolve(root, 'web', path);
      run('docker', ['cp', statSync(sourcePath).isDirectory() ? `${sourcePath}/.` : sourcePath, `${id}:/work/web/${path}`]);
    }
    run('docker', ['exec', '-w', '/work/web', id, 'xvfb-run', '-a', 'node',
      'node_modules/playwright/cli.js', 'test', '--config', 'playwright.contract-c2.config.ts',
      ...(grep ? ['--grep', grep] : [])], root, 1_800_000);
  }
  }
  if (!smokeOnly) {
    const cells = existsSync(resolve(state, 'cells')) ? readdirSync(resolve(state, 'cells'))
      .flatMap(name => JSON.parse(readFileSync(resolve(state, 'cells', name)))) : [];
    writeFileSync(resolve(state, 'matrix.json'), JSON.stringify(cells, null, 2));
    writeFileSync(resolve(state, 'matrix-manifest.json'), JSON.stringify({
      runId, sourceSha256: createHash('sha256').update(readFileSync(resolve(state, 'source.json'))).digest('hex'),
      buildsSha256: createHash('sha256').update(readFileSync(resolve(state, 'builds.json'))).digest('hex'),
      matrixSha256: createHash('sha256').update(readFileSync(resolve(state, 'matrix.json'))).digest('hex'),
      source: 'source.json', builds: 'builds.json', matrix: 'matrix.json',
    }, null, 2));
    if (!grep) {
      assertRunEvidence({ cells, source, builds: JSON.parse(readFileSync(resolve(state, 'builds.json'))),
        tools, browser: JSON.parse(readFileSync(resolve(state, 'zoom-smoke.json'))).browser,
        lockPackages, readBytes });
      assert.equal(JSON.parse(readFileSync(resolve(state, 'public-review-boundary.json'))).outcome, 'passed');
    }
    result.cells = cells.length;
  }
} catch (error) { failure = error; }
finally {
  const cleanup = { runId, id };
  try {
    if (id) {
      const resource = JSON.parse(run('docker', ['inspect', id]))[0];
      assert.equal(resource.Config.Labels['dragon.contract.c2'], runId);
      run('docker', ['rm', '-f', id]);
    }
    if (!hostNative) {
      const remaining = run('docker', ['ps', '-aq', '--filter', `label=dragon.contract.c2=${runId}`]).trim();
      assert.equal(remaining, '');
      cleanup.containerRemoved = true;
    } else {
      const ownedProcesses = JSON.parse(run('powershell', ['-NoProfile', '-Command',
        `$state = '${state.replaceAll("'", "''")}'; @(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'chrome.exe' -and $_.CommandLine -like "*$state*" } | Select-Object ProcessId,Name,CommandLine) | ConvertTo-Json -Compress`]) || '[]');
      cleanup.ownedProcesses = Array.isArray(ownedProcesses) ? ownedProcesses : [ownedProcesses];
      assert.equal(cleanup.ownedProcesses.length, 0, 'Owned host Chrome processes remain');
      cleanup.remainingProfiles = readdirSync(state).filter(name => name.startsWith('dragon-c2-pair-'));
      assert.equal(cleanup.remainingProfiles.length, 0, 'Owned profiles remain');
      cleanup.hostProfilesVerified = true;
      cleanup.scope = 'owned host profiles closed/deleted by tests; Linux recovery cleanup still blocked';
    }
  } catch (error) {
    cleanup.failure = String(error);
    failure = new AggregateError([failure, error].filter(Boolean),
      hostNative ? 'Owned host profile cleanup failed' : 'Owned container cleanup failed');
  }
  writeFileSync(resolve(state, 'cleanup.json'), JSON.stringify(cleanup, null, 2));
  result.finishedAt = new Date().toISOString();
  writeFileSync(resolve(state, 'result.json'), JSON.stringify(finalResult(result, cleanup, failure), null, 2));
  console.log(`C2 evidence: ${state}`);
}
if (failure) throw failure;

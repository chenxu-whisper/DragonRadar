import assert from 'node:assert/strict';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { dirname, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';
import test from 'node:test';

const scripts = dirname(fileURLToPath(import.meta.url));
const scratch = resolve(scripts, '../.temp/five-build-tests');
mkdirSync(scratch, { recursive: true });
const runnerPath = resolve(scripts, 'test-five-builds.mjs');
const load = async () => {
  assert.ok(existsSync(runnerPath), 'finite runner implementation is missing');
  return import(pathToFileURL(runnerPath).href);
};
const sha = bytes => createHash('sha256').update(bytes).digest('hex');

function fixture(t) {
  const root = mkdtempSync(resolve(scratch, 'runner-fixture-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  mkdirSync(resolve(root, 'web'), { recursive: true });
  writeFileSync(resolve(root, 'web/package.json'), '{"scripts":{"build":"fixture","check:bundle":"fixture"}}');
  writeFileSync(resolve(root, 'input.txt'), 'frozen source');
  return root;
}

function product(root, { manifest = true, entry = 'import "./static.js";import("./lazy.js");' } = {}) {
  mkdirSync(resolve(root, 'web/dist/assets'), { recursive: true });
  writeFileSync(resolve(root, 'web/dist/index.html'),
    '<script type="module" src="/assets/index.js"></script><link rel="stylesheet" href="/assets/style.css">');
  for (const [name, body] of Object.entries({
    'index.js': entry, 'static.js': 'export const publicValue=1;', 'lazy.js': 'console.log("lazy");',
    'style.css': 'body{color:black}', 'unlinked.css': '/* full inventory */',
  })) writeFileSync(resolve(root, 'web/dist/assets', name), body);
  if (manifest) {
    mkdirSync(resolve(root, 'web/dist/.vite'));
    writeFileSync(resolve(root, 'web/dist/.vite/manifest.json'), JSON.stringify({
      'index.html': { file: 'assets/index.js', isEntry: true, imports: ['static'],
        dynamicImports: ['lazy'], css: ['assets/style.css'] },
      static: { file: 'assets/static.js' }, lazy: { file: 'assets/lazy.js', isDynamicEntry: true },
    }));
  }
}

function executor(root, calls, action = () => {}) {
  return (command, args, options) => {
    const stage = args.includes('build') ? 'build' : 'check';
    calls.push({ command, args, options, stage });
    if (stage === 'build') {
      assert.equal(existsSync(resolve(root, 'web/dist')), false, 'old dist was reused');
      product(root);
    }
    return action(stage, calls.length, options) ?? { status: 0, stdout: `${stage} full stdout\n`, stderr: `${stage} stderr\n` };
  };
}

const optionsFor = (root, execute) => ({ root, execute, npmCli: process.execPath });

test('five explicit flags override polluted parent; parent remains intact', async t => {
  const { runFiveBuilds } = await load();
  const root = fixture(t), calls = [];
  const parent = { ...process.env, VITE_RESET_WORKBENCH: '1', VITE_PUBLIC_SHARING: '1',
    VITE_PRIVATE_REVIEW: '1', VITE_PRIVATE_AI: '1', VITE_EVIL: 'polluted',
    DRAGON_AI_TOKEN: 'synthetic-secret-only', NODE_OPTIONS: '--inspect',
    npm_config_userconfig: 'daily-config', AI_MODEL: 'synthetic-model' };
  const before = { ...parent };
  const result = runFiveBuilds({ ...optionsFor(root, executor(root, calls)), env: parent });
  assert.equal(result.status, 'passed');
  assert.deepEqual(parent, before);
  assert.equal(calls.length, 10);
  assert.deepEqual(calls.filter(call => call.stage === 'build').map(({ options: { env } }) =>
    ['RESET_WORKBENCH', 'PUBLIC_SHARING', 'PRIVATE_REVIEW', 'PRIVATE_AI'].map(key => env[`VITE_${key}`]).join('')),
  ['0000', '0100', '1000', '0010', '1011']);
  for (const { options: { env } } of calls) {
    for (const key of ['DRAGON_AI_TOKEN', 'AI_MODEL', 'NODE_OPTIONS', 'VITE_EVIL']) {
      assert.equal(env[key], undefined, key);
    }
    assert.equal(env.npm_config_userconfig, resolve(result.runRoot, 'home/.npmrc'));
    assert.notEqual(env.npm_config_userconfig, parent.npm_config_userconfig);
  }
});

test('build/check order precedes archive; all file, log, manifest and source hashes bind evidence', async t => {
  const { runFiveBuilds } = await load();
  const root = fixture(t), calls = [];
  const result = runFiveBuilds(optionsFor(root, executor(root, calls, (stage) => {
    if (stage === 'check') {
      const run = resolve(root, '.temp/five-builds', readdirSync(resolve(root, '.temp/five-builds'))[0]);
      assert.equal(existsSync(resolve(run, ['default', 'sharing', 'reset', 'private', 'owner'][calls.length / 2 - 1], 'dist')), false);
    }
  })));
  assert.deepEqual(calls.map(call => call.stage), Array(5).fill(['build', 'check']).flat());
  for (const build of result.builds) {
    const base = resolve(result.runRoot, build.name);
    assert.equal(build.status, 'passed');
    assert.match(build.source.sha256, /^[a-f0-9]{64}$/);
    assert.ok(build.source.files.some(file => file.path === 'input.txt'));
    assert.equal(build.source.sha256, result.source.sha256);
    assert.equal(build.files.length, 7);
    for (const file of build.files) {
      const bytes = readFileSync(resolve(base, 'dist', file.path));
      assert.equal(bytes.length, file.size);
      assert.equal(sha(bytes), file.sha256);
    }
    assert.equal(build.manifestSha256, sha(readFileSync(resolve(base, 'dist/.vite/manifest.json'))));
    assert.deepEqual(build.bundle.staticAssets, ['assets/index.js', 'assets/static.js', 'assets/style.css']);
    assert.equal(build.bundle.isolation.query.passed, true);
    for (const command of build.commands) {
      assert.equal(command.exitCode, 0);
      assert.ok(command.durationMs >= 0);
      for (const log of [command.stdout, command.stderr]) {
        assert.equal(log.sha256, sha(readFileSync(resolve(result.runRoot, log.path))));
      }
    }
  }
  assert.deepEqual(JSON.parse(readFileSync(resolve(result.runRoot, 'run.json'))), result);
});

test('stale dist is removed even when build fails; failure stops before check and archive', async t => {
  const { runFiveBuilds } = await load();
  const root = fixture(t), calls = [];
  product(root);
  const parent = { ...process.env, VITE_PRIVATE_AI: '1' }, before = { ...parent };
  assert.throws(() => runFiveBuilds({ ...optionsFor(root, executor(root, calls, () =>
    ({ status: 7, stdout: 'failed stdout', stderr: 'failed stderr' }))), env: parent }), /build.*7/);
  assert.deepEqual(parent, before);
  assert.equal(calls.length, 1);
  const run = resolve(root, '.temp/five-builds', readdirSync(resolve(root, '.temp/five-builds'))[0]);
  const failed = JSON.parse(readFileSync(resolve(run, 'run.json')));
  assert.equal(failed.status, 'failed');
  assert.equal(failed.builds[0].commands[0].exitCode, 7);
  assert.equal(existsSync(resolve(run, 'default/dist')), false);
  assert.equal(existsSync(resolve(root, 'web/dist')), false);
});

test('check failure stops subsequent combinations without success artifacts', async t => {
  const { runFiveBuilds } = await load();
  const root = fixture(t), calls = [];
  assert.throws(() => runFiveBuilds(optionsFor(root, executor(root, calls, (stage, index) =>
    stage === 'check' && index === 4 ? { status: 3, stdout: '', stderr: 'checker rejection' } : undefined))), /check.*3/);
  assert.equal(calls.length, 4);
  const run = resolve(root, '.temp/five-builds', readdirSync(resolve(root, '.temp/five-builds'))[0]);
  assert.ok(existsSync(resolve(run, 'default/dist')));
  assert.equal(existsSync(resolve(run, 'sharing/dist')), false);
  assert.equal(JSON.parse(readFileSync(resolve(run, 'run.json'))).status, 'failed');
});

for (const [name, change, expected] of [
  ['missing manifest', root => rmSync(resolve(root, 'web/dist/.vite'), { recursive: true }), /manifest/],
  ['nonzero-free stale output', root => rmSync(resolve(root, 'web/dist'), { recursive: true }), /ENOENT/],
  ['source mutation', root => writeFileSync(resolve(root, 'input.txt'), 'changed'), /Source inputs changed/],
  ['unlinked isolation pollution', root => writeFileSync(resolve(root, 'web/dist/assets/unlinked.css'), '/* /api/ai/ */'), /forbidden ai/],
  ['static closure pollution', root => writeFileSync(resolve(root, 'web/dist/assets/static.js'), '"/api/query/";'), /forbidden query|static closure/],
]) {
  test(`rejects ${name} rather than archiving success`, async t => {
    const { runFiveBuilds } = await load();
    const root = fixture(t), calls = [];
    assert.throws(() => runFiveBuilds(optionsFor(root, executor(root, calls, stage => {
      if (stage === 'check') change(root);
    }))), expected);
    const run = resolve(root, '.temp/five-builds', readdirSync(resolve(root, '.temp/five-builds'))[0]);
    assert.equal(existsSync(resolve(run, 'default/dist')), false);
  });
}

test('daily dotenv configuration is rejected before any child', async t => {
  const { runFiveBuilds } = await load();
  const root = fixture(t), calls = [];
  writeFileSync(resolve(root, 'web/.env.local'), 'VITE_DAILY=synthetic');
  assert.throws(() => runFiveBuilds(optionsFor(root, executor(root, calls))), /dotenv/);
  assert.equal(calls.length, 0);
});

test('secret and model values cannot leak through logs or failure facts', async t => {
  const { runFiveBuilds } = await load();
  const root = fixture(t), calls = [];
  const secret = 'synthetic-secret-only', model = 'synthetic-model-only';
  assert.throws(() => runFiveBuilds({ ...optionsFor(root, executor(root, calls, () =>
    ({ status: 1, stdout: secret, stderr: model }))), env: { ...process.env, AI_TOKEN: secret, AI_MODEL: model } }));
  const run = resolve(root, '.temp/five-builds', readdirSync(resolve(root, '.temp/five-builds'))[0]);
  for (const path of ['run.json', 'default/build.stdout.log', 'default/build.stderr.log']) {
    const text = readFileSync(resolve(run, path), 'utf8');
    assert.equal(text.includes(secret), false);
    assert.equal(text.includes(model), false);
  }
});

test('unique runs never reuse previous output', async t => {
  const { runFiveBuilds } = await load();
  const root = fixture(t);
  const one = runFiveBuilds(optionsFor(root, executor(root, [])));
  const two = runFiveBuilds(optionsFor(root, executor(root, [])));
  assert.notEqual(one.runRoot, two.runRoot);
});

test('real child invocation uses node/npm CLI without a shell and explicit manifest argument', async t => {
  const { runFiveBuilds } = await load();
  const root = fixture(t);
  // This fixture CLI is not npm/vite and does not build the product.
  const npmCli = resolve(root, 'npm-cli.cjs');
  writeFileSync(npmCli, `
    const fs = require('node:fs');
    const assert = require('node:assert/strict');
    assert.equal(process.env.DRAGON_AI_TOKEN, undefined);
    assert.equal(process.env.NODE_OPTIONS, undefined);
    if (process.argv.includes('build')) {
      assert.deepEqual(process.argv.slice(2), ['run','build','--','--manifest']);
      fs.mkdirSync('dist/assets', {recursive:true});
      fs.mkdirSync('dist/.vite', {recursive:true});
      fs.writeFileSync('dist/index.html', '<script type="module" src="/assets/index.js"></script>');
      fs.writeFileSync('dist/assets/index.js', 'console.log("fixture");');
      fs.writeFileSync('dist/.vite/manifest.json', JSON.stringify({'index.html':{file:'assets/index.js',isEntry:true}}));
    } else assert.deepEqual(process.argv.slice(2), ['run','check:bundle']);
    console.log('fixture child completed');
  `);
  const result = runFiveBuilds({ root, npmCli, env: { ...process.env, DRAGON_AI_TOKEN: 'synthetic', NODE_OPTIONS: '--inspect' } });
  assert.equal(result.status, 'passed');
  assert.equal(result.builds.length, 5);
});

test('standalone CLI help performs no build', async () => {
  await load();
  const result = spawnSync(process.execPath, [runnerPath, '--help'], { encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /five|Five/);
});

test('controlled AI flag values remain auditable while credentials are redacted', async t => {
  const { runFiveBuilds } = await load();
  const root = fixture(t);
  const result = runFiveBuilds({ ...optionsFor(root, executor(root, [], () =>
    ({ status: 0, stdout: 'VITE_PRIVATE_AI=1; bytes=315500', stderr: '' }))),
  env: { ...process.env, VITE_PRIVATE_AI: '1' } });
  const output = readFileSync(resolve(result.runRoot, 'owner/build.stdout.log'), 'utf8');
  assert.equal(output, 'VITE_PRIVATE_AI=1; bytes=315500');
});

test('actual process environment survives success and executor failure', async t => {
  const { runFiveBuilds } = await load();
  const root = fixture(t), before = { ...process.env };
  runFiveBuilds(optionsFor(root, executor(root, [])));
  assert.deepEqual({ ...process.env }, before);
  assert.throws(() => runFiveBuilds(optionsFor(root, () => { throw new Error('synthetic spawn failure'); })), /synthetic spawn failure/);
  assert.deepEqual({ ...process.env }, before);
});

test('npm resolution uses the existing installation without invoking it', async () => {
  const { resolveNpmCli } = await load();
  const cli = resolveNpmCli();
  assert.ok(existsSync(cli));
  assert.match(cli, /npm-cli\.js$/);
});

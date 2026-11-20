import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const web = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const checker = resolve(web, 'scripts/check-default-bundle.mjs');

function checkAsset(name, source, reset, sharing, review = '0', ai = '0', entry = 'console.log("public");', manifest) {
  const scratch = resolve(web, '.temp/five-build-tests');
  mkdirSync(scratch, { recursive: true });
  const fixture = mkdtempSync(resolve(scratch, 'bundle-isolation-'));
  try {
    mkdirSync(resolve(fixture, 'dist/assets'), { recursive: true });
    writeFileSync(resolve(fixture, 'dist/index.html'), '<script type="module" src="/assets/index.js"></script>');
    writeFileSync(resolve(fixture, 'dist/assets/index.js'), entry);
    writeFileSync(resolve(fixture, 'dist/assets', name), source);
    if (manifest) {
      mkdirSync(resolve(fixture, 'dist/.vite'));
      writeFileSync(resolve(fixture, 'dist/.vite/manifest.json'), JSON.stringify(manifest));
    }
    return spawnSync(process.execPath, [checker], {
      cwd: fixture, encoding: 'utf8',
      env: { ...process.env, VITE_RESET_WORKBENCH: reset, VITE_PUBLIC_SHARING: sharing,
        VITE_PRIVATE_REVIEW: review, VITE_PRIVATE_AI: ai },
    });
  } finally {
    rmSync(fixture, { recursive: true, force: true });
  }
}

for (const [name, reset, sharing, review, ai, localAllowed, ownerAllowed, aiAllowed] of [
  ['default', '0', '0', '0', '0', false, false, false],
  ['sharing', '0', '1', '0', '0', false, false, false],
  ['reset', '1', '0', '0', '0', true, false, false],
  ['private', '0', '0', '1', '0', false, true, false],
  ['owner', '1', '0', '1', '1', true, true, true],
]) {
  for (const [marker, allowed] of [
    ['dragon-radar-local-v3', localAllowed],
    ['/api/owner/', ownerAllowed],
    ['/api/ai/', aiAllowed],
  ]) {
    test(`${name} enforces ${marker} in unlinked CSS`, () => {
      const result = checkAsset('lazy.css', `/* ${marker} */`, reset, sharing, review, ai);
      assert.equal(result.status, allowed ? 0 : 1, result.stderr);
    });
  }
}

test('reset permits browser-local review UI, not an owner session', () => {
  const result = checkAsset('local.js', '"我的复盘";"人工验证";', '1', '0');
  assert.equal(result.status, 0, result.stderr);
});

for (const syntax of [
  'import "./lazy.js";', 'export { value } from "./lazy.js";',
  'import{value}from"./lazy.js";',
]) {
  test(`query cannot hide behind static syntax ${syntax}`, () => {
    const result = checkAsset('lazy.js', 'export const value="/api/query/";', '1', '0', '0', '0', syntax);
    assert.equal(result.status, 1, result.stdout);
    assert.match(result.stderr, /static closure/);
  });
}

test('dynamic query remains outside the static closure', () => {
  const result = checkAsset('lazy.js', '"/api/query/";', '1', '0', '0', '0', 'import("./lazy.js");');
  assert.equal(result.status, 0, result.stderr);
});

test('manifest imports participate in static closure', () => {
  const result = checkAsset('lazy.js', '"/api/query/";', '1', '0', '0', '0', 'console.log("entry");', {
    'index.html': { file: 'assets/index.js', isEntry: true, imports: ['lazy'] },
    lazy: { file: 'assets/lazy.js' },
  });
  assert.equal(result.status, 1, result.stdout);
  assert.match(result.stderr, /static closure/);
});

test('manifest missing dependencies fail closed', () => {
  const result = checkAsset('lazy.js', '', '1', '0', '0', '0', '', {
    'index.html': { file: 'assets/index.js', isEntry: true, imports: ['missing'] },
  });
  assert.equal(result.status, 1);
});

for (const [sharing, budget] of [['0', 315500], ['1', 320000]]) {
  test(`entry budget ${budget} accepts exact boundary`, () => {
    const result = checkAsset('lazy.js', '', '0', sharing, '0', '0', ' '.repeat(budget));
    assert.equal(result.status, 0, result.stderr);
  });
  test(`entry budget ${budget} rejects one extra byte`, () => {
    const result = checkAsset('lazy.js', '', '0', sharing, '0', '0', ' '.repeat(budget + 1));
    assert.equal(result.status, 1);
    assert.match(result.stderr, new RegExp(`budget is ${budget}`));
  });
}

test('non-reset private rejects lazy sentiment research', () => {
  const result = checkAsset('lazy.css', '/* /api/market/sentiment/research */', '0', '0', '1', '0');
  assert.equal(result.status, 1);
});

test('root-relative static imports resolve from dist, not the importing directory', () => {
  const result = checkAsset('lazy.js', 'export const value=1;', '1', '0', '0', '0',
    'import "/assets/lazy.js";');
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(result.stdout).staticAssets, ['assets/index.js', 'assets/lazy.js']);
});

test('import-shaped strings and comments are not static dependencies', () => {
  const result = checkAsset('lazy.js', '"/api/query/";', '1', '0', '0', '0',
    `const text='import "./missing.js"'; /* export { value } from "./missing.js"; */ import("./lazy.js");`);
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(result.stdout).staticAssets, ['assets/index.js']);
});

for (const [name, source, marker] of [
  ['manifest-client.js', 'fetch("/api/research/manifests/id");', '/api/research'],
  ['simulation.js', 'const disclosure = "仅研究模拟";', '仅研究模拟'],
  ['BacktestPanel.css', '.backtest-panel { display: grid; }', '.backtest-panel'],
]) {
  test(`reset sharing rejects owner marker in an unlinked lazy asset: ${name}`, () => {
    const result = checkAsset(name, source, '1', '1');
    assert.equal(result.status, 1, result.stdout);
    assert.ok(result.stderr.includes(marker), result.stderr);
  });
  test(`default rejects owner marker in any asset: ${name}`, () => {
    const result = checkAsset(name, source, '0', '0');
    assert.equal(result.status, 1, result.stdout);
    assert.ok(result.stderr.includes(marker), result.stderr);
  });
  test(`owner reset permits the isolated lazy owner asset: ${name}`, () => {
    const result = checkAsset(name, source, '1', '0');
    assert.equal(result.status, 0, result.stderr);
  });
}

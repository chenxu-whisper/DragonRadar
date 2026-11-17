import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const web = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const checker = resolve(web, 'scripts/check-default-bundle.mjs');

function checkAsset(name, source, reset, sharing) {
  mkdirSync(resolve(web, '.temp'), { recursive: true });
  const fixture = mkdtempSync(resolve(web, '.temp/bundle-isolation-'));
  try {
    mkdirSync(resolve(fixture, 'dist/assets'), { recursive: true });
    writeFileSync(resolve(fixture, 'dist/index.html'), '<script type="module" src="/assets/index.js"></script>');
    writeFileSync(resolve(fixture, 'dist/assets/index.js'), 'console.log("public");');
    writeFileSync(resolve(fixture, 'dist/assets', name), source);
    return spawnSync(process.execPath, [checker], {
      cwd: fixture, encoding: 'utf8',
      env: { ...process.env, VITE_RESET_WORKBENCH: reset, VITE_PUBLIC_SHARING: sharing },
    });
  } finally {
    rmSync(fixture, { recursive: true, force: true });
  }
}

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

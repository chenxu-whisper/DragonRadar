import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { mkdtempSync, rmSync, existsSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { chromium } from '@playwright/test';

const extension = resolve('tests/fixtures/browser-zoom-extension');
const profile = mkdtempSync(resolve(tmpdir(), 'dragon-c2-smoke-'));
const server = createServer((_req, res) => {
  res.setHeader('Content-Type', 'text/html');
  res.end('<!doctype html><title>C2 owned zoom smoke</title><button>Owned smoke target</button>');
});
await new Promise(done => server.listen(0, '127.0.0.1', done));
const origin = `http://127.0.0.1:${server.address().port}`;
let context;
let worker;
let tabId;
let failure;
const proof = { scope: 'bounded extension capability only, not product acceptance', profile, origin };
try {
  context = await chromium.launchPersistentContext(profile, {
    channel: 'chromium', headless: false, viewport: { width: 768, height: 1024 },
    args: [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`],
  });
  worker = context.serviceWorkers()[0] ?? await context.waitForEvent('serviceworker');
  const page = context.pages()[0] ?? await context.newPage();
  await page.goto(origin);
  proof.browser = context.browser()?.version();
  proof.native = await page.evaluate(() => ({
    outerWidth, outerHeight, innerWidth, innerHeight, dpr: devicePixelRatio,
  }));
  tabId = await worker.evaluate(async url => {
    const tabs = await chrome.tabs.query({});
    const tab = tabs.find(tab => tab.url.startsWith(url));
    if (!tab?.id) throw new Error('Actual smoke app tab missing');
    await chrome.tabs.setZoom(tab.id, 2);
    const actual = await chrome.tabs.getZoom(tab.id);
    if (actual !== 2) throw new Error(`Zoom receipt ${actual}`);
    return tab.id;
  }, origin);
  await page.waitForFunction(() => innerWidth === 384);
  proof.zoom = { tabId, requested: 2, actual: await worker.evaluate(id => chrome.tabs.getZoom(id), tabId) };
  proof.changed = await page.evaluate(() => ({
    outerWidth, outerHeight, innerWidth, innerHeight, dpr: devicePixelRatio,
  }));
  assert.equal(proof.browser, '153.0.8010.12');
  assert.equal(proof.native.innerWidth, 768);
  assert.equal(proof.changed.innerWidth, 384);
  assert.equal(proof.changed.innerHeight, 512);
  assert.equal(proof.changed.outerWidth, proof.native.outerWidth);
  await page.getByRole('button', { name: 'Owned smoke target' }).click();
} catch (error) {
  failure = error;
} finally {
  proof.cleanup = {};
  try {
    if (worker && tabId) {
      proof.cleanup.restored = await worker.evaluate(async id => {
        await chrome.tabs.setZoom(id, 1);
        return chrome.tabs.getZoom(id);
      }, tabId);
      assert.equal(proof.cleanup.restored, 1);
    } else throw new Error('No actual tab to restore');
  } catch (error) { failure = new AggregateError([failure, error].filter(Boolean), 'Zoom cleanup failed'); }
  try {
    await context?.close();
    proof.cleanup.closed = Boolean(context);
    rmSync(profile, { recursive: true });
    proof.cleanup.profileRemoved = !existsSync(profile);
    assert.equal(proof.cleanup.closed, true);
    assert.equal(proof.cleanup.profileRemoved, true);
  } catch (error) { failure = new AggregateError([failure, error].filter(Boolean), 'Profile cleanup failed'); }
  await new Promise(done => server.close(done));
  proof.outcome = failure ? 'failed' : 'passed';
  proof.error = failure?.stack;
  writeFileSync('/evidence/zoom-smoke.json', JSON.stringify(proof, null, 2));
  console.log(JSON.stringify(proof));
}
if (failure) throw failure;

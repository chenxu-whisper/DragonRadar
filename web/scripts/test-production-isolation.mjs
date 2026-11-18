import { chromium } from '@playwright/test';
import { randomBytes } from 'node:crypto';
import { mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

import {
  RESEARCH_EDGE_DENY_PROBES,
  RESEARCH_OWNER_READ_PROBES,
  RESEARCH_OWNER_WRITE_PROBES,
  RESEARCH_PUBLIC_DENY_PROBES,
  composeChildEnvironment,
  productionOwnerOverride,
  queryPublishedPort,
  selectPublicEdgeSubnet,
} from './production-isolation-support.mjs';
import { composeOverride } from './reset-contract-runtime-support.mjs';

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const root = resolve(webRoot, '..');
const runId = `${process.pid}-${Date.now()}-${randomBytes(4).toString('hex')}`;
const state = resolve(root, '..', '.temp', 'production-isolation', runId);
const password = randomBytes(24).toString('hex');
const project = `dragon-radar-isolation-e2e-${runId}`;
const envFile = resolve(state, 'compose.env');
const authorityFile = resolve(state, 'authority');
const pairFile = resolve(state, 'pair.txt');
const networkOverrideFile = resolve(state, 'compose.network.yaml');
const ownerOverrideFile = resolve(state, 'compose.owner.yaml');
const files = ['compose.yaml', networkOverrideFile, ownerOverrideFile];
let browser;

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: root,
    encoding: 'utf8',
    timeout: options.timeout ?? 600_000,
    maxBuffer: 32 * 1024 * 1024,
    env: composeChildEnvironment({ ...process.env, ...options.env }),
  });
  if (result.error) throw result.error;
  if (!(options.allowed ?? [0]).includes(result.status)) {
    throw new Error([
      `${command} ${args.join(' ')} exited ${result.status}`,
      result.stdout,
      result.stderr,
    ].filter(Boolean).join('\n'));
  }
  return result.stdout;
}

function compose(...args) {
  return run('docker', [
    'compose',
    '--project-name', project,
    '--project-directory', root,
    '--env-file', envFile,
    ...files.flatMap(file => ['-f', resolve(root, file)]),
    ...args,
  ]);
}

function writeEnvironment(ownerPort) {
  writeFileSync(
    envFile,
    [
      `DRAGON_DB_PASSWORD=${password}`,
      `DRAGON_OWNER_PORT=${ownerPort}`,
      'DRAGON_PUBLIC_PORT=0',
      'DRAGON_PUBLIC_BIND=127.0.0.1',
      `DRAGON_OWNER_SECRETS_DIR=${resolve(state, 'owner-secrets').replaceAll('\\', '/')}`,
      '',
    ].join('\n'),
    { encoding: 'utf8', mode: 0o600 },
  );
}

async function withStack(action) {
  let primaryError;
  try {
    return await action();
  } catch (error) {
    primaryError = error;
    throw error;
  } finally {
    try {
      compose('down', '--volumes', '--remove-orphans', '--timeout', '10');
    } catch (error) {
      if (primaryError) throw new AggregateError([primaryError, error], `Drill and cleanup failed for ${project}`);
      throw error;
    }
  }
}

async function request(page, path, options) {
  return page.evaluate(async ({ path: requestPath, options: requestOptions }) => {
    const response = await fetch(requestPath, requestOptions);
    return { status: response.status, body: await response.text() };
  }, { path, options });
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

async function launchPage(baseURL) {
  if (!browser) {
    const channel = process.env.PLAYWRIGHT_CHANNEL || 'msedge';
    browser = await chromium.launch(channel === 'chromium' ? {} : { channel });
  }
  const context = await browser.newContext({ baseURL, ignoreHTTPSErrors: true });
  const page = await context.newPage();
  return { context, page };
}

async function testPrivate(port) {
    const { context, page } = await launchPage(`https://localhost:${port}`);
    try {
      await page.goto('/');
      await page.getByRole('heading', { name: '私人会话已锁定' }).waitFor();

      compose(
        'exec',
        '-T',
        'owner-api',
        'dragon-radar',
        'owner-pair',
        '--origin',
        `https://localhost:${port}`,
        '--output',
        '/tmp/task18-pair.txt',
      );
      compose('cp', 'owner-api:/tmp/task18-pair.txt', pairFile);
      compose('exec', '-T', 'owner-api', 'rm', '-f', '/tmp/task18-pair.txt');
      const code = readFileSync(pairFile, 'utf8').trim();
      rmSync(pairFile, { force: true });
      assert(code.length > 0, 'Pairing code was empty');

      await page.getByLabel('一次性配对码').fill(code);
      await page.getByRole('button', { name: '配对', exact: true }).click();
      await page.getByRole('region', { name: '私人会话' }).waitFor();

      const session = await request(page, '/api/owner/v1/session');
      assert(session.status === 200, `Private session route returned ${session.status}`);
      const reset = await request(page, '/api/snapshots/list');
      assert(reset.status === 200, `Private Reset route returned ${reset.status}`);
      const ai = await request(
        page,
        '/api/ai/task/00000000-0000-4000-8000-000000000099',
      );
      assert(
        ai.status === 404 && ai.body.includes('"code":"NOT_FOUND"'),
        `Private AI proxy was not reached: ${ai.status} ${ai.body}`,
      );
      for (const [path, options] of [
        ['/api/owner/v1/notes', undefined],
        ['/api/snapshots/publish', { method: 'POST' }],
        ['/api/news/00000000-0000-4000-8000-000000000001/associations/review', { method: 'POST' }],
      ]) {
        const denied = await request(page, path, options);
        assert(denied.status === 404, `Private disallowed route ${path} returned ${denied.status}`);
      }

      // Task 17: whitelisted research reads proxy through to the app. The
      // paired session clears the OwnerGate; the unknown id reaches the route,
      // whose JSON NOT_FOUND envelope proves the edge did not swallow the call.
      for (const path of RESEARCH_OWNER_READ_PROBES) {
        const probed = await request(page, path);
        assert(
          probed.status === 404 && probed.body.includes('"code":"NOT_FOUND"'),
          `Private research read ${path} did not reach the app: ${probed.status} ${probed.body}`,
        );
      }
      // Whitelisted research writes without an X-CSRF-Token header are rejected
      // by the app gate with a JSON csrf_forbidden envelope; an edge 404 would
      // have an empty body instead.
      for (const { method, path } of RESEARCH_OWNER_WRITE_PROBES) {
        const probed = await request(page, path, {
          method,
          headers: { 'Content-Type': 'application/json' },
          body: '{}',
        });
        assert(
          probed.status === 403 && probed.body.includes('"code":"csrf_forbidden"'),
          `Private research write ${path} was not CSRF-rejected by the app: ${probed.status} ${probed.body}`,
        );
      }
      // Research sibling paths match no whitelist and die at the edge default-deny.
      for (const path of RESEARCH_EDGE_DENY_PROBES) {
        const probed = await request(page, path);
        assert(
          probed.status === 404 && probed.body === '',
          `Private edge did not deny research sibling ${path}: ${probed.status} ${probed.body}`,
        );
      }
    } finally {
      await context.close();
    }
}

async function testPublic(port) {
    const { context, page } = await launchPage(`http://127.0.0.1:${port}`);
    try {
      await page.goto('/');
      const html = await page.content();
      assert(!html.includes('仅限本机所有者会话'), 'Public page exposed private AI UI');
      assert(!html.includes('私人会话已锁定'), 'Public page exposed owner session UI');
      for (const [path, options, expected] of [
        ['/api/ai/task/00000000-0000-4000-8000-000000000099', undefined, 404],
        ['/api/owner/v1/session', undefined, 404],
        ['/api/ai/daily-review', { method: 'POST' }, 403],
        ['/api/owner/v1/pair', { method: 'POST' }, 403],
      ]) {
        const denied = await request(page, path, options);
        assert(
          denied.status === expected && denied.body === '',
          `Public Caddy did not deny ${path} at the edge: ${denied.status} ${denied.body}`,
        );
      }
      // Task 17: the read-only edge denies the whole research namespace. GETs
      // hit the empty private-namespace 404; POSTs stop at the method 403 guard.
      for (const path of RESEARCH_PUBLIC_DENY_PROBES) {
        const denied = await request(page, path);
        assert(
          denied.status === 404 && denied.body === '',
          `Public Caddy did not deny research path ${path} at the edge: ${denied.status} ${denied.body}`,
        );
      }
      for (const { method, path } of RESEARCH_OWNER_WRITE_PROBES) {
        const denied = await request(page, path, { method });
        assert(
          denied.status === 403 && denied.body === '',
          `Public Caddy allowed research write ${path} past the method guard: ${denied.status} ${denied.body}`,
        );
      }
      compose(
        'exec',
        '-T',
        'public-caddy',
        'sh',
        '-ec',
        "if grep -R -E '仅限本机所有者会话|私人会话已锁定|/api/ai/|/api/research|仅研究模拟' /srv; then exit 1; fi; "
          + "if find /srv -type f -iname '*taskpanel*' | grep -q .; then exit 1; fi",
      );
    } finally {
      await context.close();
    }
}

try {
  mkdirSync(resolve(state, 'owner-secrets'), { recursive: true });
  run('docker', ['version'], { timeout: 30_000 });
  writeEnvironment(1);
  writeFileSync(networkOverrideFile, composeOverride({ publicEdgeSubnet: selectPublicEdgeSubnet(run) }), 'utf8');
  writeFileSync(ownerOverrideFile, productionOwnerOverride({ state }), 'utf8');
  await withStack(async () => {
    compose('config', '--quiet');
    compose('build');
    compose('up', '--detach', '--wait', 'db');
    compose('run', '--rm', 'public-api', 'migrate');
    // Start the gateway before its authority exists so Docker allocates the port.
    compose('up', '--detach', '--no-deps', 'owner-caddy');
    const privatePort = queryPublishedPort(compose, 'owner-caddy', 8443);
    writeEnvironment(privatePort);
    writeFileSync(authorityFile, `localhost:${privatePort}\n`, 'utf8');
    compose('run', '--rm', 'owner-api', 'owner-init');
    compose('up', '--detach', '--wait', '--no-recreate');
    const publicPort = queryPublishedPort(compose, 'public-caddy', 8080);
    await testPrivate(privatePort);
    await testPublic(publicPort);
    console.log(JSON.stringify({
      private: `https://localhost:${privatePort}`,
      public: `http://127.0.0.1:${publicPort}`,
      status: 'passed',
    }));
  });
} finally {
  if (browser) await browser.close();
  rmSync(state, { recursive: true, force: true });
}

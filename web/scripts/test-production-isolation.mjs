import { chromium } from '@playwright/test';
import { readFileSync, rmSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

import {
  RESEARCH_EDGE_DENY_PROBES,
  RESEARCH_OWNER_READ_PROBES,
  RESEARCH_OWNER_WRITE_PROBES,
  RESEARCH_PUBLIC_DENY_PROBES,
  productionOwnerOverride,
  queryPublishedPort,
  selectPublicEdgeSubnet,
} from './production-isolation-support.mjs';
import {
  composeOverride, finishProductionIsolationEvidence, fixtureBrowserLaunchOptions,
} from './reset-contract-runtime-support.mjs';
import {
  captureFixtureSource, cleanupFixtureProject, createFixtureIdentity,
  createPrivateDirectory, fixtureChildEnvironment, fixtureEvidenceSecrets, initializeFixtureEvidence,
  protectOwnerSecretFiles, protectPrivateFile, recordFixtureCommand,
  sanitizeFixtureEvidence, writePrivateFile,
  recordFixtureBrowserIdentity, recordFixtureToolIdentities,
} from './contract-synthetic-fixture.mjs';

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const root = resolve(webRoot, '..');
const identity = createFixtureIdentity({
  root, kind: 'isolation',
  evidenceRoot: resolve(root, '..', 'production-isolation'),
});
const { runId, project, state, password, envFile } = identity;
const secrets = fixtureEvidenceSecrets(identity);
const authorityFile = resolve(state, 'authority');
const pairFile = resolve(state, 'pair.txt');
const networkOverrideFile = resolve(state, 'compose.network.yaml');
const ownerOverrideFile = resolve(state, 'compose.owner.yaml');
const files = ['compose.yaml', networkOverrideFile, ownerOverrideFile];
let browser;
let evidencePrepared = false;
let primaryError;
let cleanup = { status: 'not-started' };
let probeResult;
let manifest;
const permissions = {};

function run(command, args, options = {}) {
  const started = Date.now();
  const result = spawnSync(command, args, {
    cwd: root,
    encoding: 'utf8',
    timeout: options.timeout ?? 600_000,
    maxBuffer: 32 * 1024 * 1024,
    env: fixtureChildEnvironment(process.env),
  });
  if (evidencePrepared) {
    recordFixtureCommand(identity, {
      command, args, status: result.status, durationMs: Date.now() - started,
      stdout: result.stdout, stderr: result.stderr, error: result.error?.message,
    });
  }
  if (result.error) throw new Error(sanitizeFixtureEvidence(result.error.message, secrets));
  if (!(options.allowed ?? [0]).includes(result.status)) {
    throw new Error(sanitizeFixtureEvidence([
      `${command} ${args.join(' ')} exited ${result.status}`,
      result.stdout,
      result.stderr,
    ].filter(Boolean).join('\n'), secrets));
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
  permissions.envFile = writePrivateFile(
    envFile,
    [
      `DRAGON_DB_PASSWORD=${password}`,
      `DRAGON_OWNER_PORT=${ownerPort}`,
      'DRAGON_PUBLIC_PORT=0',
      'DRAGON_PUBLIC_BIND=127.0.0.1',
      `DRAGON_OWNER_SECRETS_DIR=${resolve(state, 'owner-secrets').replaceAll('\\', '/')}`,
      'DRAGON_AI_PROVIDER=',
      'DRAGON_AI_TOKEN=',
      'DRAGON_AI_MODEL_VERSION=',
      '',
    ].join('\n'),
  );
}

async function withStack(action) {
  let primaryError;
  try {
    return await action();
  } catch (error) {
    primaryError = new Error(sanitizeFixtureEvidence(error.message, secrets));
    throw primaryError;
  } finally {
    try {
      cleanup = cleanupFixtureProject({ project, runId, kind: 'isolation', run, compose });
    } catch (error) {
      cleanup = { status: 'failed', error: sanitizeFixtureEvidence(error.message, secrets) };
    }
    if (cleanup.status !== 'removed') {
      throw new Error(sanitizeFixtureEvidence(
        [primaryError?.message, cleanup.error].filter(Boolean).join('\n'), secrets,
      ));
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
    recordFixtureBrowserIdentity(identity, { channel });
    browser = await chromium.launch(fixtureBrowserLaunchOptions(channel));
    recordFixtureBrowserIdentity(identity, { channel, version: browser.version() });
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
      protectPrivateFile(pairFile);
      compose('exec', '-T', 'owner-api', 'rm', '-f', '/tmp/task18-pair.txt');
      const code = readFileSync(pairFile, 'utf8').trim();
      secrets.push(code);
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
      ]) {
        const denied = await request(page, path, options);
        assert(denied.status === 404, `Private disallowed route ${path} returned ${denied.status}`);
      }

      // News-review POST is edge-allowed; JSON reaches the app's CSRF refusal.
      const newsReview = await request(
        page, '/api/news/00000000-0000-4000-8000-000000000001/associations/review',
        { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' },
      );
      assert(
        newsReview.status === 403,
        `Private news-review write was not CSRF-rejected: ${newsReview.status} ${newsReview.body}`,
      );
      const refusal = JSON.parse(newsReview.body);
      assert(
        refusal.code === 'FORBIDDEN' && refusal.message === 'Access denied'
          && Object.keys(refusal).length === 2,
        `Private news-review refusal was not exact: ${newsReview.body}`,
      );

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
  initializeFixtureEvidence(identity, captureFixtureSource(root, run), { privateState: true });
  evidencePrepared = true;
  permissions.ownerSecrets = createPrivateDirectory(identity.secretsDir);
  recordFixtureToolIdentities(identity, run);
  writeEnvironment(1);
  permissions.networkOverrideFile = writePrivateFile(networkOverrideFile, composeOverride({ publicEdgeSubnet: selectPublicEdgeSubnet(run), password }));
  permissions.ownerOverrideFile = writePrivateFile(ownerOverrideFile, productionOwnerOverride({ state }));
  await withStack(async () => {
    compose('config', '--quiet');
    compose('build');
    compose('up', '--detach', '--wait', 'db');
    compose('run', '--rm', 'public-api', 'migrate');
    // Start the gateway before its authority exists so Docker allocates the port.
    compose('up', '--detach', '--no-deps', 'owner-caddy');
    const privatePort = queryPublishedPort(compose, 'owner-caddy', 8443);
    writeEnvironment(privatePort);
    writePrivateFile(authorityFile, `localhost:${privatePort}\n`);
    compose('run', '--rm', 'owner-api', 'owner-init');
    permissions.ownerSecretFiles = protectOwnerSecretFiles(identity.secretsDir);
    compose('up', '--detach', '--wait', '--no-recreate');
    const publicPort = queryPublishedPort(compose, 'public-caddy', 8080);
    await testPrivate(privatePort);
    await testPublic(publicPort);
    probeResult = {
      private: `https://localhost:${privatePort}`,
      public: `http://127.0.0.1:${publicPort}`,
    };
  });
} catch (error) {
  primaryError = new Error(sanitizeFixtureEvidence(error.message, secrets));
} finally {
  if (evidencePrepared) {
    try {
      manifest = await finishProductionIsolationEvidence(identity, {
        browser, error: primaryError, cleanup, result: probeResult, permissions,
        secretFiles: [networkOverrideFile, ownerOverrideFile, authorityFile, pairFile],
        environment: { inheritedKeys: Object.keys(fixtureChildEnvironment()).sort() },
      });
    } catch (error) {
      primaryError = new Error(sanitizeFixtureEvidence(
        [primaryError?.message, error.message].filter(Boolean).join('\n'), secrets,
      ));
    }
  }
}

if (primaryError) throw new Error(sanitizeFixtureEvidence(primaryError.message, secrets));
if (manifest?.status !== 'passed') {
  throw new Error(sanitizeFixtureEvidence(manifest?.error ?? 'Fixture evidence was not initialized', secrets));
}
console.log(sanitizeFixtureEvidence({ ...manifest.result, status: manifest.status, evidence: state }, secrets));

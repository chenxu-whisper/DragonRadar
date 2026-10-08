import { chromium } from '@playwright/test';
import { randomBytes } from 'node:crypto';
import { mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:net';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const root = resolve(webRoot, '..');
const runId = `${process.pid}-${Date.now()}`;
const state = resolve(root, '.temp', 'production-isolation', runId);
const password = randomBytes(24).toString('hex');
const privateProject = `dragon-radar-private-e2e-${process.pid}`;
const publicProject = `dragon-radar-public-e2e-${process.pid}`;
const envFile = resolve(state, 'compose.env');
const pairFile = resolve(state, 'pair.txt');
const stacks = [];
let browser;

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: root,
    encoding: 'utf8',
    timeout: options.timeout ?? 600_000,
    env: { ...process.env, ...options.env },
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

function compose(project, files, ...args) {
  return run('docker', [
    'compose',
    '--project-name', project,
    '--project-directory', root,
    '--env-file', envFile,
    ...files.flatMap(file => ['-f', resolve(root, file)]),
    ...args,
  ]);
}

async function freePort() {
  return new Promise((resolvePort, reject) => {
    const server = createServer();
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const address = server.address();
      if (!address || typeof address === 'string') {
        server.close();
        reject(new Error('Could not allocate a loopback port'));
        return;
      }
      const { port } = address;
      server.close(error => error ? reject(error) : resolvePort(port));
    });
  });
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
  const files = ['compose.yaml', 'compose.private.yaml'];
  stacks.push({ project: privateProject, files });
  compose(privateProject, files, 'build', 'api', 'web');
  compose(privateProject, files, 'up', '-d', '--wait', 'db');
  compose(privateProject, files, 'run', '--rm', 'api', 'migrate');
  compose(privateProject, files, 'run', '--rm', 'api', 'owner-init');
  compose(privateProject, files, 'up', '-d', '--wait', 'web');

  const { context, page } = await launchPage(`https://localhost:${port}`);
  try {
    await page.goto('/');
    await page.getByRole('heading', { name: '私人会话已锁定' }).waitFor();

    compose(
      privateProject,
      files,
      'exec',
      '-T',
      'api',
      'dragon-radar',
      'owner-pair',
      '--origin',
      `https://localhost:${port}`,
      '--output',
      '/tmp/task18-pair.txt',
    );
    compose(privateProject, files, 'cp', 'api:/tmp/task18-pair.txt', pairFile);
    compose(privateProject, files, 'exec', '-T', 'api', 'rm', '-f', '/tmp/task18-pair.txt');
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
  } finally {
    await context.close();
  }
}

async function testPublic(port) {
  const files = ['compose.yaml', 'compose.workbench.yaml'];
  stacks.push({ project: publicProject, files });
  compose(publicProject, files, 'build', 'api', 'web');
  compose(publicProject, files, 'up', '-d', '--wait', 'db');
  compose(publicProject, files, 'run', '--rm', 'api', 'migrate');
  compose(publicProject, files, 'up', '-d', '--wait', 'web');

  const { context, page } = await launchPage(`http://127.0.0.1:${port}`);
  try {
    await page.goto('/');
    const html = await page.content();
    assert(!html.includes('仅限本机所有者会话'), 'Public page exposed private AI UI');
    assert(!html.includes('私人会话已锁定'), 'Public page exposed owner session UI');
    for (const [path, options, expected] of [
      ['/api/ai/task/00000000-0000-4000-8000-000000000099', undefined, 404],
      ['/api/owner/v1/session', undefined, 404],
      ['/api/ai/daily-review', { method: 'POST' }, 405],
      ['/api/owner/v1/pair', { method: 'POST' }, 405],
    ]) {
      const denied = await request(page, path, options);
      assert(
        denied.status === expected && denied.body === '',
        `Public Caddy did not deny ${path} at the edge: ${denied.status} ${denied.body}`,
      );
    }
    compose(
      publicProject,
      files,
      'exec',
      '-T',
      'web',
      'sh',
      '-ec',
      "if grep -R -E '仅限本机所有者会话|私人会话已锁定|/api/ai/' /srv; then exit 1; fi; "
        + "if find /srv -type f -iname '*taskpanel*' | grep -q .; then exit 1; fi",
    );
  } finally {
    await context.close();
  }
}

mkdirSync(state, { recursive: true });
const privatePort = await freePort();
const publicPort = await freePort();
writeFileSync(
  envFile,
  [
    `DRAGON_DB_PASSWORD=${password}`,
    `DRAGON_PRIVATE_PORT=${privatePort}`,
    `DRAGON_WEB_PORT=${publicPort}`,
    '',
  ].join('\n'),
  { encoding: 'utf8', mode: 0o600 },
);

try {
  run('docker', ['version'], { timeout: 30_000 });
  await testPrivate(privatePort);
  await testPublic(publicPort);
  console.log(JSON.stringify({
    private: `https://localhost:${privatePort}`,
    public: `http://127.0.0.1:${publicPort}`,
    status: 'passed',
  }));
} finally {
  if (browser) await browser.close();
  for (const { project, files } of stacks.reverse()) {
    try {
      compose(project, files, 'down', '--volumes', '--remove-orphans', '--timeout', '10');
    } catch (error) {
      console.error(`Cleanup failed for ${project}: ${error.message}`);
    }
  }
  rmSync(state, { recursive: true, force: true });
}

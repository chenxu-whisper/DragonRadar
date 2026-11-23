import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import https from 'node:https';
import { createHash } from 'node:crypto';
import { spawn } from 'node:child_process';
import { resolve } from 'node:path';
import { chromium } from '@playwright/test';
import { sanitizeFixtureEvidence, fixtureEvidenceSecrets, fixtureChildEnvironment, recordFixtureBrowserIdentity,
  recordFixtureCommand } from './contract-synthetic-fixture.mjs';
import { publishContractCheckpoint, ownerTransportRequestOptions } from './reset-contract-runtime-support.mjs';
import { protectPrivateFile } from './contract-synthetic-fixture.mjs';

export async function runC3Contract({ identity, root, compose, run, publicPort, ownerBinding, ownerCa }) {
  const retain = (name, value) => writeFileSync(resolve(identity.state, name),
    sanitizeFixtureEvidence(value, fixtureEvidenceSecrets(identity)));
  const origin = `http://127.0.0.1:${publicPort}`;
  const routes = [
    '/api/realtime/quotes?security_ids=SYNTH-C1-1',
    '/api/realtime/minutes?security_id=SYNTH-C1-1&date=2026-09-08',
    '/api/realtime/anomalies?security_id=SYNTH-C1-1&date=2026-09-08',
    '/api/market/sector-flow/intraday?sector=SYNTH-SECTOR-1&date=2026-09-08',
  ];
  const gateway = [];
  for (const path of routes) {
    const response = await fetch(origin + path);
    gateway.push({ path, status: response.status, body: await response.text() });
  }
  retain('c3-gateway.json', gateway);
  if (gateway.some(item => item.status === 404 && item.body === '')) {
    throw new Error('C3 RED: actual gateway denies existing EX04-07 routes (see c3-gateway.json)');
  }
  for (const path of ['/api/realtime/import', '/api/realtime/quotes/extra', '/api/owner/v1/session']) {
    if ((await fetch(origin + path)).status !== 404) throw new Error('C3 unknown/private route admitted');
  }
  if ((await fetch(origin + routes[0], { method: 'POST' })).status !== 403) {
    throw new Error('C3 public write admitted');
  }
  compose('exec', '-T', '--user', 'root', 'public-api', 'mkdir', '/fixture');
  for (const [path, name] of [
    ['web/scripts/contract-c1-fixture.py', 'contract_c1_fixture.py'],
    ['web/scripts/contract-c3-fixture.py', 'contract-c3-fixture.py'],
    ['tests/factories.py', 'factories.py'],
    ['tests/topic_factories.py', 'topic_factories.py'],
    ['tests/news_factories.py', 'news_factories.py'],
  ]) compose('cp', resolve(root, path), `public-api:/fixture/${name}`);
  compose('exec', '-T', '--user', 'root', 'public-api', 'chown', '-R', '10001:10001', '/fixture');
  const helper = (...args) => JSON.parse(compose('exec', '-T', '-e', 'PYTHONPATH=/fixture:/app/src',
    'public-api', 'python', '/fixture/contract-c3-fixture.py', ...args));
  const cli = (...args) => JSON.parse(compose('exec', '-T', 'public-api', 'dragon-radar', ...args));
  const c1 = (...args) => JSON.parse(compose('exec', '-T', '-e', 'PYTHONPATH=/fixture:/app/src',
    'public-api', 'python', '/fixture/contract_c1_fixture.py', ...args));
  const proof = c1('database-proof');
  if (proof.database !== 'dragon_test') throw new Error('C3 database isolation mismatch');
  retain('c3-database.json', proof);
  const ai = helper('ai');
  retain('c3-ai.json', ai);
  const inputs = c1('generate', '--output', '/fixture/inputs');
  retain('c3-archived-inputs.json', inputs);
  cli('source-register', '/fixture/inputs/grant.json');
  const market = cli('publish', cli('import', '/fixture/inputs/market-1.json', '--key', 'c3-ui-market').batch_id);
  const topic = cli('topic-publish', cli('topic-import', '/fixture/inputs/topic-1.json', '--key', 'c3-ui-topic').batch_id);
  const news = c1('news', '--inputs', '/fixture/inputs', '--index', '1');
  const publicationPath = resolve(identity.state, 'c3-publication.json');
  writeFileSync(publicationPath, JSON.stringify({
    trade_date: '2026-09-08', market_snapshot_id: market.snapshot_id,
    topic_snapshot_id: topic.snapshot_id, news_input_id: news.id,
    disabled_domains: ['regulation', 'role'],
  }));
  compose('cp', publicationPath, 'public-api:/fixture/c3-publication.json');
  const publication = cli('publication-publish', '/fixture/c3-publication.json', '--key', 'c3-ui-publication');
  const realtime = helper('realtime');
  retain('c3-realtime-inputs.json', realtime);
  const ownerRealtime = await probeC3OwnerRealtime({
    identity, root, compose, run, ownerBinding, ownerCa, realtime, retain,
  });
  const privateRead = await fetch(`${origin}/api/realtime/quotes?security_ids=${realtime.security}`
    + '&source_id=c3-private-feed&quote_batch_id=c3-private-quote');
  const privateProof = { status: privateRead.status, body: await privateRead.json() };
  retain('c3-sharing-denial.json', privateProof);
  if (privateProof.status !== 403) throw new Error('C3 non-sharing source leaked through public entry');
  const channel = resolve(identity.state, 'c3-browser');
  mkdirSync(channel);
  writeFileSync(resolve(channel, 'inputs.json'), JSON.stringify({ ...realtime, publication }));
  const args = [
    resolve(root, 'web/node_modules/@playwright/test/cli.js'), 'test',
    '--config', 'playwright.contract-c3.config.ts',
  ];
  const started = Date.now();
  const child = spawn('node', args, { cwd: resolve(root, 'web'),
    env: { ...fixtureChildEnvironment(), C3_ORIGIN: origin, C3_CHANNEL: channel,
      C3_CHROMIUM_EXECUTABLE: chromium.executablePath() } });
  const stdout = [];
  const stderr = [];
  child.stdout.on('data', chunk => stdout.push(chunk));
  child.stderr.on('data', chunk => stderr.push(chunk));
  let exited = false;
  const completed = new Promise((done, fail) => {
    child.on('error', fail);
    child.on('exit', code => { exited = true; done(code); });
  });
  const deadline = started + 180_000;
  try {
    for (const [name, operation] of [['late', 'late'], ['revoke', 'revoke']]) {
      while (!existsSync(resolve(channel, `${name}.request.json`))) {
        if (exited || Date.now() >= deadline) throw new Error(`C3 browser ended before ${name}`);
        await new Promise(done => setTimeout(done, 100));
      }
      const result = helper(operation);
      retain(`c3-${name}.json`, result);
      publishContractCheckpoint(resolve(channel, `${name}.response.json`), JSON.stringify(result));
    }
    const code = await completed;
    if (code !== 0) throw new Error(`C3 browser exited ${code}; see c3-browser.log`);
  } finally {
    if (!exited) child.kill();
    await completed;
    const output = Buffer.concat(stdout).toString();
    const errors = Buffer.concat(stderr).toString();
    retain('c3-browser.log', output + errors);
    recordFixtureCommand(identity, { command: 'node', args,
      durationMs: Date.now() - started, status: child.exitCode, stdout: output, stderr: errors });
  }
  const evidence = JSON.parse(readFileSync(resolve(channel, 'evidence.json')));
  recordFixtureBrowserIdentity(identity, { channel: 'chromium', version: evidence.chromium });
  return { scope: 'C3 only; no C4/C5 promotion', ai, realtime, ownerRealtime, database: proof, browser: evidence };
}

export async function probeC3OwnerRealtime({
  identity, root, compose, run, ownerBinding, ownerCa, realtime, retain,
}) {
  const transport = ownerTransportRequestOptions({ connectPort: ownerBinding.port, ca: ownerCa });
  const authority = 'https://localhost:8443';
  const requests = [];
  const proof = { authority, connectPort: ownerBinding.port,
    caSha256: createHash('sha256').update(ownerCa).digest('hex'), requests };
  const check = (condition, message) => { if (!condition) throw new Error(message); };
  const resourceIds = compose('ps', '-q', 'owner-caddy', 'owner-api').trim().split(/\s+/);
  proof.resources = JSON.parse(run('docker', ['inspect', ...resourceIds])).map(item => ({
    container: item.Id, image: item.Image, service: item.Config.Labels['com.docker.compose.service'],
  }));
  const deployed = compose('exec', '-T', 'owner-caddy', 'cat', '/etc/caddy/Caddyfile');
  check(deployed === readFileSync(resolve(root, 'containers/owner.Caddyfile'), 'utf8'),
    'C3 owner deployed allowlist differs from source');
  proof.caddySha256 = createHash('sha256').update(deployed).digest('hex');
  const copiedPair = resolve(identity.state, 'c3-owner-pair.txt');
  let cookie = '';
  let csrf = '';
  async function ownerRequest(path, method = 'GET', body, extra = {}) {
    const payload = body === undefined ? undefined : JSON.stringify(body);
    const headers = { ...transport.headers, ...(cookie ? { Cookie: cookie } : {}),
      ...(payload === undefined ? {} : {
        Origin: authority, 'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(payload),
      }), ...extra };
    const response = await new Promise((done, fail) => {
      const request = https.request({ ...transport, path, method, headers }, response => {
        const parts = [];
        response.on('data', part => parts.push(part));
        response.on('end', () => done({ status: response.statusCode,
          body: Buffer.concat(parts).toString(), cookies: response.headers['set-cookie'] ?? [] }));
      });
      request.on('error', fail);
      request.on('timeout', () => request.destroy(new Error('C3 owner TLS request timed out')));
      request.end(payload);
    });
    // Credentials never enter the retained request/response record.
    requests.push({ method, path, host: headers.Host, paired: Boolean(cookie),
      status: response.status, body: response.body });
    retain('c3-owner-realtime.json', proof);
    return response;
  }
  try {
    compose('exec', '-T', 'owner-api', 'dragon-radar', 'owner-pair',
      '--origin', authority, '--output', '/tmp/c3-owner-pair.txt');
    compose('cp', 'owner-api:/tmp/c3-owner-pair.txt', copiedPair);
    protectPrivateFile(copiedPair);
    const code = readFileSync(copiedPair, 'utf8').trim();
    fixtureEvidenceSecrets(identity).push(code);
    const paired = await ownerRequest('/api/owner/v1/pair', 'POST', { code },
      { 'X-Dragon-Owner-Pair': '1' });
    check(paired.status === 200, 'C3 independent owner pairing failed');
    const cookies = paired.cookies.map(value => value.split(';')[0]);
    check(cookies.length === 2, 'C3 pairing cookie identity missing');
    for (const value of cookies) fixtureEvidenceSecrets(identity).push(value.slice(value.indexOf('=') + 1));
    cookie = cookies.join('; ');
    csrf = cookies.find(value => value.startsWith('__Host-dragon-private-csrf='))?.split('=')[1];
    check(Boolean(csrf), 'C3 owner CSRF identity missing');
    const session = await ownerRequest('/api/owner/v1/session');
    check(session.status === 200, 'C3 paired session not authorized');
    proof.session = JSON.parse(session.body);
    const query = new URLSearchParams({ source_id: realtime.source, as_of: realtime.pit });
    const paths = [
      `/api/realtime/quotes?${query}&security_ids=${realtime.security}&quote_batch_id=${realtime.quote}`,
      `/api/realtime/minutes?${query}&security_id=${realtime.security}&date=${realtime.date}&batch_id=${realtime.minute}`,
      `/api/realtime/anomalies?${query}&security_id=${realtime.security}&date=${realtime.date}&limit=50`,
      `/api/market/sector-flow/intraday?${query}&sector=${realtime.sector}&date=${realtime.date}&batch_id=${realtime.flow}`,
    ];
    for (const path of paths) {
      const response = await ownerRequest(path);
      check(response.status === 200 && JSON.parse(response.body).data !== null,
        `C3 owner RT GET did not reach actual storage: ${path}`);
      for (const method of ['POST', 'HEAD']) {
        const denied = await ownerRequest(path, method, method === 'POST' ? {} : undefined);
        check(denied.status === 404 && denied.body === '',
          `C3 owner exact GET-only edge admitted ${method} ${path}`);
      }
      const adjacent = await ownerRequest(path.replace('?', '/extra?'));
      check(adjacent.status === 404 && adjacent.body === '', 'C3 owner adjacent RT path admitted');
    }
    const logout = await ownerRequest('/api/owner/v1/session', 'DELETE', {}, { 'X-CSRF-Token': csrf });
    check(logout.status === 200, 'C3 independent owner session cleanup failed');
    const revoked = await ownerRequest('/api/owner/v1/session');
    check(revoked.status === 401, 'C3 independent owner session remains live');
    proof.status = 'passed';
    return proof;
  } finally {
    rmSync(copiedPair, { force: true });
    compose('exec', '-T', 'owner-api', 'rm', '-f', '/tmp/c3-owner-pair.txt');
    retain('c3-owner-realtime.json', proof);
  }
}

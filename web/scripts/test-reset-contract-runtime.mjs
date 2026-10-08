import { randomBytes } from 'node:crypto';
import { mkdirSync, rmSync, writeFileSync } from 'node:fs';
import http from 'node:http';
import https from 'node:https';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

import {
  composeOverride,
  ownerTransportRequestOptions,
  parseLoopbackBinding,
} from './reset-contract-runtime-support.mjs';

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const root = resolve(webRoot, '..');
const runId = `${process.pid}-${Date.now()}-${randomBytes(4).toString('hex')}`;
const project = `dragon-radar-contract-${runId}`.toLowerCase();
const state = resolve(root, '.temp', 'reset-contract-runtime', runId);
const envFile = resolve(state, 'compose.env');
const overrideFile = resolve(state, 'compose.override.yaml');
const composeFiles = [
  resolve(root, 'compose.reset.yaml'),
  overrideFile,
];

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: root,
    encoding: 'utf8',
    timeout: options.timeout ?? 1_200_000,
    maxBuffer: 32 * 1024 * 1024,
    env: { ...process.env, ...options.env },
  });
  if (result.error) throw result.error;
  if (!(options.allowed ?? [0]).includes(result.status)) {
    throw new Error(
      [
        `${command} ${args.join(' ')} exited ${result.status}`,
        result.stdout,
        result.stderr,
      ]
        .filter(Boolean)
        .join('\n'),
    );
  }
  return result.stdout;
}

function compose(...args) {
  return run('docker', [
    'compose',
    '--project-name',
    project,
    '--project-directory',
    root,
    '--env-file',
    envFile,
    ...composeFiles.flatMap(file => ['-f', file]),
    ...args,
  ]);
}

function ipv4Range(cidr) {
  const match = cidr.match(
    /^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\/([0-9]{1,2})$/,
  );
  if (!match) return null;
  const octets = match.slice(1, 5).map(Number);
  const prefix = Number(match[5]);
  if (octets.some(octet => octet > 255) || prefix > 32) return null;
  const address = octets.reduce(
    (value, octet) => ((value << 8) | octet) >>> 0,
    0,
  );
  const mask = prefix === 0 ? 0 : (0xffffffff << (32 - prefix)) >>> 0;
  const start = (address & mask) >>> 0;
  const size = 2 ** (32 - prefix);
  return [start, start + size - 1];
}

function selectPublicEdgeSubnet() {
  const networkIds = run('docker', ['network', 'ls', '-q'])
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  const occupied = networkIds.length
    ? JSON.parse(run('docker', ['network', 'inspect', ...networkIds]))
        .flatMap(network => network.IPAM?.Config ?? [])
        .map(config => ipv4Range(config.Subnet ?? ''))
        .filter(Boolean)
    : [];
  const candidates = [];
  for (let second = 240; second <= 254; second += 1) {
    for (let third = 0; third <= 255; third += 1) {
      candidates.push(`10.${second}.${third}.0/28`);
    }
  }
  for (const candidate of candidates) {
    const [start, end] = ipv4Range(candidate);
    if (
      occupied.every(
        ([occupiedStart, occupiedEnd]) =>
          end < occupiedStart || start > occupiedEnd,
      )
    ) {
      return candidate;
    }
  }
  throw new Error('No free private /28 was available for the public edge');
}

function request({ port, path, method = 'GET', headers = {} }) {
  return new Promise((resolveRequest, reject) => {
    const requestHandle = http.request(
      {
        hostname: '127.0.0.1',
        port,
        path,
        method,
        headers,
        timeout: 10_000,
      },
      response => {
        const chunks = [];
        response.on('data', chunk => chunks.push(chunk));
        response.on('end', () => {
          resolveRequest({
            status: response.statusCode,
            body: Buffer.concat(chunks).toString('utf8'),
          });
        });
      },
    );
    requestHandle.on('timeout', () => {
      requestHandle.destroy(new Error(`Request timed out: ${method} ${path}`));
    });
    requestHandle.on('error', reject);
    requestHandle.end();
  });
}

function assertStatus(response, expected, label) {
  assert(
    response.status === expected,
    `${label} returned ${response.status}, expected ${expected}: ${response.body}`,
  );
}

function httpsRequest(options) {
  return new Promise((resolveRequest, reject) => {
    const requestHandle = https.request(options, response => {
      const chunks = [];
      response.on('data', chunk => chunks.push(chunk));
      response.on('end', () => {
        resolveRequest({
          status: response.statusCode,
          body: Buffer.concat(chunks).toString('utf8'),
        });
      });
    });
    requestHandle.on('timeout', () => {
      requestHandle.destroy(
        new Error(`Request timed out: ${options.method} ${options.path}`),
      );
    });
    requestHandle.on('error', reject);
    requestHandle.end();
  });
}

async function assertOwnerTransport(ownerBinding, ownerCa) {
  const response = await httpsRequest(
    ownerTransportRequestOptions({
      connectPort: ownerBinding.port,
      ca: ownerCa,
    }),
  );
  assertStatus(response, 401, 'Published owner transport session');
  let body;
  try {
    body = JSON.parse(response.body);
  } catch {
    throw new Error(
      `Published owner transport returned invalid JSON: ${response.body}`,
    );
  }
  assert(
    body.code === 'session_unauthorized',
    `Published owner transport returned an unexpected code: ${response.body}`,
  );
  return { status: response.status, code: body.code };
}

function assertPublicIsolation() {
  const python = [
    'import socket',
    'for name in ("owner-api", "owner-caddy"):',
    '    try:',
    '        socket.getaddrinfo(name, 8443)',
    '    except socket.gaierror:',
    '        continue',
    '    raise SystemExit(f"{name} unexpectedly resolved")',
  ].join('\n');
  compose('exec', '-T', 'public-api', 'python', '-c', python);
  compose(
    'exec',
    '-T',
    'public-caddy',
    'sh',
    '-ec',
    [
      'command -v nslookup >/dev/null',
      'for name in owner-api owner-caddy; do',
      '  if nslookup "$name" >/dev/null 2>&1; then exit 1; fi',
      '  if wget -q -T 2 -O /dev/null "http://$name:8443/"; then exit 1; fi',
      'done',
    ].join('\n'),
  );
}

function assertOwnerNamespace() {
  const caddyNamespace = compose(
    'exec',
    '-T',
    'owner-caddy',
    'readlink',
    '/proc/1/ns/net',
  ).trim();
  const apiNamespace = compose(
    'exec',
    '-T',
    'owner-api',
    'readlink',
    '/proc/1/ns/net',
  ).trim();
  assert(
    caddyNamespace === apiNamespace,
    `Owner network namespaces differ: ${caddyNamespace} != ${apiNamespace}`,
  );

  const lockedSession = compose(
    'exec',
    '-T',
    'owner-caddy',
    'curl',
    '--silent',
    '--show-error',
    '--cacert',
    '/data/caddy/pki/authorities/local/root.crt',
    '--header',
    'Host: localhost:8443',
    '--write-out',
    '\\n%{http_code}',
    'https://localhost:8443/api/owner/v1/session',
  ).trimEnd();
  const separator = lockedSession.lastIndexOf('\n');
  const body = lockedSession.slice(0, separator);
  const status = Number(lockedSession.slice(separator + 1));
  assert(status === 401, `Owner namespace session returned ${status}: ${body}`);
  assert(
    body.includes('"code":"session_unauthorized"'),
    `Owner namespace session was not locked: ${body}`,
  );

  const socketCheck = [
    'from pathlib import Path',
    'rows = []',
    'for path in ("/proc/net/tcp", "/proc/net/tcp6"):',
    '    rows.extend(Path(path).read_text().splitlines()[1:])',
    'listeners = [row.split()[1] for row in rows if row.split()[3] == "0A"]',
    'assert "0100007F:1F41" in listeners, listeners',
    'assert "00000000:1F41" not in listeners, listeners',
    'assert not any(item.endswith(":1F41") for item in listeners if item != "0100007F:1F41"), listeners',
  ].join('\n');
  compose('exec', '-T', 'owner-api', 'python', '-c', socketCheck);
}

function assertPublicAssets() {
  compose(
    'exec',
    '-T',
    'public-caddy',
    'sh',
    '-ec',
    [
      "if grep -R -i -E '/api/owner/|/api/ai/|indexedDB|dragon-radar-local|review-workspace|local-review|我的复盘|人工验证' /srv; then",
      '  exit 1',
      'fi',
    ].join('\n'),
  );
}

async function assertHttpContracts(publicPort) {
  const spoofed = {
    Host: 'localhost:8443',
    Forwarded: 'for=127.0.0.1;host=localhost:8443;proto=https',
    'X-Forwarded-For': '127.0.0.1',
    'X-Forwarded-Host': 'localhost:8443',
    'X-Forwarded-Proto': 'https',
    'X-Real-IP': '127.0.0.1',
    'X-Dragon-Client-IP': '127.0.0.1',
    Cookie: '__Host-dragon-private-session=spoof',
  };
  assertStatus(
    await request({
      port: publicPort,
      path: '/api/snapshots/list',
      headers: spoofed,
    }),
    200,
    'Public read',
  );
  assertStatus(
    await request({
      port: publicPort,
      path: '/api/snapshots/list',
      method: 'POST',
      headers: spoofed,
    }),
    403,
    'Public write',
  );
  for (const path of [
    '/api/owner/v1/session',
    '/api/ai/task/00000000-0000-4000-8000-000000000099',
    '/api/analysis/jobs',
  ]) {
    assertStatus(
      await request({
        port: publicPort,
        path,
        headers: spoofed,
      }),
      404,
      `Public private-path probe ${path}`,
    );
  }
}

let primaryError;
let cleanupError;
let composePrepared = false;
try {
  mkdirSync(state, { recursive: true });
  run('docker', ['version'], { timeout: 30_000 });
  const publicEdgeSubnet = selectPublicEdgeSubnet();
  writeFileSync(
    envFile,
    `DRAGON_DB_PASSWORD=${randomBytes(24).toString('hex')}\n`,
    { encoding: 'utf8', mode: 0o600 },
  );
  writeFileSync(
    overrideFile,
    composeOverride({ publicEdgeSubnet }),
    'utf8',
  );
  composePrepared = true;
  compose('config', '--quiet');
  compose('build', 'public-api', 'public-caddy', 'owner-caddy');
  compose('up', '-d', '--wait', 'db');
  compose('run', '--rm', '--no-deps', 'public-api', 'migrate');
  compose('run', '--rm', '--no-deps', 'public-api', 'owner-init');
  compose(
    'up',
    '-d',
    '--wait',
    'public-api',
    'public-caddy',
    'owner-caddy',
    'owner-api',
  );

  const publicBinding = parseLoopbackBinding(
    compose('port', 'public-caddy', '8080'),
  );
  const ownerBinding = parseLoopbackBinding(
    compose('port', 'owner-caddy', '8443'),
  );
  const ownerCa = compose(
    'exec',
    '-T',
    'owner-caddy',
    'cat',
    '/data/caddy/pki/authorities/local/root.crt',
  );

  await assertHttpContracts(publicBinding.port);
  const ownerTransport = await assertOwnerTransport(ownerBinding, ownerCa);
  assertPublicIsolation();
  assertOwnerNamespace();
  assertPublicAssets();
  console.log(
    JSON.stringify({
      ownerTransport: {
        authority: 'https://localhost:8443',
        code: ownerTransport.code,
        hostHeader: 'localhost:8443',
        publishedMapping:
          `${ownerBinding.host}:${ownerBinding.port}->owner-caddy:8443`,
        status: ownerTransport.status,
        testedFrom: 'host',
        tlsServername: 'localhost',
      },
      project,
      public: `http://${publicBinding.host}:${publicBinding.port}`,
      status: 'passed',
    }),
  );
} catch (error) {
  primaryError = error;
} finally {
  if (composePrepared) {
    try {
      compose(
        'down',
        '--volumes',
        '--remove-orphans',
        '--timeout',
        '10',
      );
    } catch (error) {
      cleanupError = error;
    }
  }
  rmSync(state, { recursive: true, force: true });
}

if (primaryError) throw primaryError;
if (cleanupError) throw cleanupError;

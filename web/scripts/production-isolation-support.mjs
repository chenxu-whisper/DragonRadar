import { isIP } from 'node:net';

// Variables owned by this drill's --env-file. Compose lets inherited shell
// variables override --env-file interpolation, so they must be stripped from
// child environments (e.g. the Task 20 TLS drill leaks *_PORT=8030 via
// GITHUB_ENV, which would collide with its long-lived gateway container).
const STACK_ENVIRONMENT_KEYS = [
  'DRAGON_DB_PASSWORD',
  'DRAGON_PRIVATE_PORT',
  'DRAGON_PRIVATE_PUBLISHED_PORT',
  'DRAGON_WEB_PORT',
  'DRAGON_E2E_STATE_DIR',
];

export function composeChildEnvironment(baseEnv = process.env) {
  const env = { ...baseEnv };
  for (const key of STACK_ENVIRONMENT_KEYS) delete env[key];
  return env;
}

export function parsePublishedPort(output) {
  for (const line of output.trim().split(/\r?\n/)) {
    const match = line.match(/^(.*):([0-9]+)$/);
    const host = match?.[1].replace(/^\[|\]$/g, '');
    const port = match ? Number(match[2]) : 0;
    if (isIP(host) && port >= 1 && port <= 65535) return port;
  }
  throw new Error(`Could not parse a published port from: ${JSON.stringify(output)}`);
}

export function queryPublishedPort(compose, service, containerPort) {
  return parsePublishedPort(compose('port', service, String(containerPort)));
}

// Task 17 research edge contract. The patterns mirror the researchRead /
// researchWrite matchers in deploy/owner.Caddyfile and deploy/private.Caddyfile;
// the node:test suite pins both the patterns and the concrete probes so an
// edge rewrite cannot silently widen the research namespace.
const RESEARCH_UUID = '00000000-0000-4000-8000-0000000000aa';

export const RESEARCH_OWNER_READ_PATTERN =
  '^/api/research/(manifests/[0-9a-fA-F-]{36}|backtests/[0-9a-fA-F-]{36})$';
export const RESEARCH_OWNER_WRITE_PATTERN =
  '^/api/research/backtests(/[0-9a-fA-F-]{36}/cancel)?$';

// Whitelisted owner reads: an unknown id still reaches the app, which answers
// with its JSON NOT_FOUND envelope (proving the edge did not swallow the call).
export const RESEARCH_OWNER_READ_PROBES = [
  `/api/research/manifests/${RESEARCH_UUID}`,
  `/api/research/backtests/${RESEARCH_UUID}`,
];

// Whitelisted owner writes: without a CSRF header the app rejects them with a
// JSON envelope, which still proves the edge proxied the request.
export const RESEARCH_OWNER_WRITE_PROBES = [
  { method: 'POST', path: '/api/research/backtests' },
  { method: 'POST', path: `/api/research/backtests/${RESEARCH_UUID}/cancel` },
];

// Research siblings that no edge matcher may whitelist: the owner edge must
// answer with its empty default-deny 404.
export const RESEARCH_EDGE_DENY_PROBES = [
  '/api/research/manifests',
  '/api/research/backtests',
  '/api/research/runs/x',
  `/api/research/backtests/${RESEARCH_UUID}/result`,
];

// The workbench/public edge denies the whole namespace regardless of method.
export const RESEARCH_PUBLIC_DENY_PROBES = [
  `/api/research/manifests/${RESEARCH_UUID}`,
  `/api/research/backtests/${RESEARCH_UUID}`,
  '/api/research/backtests',
  `/api/research/backtests/${RESEARCH_UUID}/cancel`,
];

export function isResearchPath(path) {
  return /^\/api\/research\//.test(path);
}

export function isResearchOwnerRead(path) {
  return new RegExp(RESEARCH_OWNER_READ_PATTERN).test(path);
}

export function isResearchOwnerWrite(method, path) {
  return method === 'POST' && new RegExp(RESEARCH_OWNER_WRITE_PATTERN).test(path);
}

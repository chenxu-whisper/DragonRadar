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
const STACK_ENVIRONMENT_PREFIXES = [
  'COMPOSE_', 'DRAGON_AI_', 'DRAGON_OWNER_', 'DRAGON_PUBLIC_', 'DRAGON_PRIVATE_',
];

export function composeChildEnvironment(baseEnv = process.env) {
  const env = { ...baseEnv };
  for (const key of STACK_ENVIRONMENT_KEYS) delete env[key];
  for (const key of Object.keys(env)) {
    if (STACK_ENVIRONMENT_PREFIXES.some(prefix => key.startsWith(prefix))) delete env[key];
  }
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

export function productionOwnerOverride({ state }) {
  const directory = state.replaceAll('\\', '/');
  return `services:
  owner-caddy:
    entrypoint: ["/bin/sh", "-ec"]
    command:
      - |
        while [ ! -s /run/dragon-e2e/authority ]; do sleep 0.1; done
        export DRAGON_OWNER_AUTHORITY="$$(cat /run/dragon-e2e/authority)"
        exec caddy run --config /etc/caddy/Caddyfile --adapter caddyfile
    healthcheck:
      test: ["CMD-SHELL", "curl --fail --silent --show-error --max-time 3 --cacert /data/caddy/pki/authorities/local/root.crt --header \\"Host: $$(cat /run/dragon-e2e/authority)\\" https://localhost:8443/health/ready"]
    volumes:
      - type: bind
        source: ${JSON.stringify(directory)}
        target: /run/dragon-e2e
        read_only: true

  owner-api:
    command:
      - serve-owner
      - --host
      - 127.0.0.1
      - --port
      - "8001"
      - --origin
      - "https://localhost:\${DRAGON_OWNER_PORT:?Set the discovered owner port}"
    environment:
      DRAGON_OWNER_AUTHORITY: localhost:\${DRAGON_OWNER_PORT:?Set the discovered owner port}
    volumes: !override
      - type: bind
        source: ${JSON.stringify(`${directory}/owner-secrets`)}
        target: /run/dragon-owner
`;
}

function ipv4Range(cidr) {
  const match = cidr.match(
    /^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\/([0-9]{1,2})$/,
  );
  if (!match) return null;
  const octets = match.slice(1, 5).map(Number);
  const prefix = Number(match[5]);
  if (octets.some(octet => octet > 255) || prefix > 32) return null;
  const address = octets.reduce((value, octet) => ((value << 8) | octet) >>> 0, 0);
  const mask = prefix === 0 ? 0 : (0xffffffff << (32 - prefix)) >>> 0;
  const start = (address & mask) >>> 0;
  return [start, start + 2 ** (32 - prefix) - 1];
}

export function selectPublicEdgeSubnet(run) {
  const ids = run('docker', ['network', 'ls', '-q']).trim().split(/\s+/).filter(Boolean);
  const occupied = ids.length
    ? JSON.parse(run('docker', ['network', 'inspect', ...ids]))
      .flatMap(network => network.IPAM?.Config ?? [])
      .map(config => ipv4Range(config.Subnet ?? ''))
      .filter(Boolean)
    : [];
  for (let second = 240; second <= 254; second += 1) {
    for (let third = 0; third <= 255; third += 1) {
      const candidate = `10.${second}.${third}.0/28`;
      const [start, end] = ipv4Range(candidate);
      if (occupied.every(([low, high]) => end < low || start > high)) return candidate;
    }
  }
  throw new Error('No free private /28 was available for the public edge');
}

// Task 17 research edge contract. The patterns mirror the researchRead /
// researchWrite matchers in containers/owner.Caddyfile;
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

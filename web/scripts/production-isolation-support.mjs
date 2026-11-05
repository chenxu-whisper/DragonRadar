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

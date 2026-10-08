import { isIP } from 'node:net';

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

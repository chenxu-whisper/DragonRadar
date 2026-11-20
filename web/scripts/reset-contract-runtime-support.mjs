import { existsSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import {
  assertFixtureDatabase, finishFixtureEvidence, fixtureChildEnvironment, sanitizeFixtureEvidence,
} from './contract-synthetic-fixture.mjs';

export function publishContractCheckpoint(path, serialized) {
  JSON.parse(serialized);
  if (existsSync(path)) throw new Error('Contract checkpoint already published');
  const pending = `${path}.pending`;
  writeFileSync(pending, serialized, { flag: 'wx' });
  try {
    renameSync(pending, path);
  } catch (error) {
    rmSync(pending, { force: true });
    throw error;
  }
}

export function isContractLhbDenial(error, publications) {
  const url = new URL(error.url);
  return error.transition === 'archive-reads'
    && url.pathname === '/api/lhb' && error.method === 'GET'
    && error.status === 404 && error.body === ''
    && ['date', 'security_id', 'page', 'size', 'as_of'].every(key =>
      url.searchParams.getAll(key).length === 1)
    && publications.some(publication =>
      url.searchParams.get('security_id') === publication.archive.security_id
      && url.searchParams.get('date') === '2026-09-08'
      && Date.parse(url.searchParams.get('as_of') ?? '') === Date.parse(publication.receipt.published_at)
      && url.searchParams.get('page') === '1' && url.searchParams.get('size') === '20'
      && [...url.searchParams.keys()].every(key => ['date', 'security_id', 'page', 'size', 'as_of'].includes(key)));
}

export function fixtureBrowserLaunchOptions(channel, environment = process.env) {
  return {
    env: fixtureChildEnvironment(environment),
    ...(channel === 'chromium' ? {} : { channel }),
  };
}

export function contractBrowserSetup(mode) {
  if (!['owner', 'public'].includes(mode)) throw new Error('Invalid contract browser mode');
  return [
    'export DEBIAN_FRONTEND=noninteractive',
    // Preserve Ubuntu suites/components/signature checks; only use reachable HTTPS.
    "sed -i -e 's|http://azure.archive.ubuntu.com|https://archive.ubuntu.com|g' -e 's|http://security.ubuntu.com|https://security.ubuntu.com|g' /etc/apt/sources.list.d/ubuntu.sources",
    'apt-get -o APT::Update::Error-Mode=any -o Acquire::Retries=0 -o Acquire::https::Timeout=20 update',
    'apt-get -o Acquire::Retries=0 -o Acquire::https::Timeout=20 install -y --no-install-recommends ca-certificates libnss3-tools xvfb xauth',
    ...(mode === 'owner' ? [
      'cp /fixture/root.crt /usr/local/share/ca-certificates/dragon-fixture.crt',
      'update-ca-certificates',
      // Pretrust Chromium may create NSS state; replace only disposable user databases.
      'for d in /home/pwuser/.pki/nssdb /home/pwuser/.local/share/pki/nssdb; do',
      '  rm -rf -- "$d"',
      '  mkdir -p "$d"; chown -R pwuser:pwuser "$d"',
      '  timeout --kill-after=2s 15s runuser -u pwuser -- certutil -N --empty-password -d "sql:$d" -f /dev/null < /dev/null',
      '  timeout --kill-after=2s 15s runuser -u pwuser -- certutil -A -d "sql:$d" -n dragon-fixture -t C,, -i /fixture/root.crt -f /dev/null < /dev/null',
      '  timeout --kill-after=2s 15s runuser -u pwuser -- certutil -L -d "sql:$d" -n dragon-fixture -f /dev/null < /dev/null',
      'done',
    ] : []),
  ].join('\n');
}

export function mergeContractFailures(primary, cleanup) {
  if (!cleanup) return primary;
  if (!primary) return cleanup;
  return new AggregateError([primary, cleanup], `${primary.message}\nCleanup: ${cleanup.message}`);
}

export function sanitizeContractBrowserDiagnostic(value, secrets = []) {
  return sanitizeFixtureEvidence(value, secrets)
    .replace(/(^|\n)(\s*-\s*)?(cookie|set-cookie|x-csrf-token):[^\r\n]*/gi, '$1$2$3: [REDACTED]');
}

export async function finishProductionIsolationEvidence(identity, { browser, error, ...outcome }) {
  let failure = error;
  try {
    if (browser) await browser.close();
  } catch (closeError) {
    failure = new Error([error?.message, closeError.message].filter(Boolean).join('\n'));
  } finally {
    // Browser shutdown must never bypass removal of host-side secrets.
    return finishFixtureEvidence(identity, { ...outcome, error: failure });
  }
}

export function composeOverride({
  publicEdgeSubnet,
  password,
}) {
  const databaseUrl = `postgresql+psycopg://dragon_radar:${password}@db:5432/dragon_test`;
  assertFixtureDatabase(databaseUrl, password);
  const subnetMatch = publicEdgeSubnet?.match(
    /^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.0\/28$/,
  );
  if (
    !subnetMatch
    || subnetMatch.slice(1).some(octet => Number(octet) > 255)
  ) {
    throw new Error(`Invalid public edge subnet: ${publicEdgeSubnet}`);
  }
  const prefix = subnetMatch.slice(1).join('.');
  return `services:
  db:
    environment:
      POSTGRES_DB: dragon_test
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U dragon_radar -d dragon_test"]

  public-api:
    environment:
      DRAGON_DATABASE_URL: ${databaseUrl}
      DRAGON_TRUSTED_PROXY_CIDR: ${prefix}.2/32
    networks:
      public-edge:
        ipv4_address: ${prefix}.3

  public-caddy:
    ports: !override
      - target: 8080
        published: "0"
        host_ip: 127.0.0.1
        protocol: tcp
    networks:
      public-edge:
        ipv4_address: ${prefix}.2

  owner-caddy:
    environment:
      DRAGON_OWNER_AUTHORITY: localhost:8443
    ports: !override
      - target: 8443
        published: "0"
        host_ip: 127.0.0.1
        protocol: tcp

  owner-api:
    command:
      - serve-owner
      - --host
      - 127.0.0.1
      - --port
      - "8001"
      - --origin
      - "https://localhost:8443"
    environment:
      DRAGON_OWNER_AUTHORITY: localhost:8443
      DRAGON_DATABASE_URL: ${databaseUrl}

  analysis-worker:
    environment:
      DRAGON_DATABASE_URL: ${databaseUrl}

  ai-worker:
    environment:
      DRAGON_DATABASE_URL: ${databaseUrl}
      DRAGON_AI_PROVIDER: ""
      DRAGON_AI_TOKEN: ""
      DRAGON_AI_MODEL_VERSION: ""

networks:
  public-edge:
    ipam:
      config: !override
        - subnet: ${publicEdgeSubnet}
`;
}

export function parseLoopbackBinding(output) {
  const match = output.trim().match(/^127\.0\.0\.1:([0-9]+)$/);
  const port = match ? Number(match[1]) : 0;
  if (port < 1 || port > 65535) {
    throw new Error(
      `Could not parse a loopback published port from: ${JSON.stringify(output)}`,
    );
  }
  return { host: '127.0.0.1', port };
}

export function ownerTransportRequestOptions({ connectPort, ca }) {
  return {
    hostname: '127.0.0.1',
    port: connectPort,
    path: '/api/owner/v1/session',
    method: 'GET',
    servername: 'localhost',
    ca,
    headers: {
      Host: 'localhost:8443',
    },
    timeout: 10_000,
  };
}

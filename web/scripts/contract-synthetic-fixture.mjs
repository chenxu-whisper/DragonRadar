import { createHash, randomBytes } from 'node:crypto';
import {
  appendFileSync, chmodSync, existsSync, lstatSync, mkdirSync, readFileSync,
  readdirSync, rmSync, statSync, writeFileSync,
} from 'node:fs';
import { basename, dirname, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';

const OS_KEYS = new Set([
  'PATH', 'PATHEXT', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'TEMP', 'TMP', 'TMPDIR',
  'HOME', 'USERPROFILE', 'HOMEDRIVE', 'HOMEPATH', 'APPDATA', 'LOCALAPPDATA',
  'PROGRAMDATA', 'PROGRAMFILES', 'PROGRAMFILES(X86)', 'LANG', 'LC_ALL', 'TZ',
]);
const PROJECT_LABEL = 'com.docker.compose.project';
const LOCKFILES = ['uv.lock', 'web/package-lock.json'];
const TRANSPORT_BROWSER_REASON = 'C0 transport-only runtime uses HTTP/HTTPS probes; no browser is launched until C1.';
const evidenceSecrets = new WeakMap();

export function fixtureEvidenceSecrets(identity) {
  if (!evidenceSecrets.has(identity)) evidenceSecrets.set(identity, [identity.password]);
  return evidenceSecrets.get(identity);
}

const UUID_PATTERN = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i;
const SHA_PATTERN = /^[a-f0-9]{64}$/;
function timestamp(value) {
  return typeof value === 'string' && /T.*(?:Z|[+-][0-9]{2}:[0-9]{2})$/.test(value)
    && Number.isFinite(Date.parse(value));
}

export function assertContractPublications(input) {
  if (input?.synthetic !== true || !Array.isArray(input.publications) || input.publications.length < 2) {
    throw new Error('C1 publication requires at least two explicitly synthetic bound archives');
  }
  const ids = new Set();
  const sectors = new Set();
  const securities = new Set();
  const articles = new Set();
  const versions = new Set();
  for (const { receipt, archive } of input.publications) {
    if (!UUID_PATTERN.test(receipt?.snapshot_id ?? '') || ids.has(receipt.snapshot_id)
      || !Number.isSafeInteger(receipt.version) || receipt.version < 1 || versions.has(receipt.version)
      || !timestamp(receipt.published_at)) {
      throw new Error('C1 publication receipt identity/version/time is missing or duplicate');
    }
    if (!archive || archive.snapshot_id !== receipt.snapshot_id || archive.version !== receipt.version
      || archive.published_at !== receipt.published_at) {
      throw new Error('C1 publication archive does not match actual command receipt');
    }
    if (archive.trade_date !== '2026-09-08'
      || !/^SYNTH-SECTOR-[12]$/.test(archive.sector_id ?? '')
      || !/^SYNTH-C1-[12]$/.test(archive.security_id ?? '')
      || !Array.isArray(archive.members) || !archive.members.includes(archive.security_id)
      || !UUID_PATTERN.test(archive.article_id ?? '') || !archive.source_version
      || !timestamp(archive.available_at) || !SHA_PATTERN.test(archive.raw_sha256 ?? '')
      || sectors.has(archive.sector_id) || securities.has(archive.security_id)
      || articles.has(archive.article_id)) {
      throw new Error('C1 archive sector/member/security/article/source evidence missing or unrelated');
    }
    ids.add(receipt.snapshot_id);
    versions.add(receipt.version);
    sectors.add(archive.sector_id);
    securities.add(archive.security_id);
    articles.add(archive.article_id);
  }
  return input;
}

export function assertContractCitation(publication, proof) {
  const entry = proof?.citation;
  const manifest = proof?.manifest;
  const article = manifest?.input?.article;
  if (proof?.status !== 'ready' || !UUID_PATTERN.test(proof.task_id ?? '')
    || !Number.isSafeInteger(proof.draft_revision) || proof.draft_revision < 1
    || proof.snapshot_id !== publication.receipt.snapshot_id
    || manifest?.publication?.snapshot_id !== proof.snapshot_id
    || proof.article_id !== publication.archive.article_id || article?.article_id !== proof.article_id
    || !entry?.evidence_id || entry.evidence_position !== '/input/article/content'
    || typeof article?.content !== 'string' || !article.content || entry.value !== article.content
    || !manifest?.evidence?.some(item => item.evidence_id === entry.evidence_id
      && item.evidence_position === entry.evidence_position && item.value === entry.value)
    || proof.resolved?.status !== 'ready' || proof.resolved.task_id !== proof.task_id
    || proof.resolved.snapshot_id !== proof.snapshot_id || proof.resolved.revision !== proof.draft_revision
    || proof.resolved.evidence_id !== entry.evidence_id
    || proof.resolved.evidence_position !== entry.evidence_position
    || proof.resolved.locator?.kind !== 'news_article_field'
    || proof.resolved.locator.article_id !== proof.article_id
    || !UUID_PATTERN.test(proof.resolved.locator.article_revision_id ?? '')
    || proof.resolved.locator.article_revision_id !== article.revision_id
    || !Number.isSafeInteger(proof.resolved.locator.article_revision) || proof.resolved.locator.article_revision < 1
    || proof.resolved.locator.article_revision !== article.revision
    || proof.resolved.locator.field !== 'content'
    || proof.resolved.value !== entry.value) {
    throw new Error('C1 citation is not a ready draft bound to exact archived article evidence');
  }
  return proof;
}

export function fixtureChildEnvironment(base = process.env) {
  return Object.fromEntries(Object.entries(base).filter(([key]) => OS_KEYS.has(key.toUpperCase())));
}

function assertIdentity({ project, runId, kind = 'contract' }) {
  const prefix = kind === 'contract' ? 'dragon-radar-contract'
    : kind === 'isolation' ? 'dragon-radar-isolation-e2e' : undefined;
  if (!/^[0-9]+-[0-9]+-[a-f0-9]{8}$/.test(runId ?? '')
    || !prefix || project !== `${prefix}-${runId}`) {
    throw new Error('Refusing unsafe fixture project identity');
  }
}

export function createFixtureIdentity({
  root, runId = `${process.pid}-${Date.now()}-${randomBytes(4).toString('hex')}`,
  kind = 'contract',
  evidenceRoot = resolve(root, '..', ...(basename(dirname(root)) === '.temp' ? [] : ['.temp']), 'reset-contract-runtime'),
}) {
  const project = `${kind === 'isolation' ? 'dragon-radar-isolation-e2e' : 'dragon-radar-contract'}-${runId}`;
  assertIdentity({ project, runId, kind });
  const password = randomBytes(24).toString('hex');
  const state = resolve(evidenceRoot, runId);
  return {
    runId, project, kind, state, password,
    databaseUrl: `postgresql+psycopg://dragon_radar:${password}@db:5432/dragon_test`,
    envFile: resolve(state, 'compose.env'),
    overrideFile: resolve(state, 'compose.override.yaml'),
    secretsDir: resolve(state, 'owner-secrets'),
  };
}

export function assertFixtureDatabase(databaseUrl, password) {
  if (!/^[a-f0-9]{48}$/.test(password ?? '')
    || databaseUrl !== `postgresql+psycopg://dragon_radar:${password}@db:5432/dragon_test`) {
    throw new Error('Refusing unsafe fixture database configuration');
  }
}

export function fixtureEnvironmentFile(identity) {
  assertIdentity(identity);
  assertFixtureDatabase(identity.databaseUrl, identity.password);
  const directory = identity.secretsDir.replaceAll('\\', '/');
  if (/[\r\n]/.test(directory)) throw new Error('Invalid fixture secrets directory');
  return [
    `DRAGON_DB_PASSWORD=${identity.password}`,
    `DRAGON_OWNER_SECRETS_DIR=${directory}`,
    'DRAGON_AI_PROVIDER=',
    'DRAGON_AI_TOKEN=',
    'DRAGON_AI_MODEL_VERSION=',
    '',
  ].join('\n');
}

export function windowsPrivateAclCommand(path, directory) {
  const literal = path.replaceAll("'", "''");
  // A fresh protected DACL has exactly one allow rule, owned by the current SID.
  return [
    "$ErrorActionPreference = 'Stop'",
    `$path = '${literal}'`,
    '$sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User',
    '$existing = Get-Acl -LiteralPath $path',
    '$existingRules = @($existing.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]))',
    "$valid = $existing.AreAccessRulesProtected -and $existing.GetOwner([System.Security.Principal.SecurityIdentifier]).Value -eq $sid.Value -and $existingRules.Count -eq 1 -and -not $existingRules[0].IsInherited -and $existingRules[0].IdentityReference.Value -eq $sid.Value -and $existingRules[0].AccessControlType -eq 'Allow' -and $existingRules[0].FileSystemRights -eq 'FullControl'",
    'if (-not $valid) {',
    `$acl = New-Object System.Security.AccessControl.${directory ? 'DirectorySecurity' : 'FileSecurity'}`,
    '$acl.SetOwner($sid)',
    '$acl.SetAccessRuleProtection($true, $false)',
    directory
      ? "$rule = New-Object System.Security.AccessControl.FileSystemAccessRule($sid, 'FullControl', 'ContainerInherit, ObjectInherit', 'None', 'Allow')"
      : "$rule = New-Object System.Security.AccessControl.FileSystemAccessRule($sid, 'FullControl', 'Allow')",
    '$acl.AddAccessRule($rule)',
    'Set-Acl -LiteralPath $path -AclObject $acl',
    '}',
    '$actual = Get-Acl -LiteralPath $path',
    '$owner = $actual.GetOwner([System.Security.Principal.SecurityIdentifier]).Value',
    '$rules = @($actual.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]))',
    "if ($owner -ne $sid.Value -or -not $actual.AreAccessRulesProtected -or $rules.Count -ne 1) { throw 'Private ACL identity/protection assertion failed' }",
    "$r = $rules[0]",
    "if ($r.IsInherited -or $r.IdentityReference.Value -ne $sid.Value -or $r.AccessControlType -ne 'Allow' -or $r.FileSystemRights -ne 'FullControl') { throw 'Private ACL access assertion failed' }",
    '@{ ownerSid = $owner; protected = $true } | ConvertTo-Json -Compress',
  ].join('\n');
}

function protectPrivatePath(path, directory, {
  platform = process.platform, exec = spawnSync, chmod = chmodSync, stat = statSync,
} = {}) {
  if (platform === 'win32') {
    const result = exec('powershell.exe', [
      '-NoProfile', '-NonInteractive', '-Command', windowsPrivateAclCommand(path, directory),
    ], { encoding: 'utf8', timeout: 30_000, env: fixtureChildEnvironment() });
    if (result.error || result.status !== 0) throw new Error('Private Windows ACL command failed');
    let proof;
    try { proof = JSON.parse(result.stdout); } catch { throw new Error('Private Windows ACL proof missing'); }
    if (!/^S-1-[0-9-]+$/.test(proof.ownerSid ?? '') || proof.protected !== true) {
      throw new Error('Private Windows ACL identity proof invalid');
    }
    return { platform, ...proof };
  }
  const mode = directory ? 0o700 : 0o600;
  chmod(path, mode);
  if ((stat(path).mode & 0o777) !== mode) throw new Error('Private POSIX chmod proof failed');
  return { platform, mode: directory ? '0700' : '0600' };
}

export function createPrivateDirectory(path, options) {
  mkdirSync(path);
  try {
    return protectPrivatePath(path, true, options);
  } catch (error) {
    rmSync(path, { recursive: true, force: true });
    throw error;
  }
}

export function writePrivateFile(path, content, options) {
  if (!existsSync(path)) writeFileSync(path, '', { flag: 'wx' });
  try {
    const proof = protectPrivateFile(path, options);
    writeFileSync(path, content, 'utf8');
    return proof;
  } catch (error) {
    rmSync(path, { force: true });
    throw error;
  }
}

export function protectPrivateFile(path, options) {
  if (!lstatSync(path).isFile() || lstatSync(path).isSymbolicLink()) {
    throw new Error('Refusing unsafe private file identity');
  }
  return protectPrivatePath(path, false, options);
}

export function protectOwnerSecretFiles(directory, options) {
  const proofs = {};
  function visit(path, prefix = '') {
    const entry = lstatSync(path);
    if (entry.isSymbolicLink() || !entry.isDirectory()) {
      throw new Error('Refusing unsafe owner secrets directory identity');
    }
    protectPrivatePath(path, true, options);
    for (const name of readdirSync(path).sort()) {
      const child = resolve(path, name);
      const relative = prefix ? `${prefix}/${name}` : name;
      if (lstatSync(child).isDirectory() && !lstatSync(child).isSymbolicLink()) visit(child, relative);
      else proofs[relative] = protectPrivateFile(child, options);
    }
  }
  visit(directory);
  return proofs;
}

function resources(run, project) {
  const filter = `label=${PROJECT_LABEL}=${project}`;
  return Object.fromEntries([
    ['containers', ['ps', '-aq', '--filter', filter]],
    ['volumes', ['volume', 'ls', '-q', '--filter', filter]],
    ['networks', ['network', 'ls', '-q', '--filter', filter]],
  ].map(([kind, args]) => [kind, run('docker', args).trim().split(/\s+/).filter(Boolean)]));
}

export function cleanupFixtureProject({ project, runId, kind = 'contract', run, compose }) {
  assertIdentity({ project, runId, kind });
  const before = resources(run, project);
  for (const [kind, ids] of Object.entries(before)) {
    if (!ids.length) continue;
    const args = kind === 'containers' ? ['inspect'] : [kind === 'volumes' ? 'volume' : 'network', 'inspect'];
    const inspected = JSON.parse(run('docker', [...args, ...ids]));
    if (inspected.length !== ids.length || inspected.some(resource =>
      (resource.Config?.Labels ?? resource.Labels)?.[PROJECT_LABEL] !== project)) {
      throw new Error('Refusing cleanup: resource project identity mismatch');
    }
  }
  let error;
  try {
    compose('down', '--volumes', '--remove-orphans', '--timeout', '10');
  } catch (failure) {
    error = failure.message;
  }
  let after;
  try {
    after = resources(run, project);
    if (Object.values(after).some(ids => ids.length)) error ??= 'Own fixture resources remain after cleanup';
  } catch (failure) {
    error ??= failure.message;
  }
  return { status: error ? 'failed' : 'removed', before, after, ...(error ? { error } : {}) };
}

export function sanitizeFixtureEvidence(value, secrets = []) {
  const secretKey = /(?:^|_)(?:TOKEN|PASSWORD|API_KEY|SECRET|PAIRING_CODE)$/i;
  function sanitize(item) {
    if (Array.isArray(item)) return item.map(sanitize);
    if (item && typeof item === 'object') {
      return Object.fromEntries(Object.entries(item).map(([key, child]) =>
        [key, secretKey.test(key) ? '[REDACTED]' : sanitize(child)]));
    }
    if (typeof item !== 'string') return item;
    // Parse complete JSON before falling back to embedded JSON/log assignments.
    try {
      const parsed = JSON.parse(item);
      if (parsed && typeof parsed === 'object') return JSON.stringify(sanitize(parsed));
    } catch { /* Command output is usually not a complete JSON document. */ }
    let text = item;
    for (const secret of secrets.filter(Boolean)) text = text.replaceAll(secret, '[REDACTED]');
    return text
      .replace(/("(?:[A-Z0-9_]*_)?(?:TOKEN|PASSWORD|API_KEY|SECRET|PAIRING_CODE)"\s*:\s*)"(?:\\.|[^"\\])*"/gi, '$1"[REDACTED]"')
      .replace(/(postgres(?:ql)?(?:\+psycopg)?:\/\/[^:\s"@]+:)[^@\s"]+@/gi, '$1[REDACTED]@')
      .replace(/(\b(?:[A-Z0-9_]*_)?(?:TOKEN|PASSWORD|API_KEY|SECRET|PAIRING_CODE)\b\s*[=:]\s*["']?)[^\s"',}]+/gi, '$1[REDACTED]')
      .replace(/(Bearer\s+)[^\s"']+/gi, '$1[REDACTED]');
  }
  const sanitized = sanitize(value);
  return typeof value === 'string' ? sanitized : JSON.stringify(sanitized);
}

export function captureFixtureSource(root, run, requiredPaths = []) {
  const head = run('git', ['rev-parse', 'HEAD']).trim();
  const names = new Set([
    ...requiredPaths,
    ...run('git', ['ls-files', '-z']).split('\0'),
    ...run('git', ['diff', 'HEAD', '--name-only', '-z']).split('\0'),
    ...run('git', ['ls-files', '--others', '--exclude-standard', '-z']).split('\0'),
  ].filter(Boolean));
  const files = [...names].sort().map(path => {
    const absolute = resolve(root, path);
    if (!existsSync(absolute)) return { path, deleted: true };
    if (lstatSync(absolute).isSymbolicLink()) throw new Error('Linked source input is not evidence');
    const bytes = readFileSync(absolute);
    return { path, bytes: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex') };
  });
  return {
    head, files,
    dirtyRawDigest: createHash('sha256').update(JSON.stringify(files)).digest('hex'),
    lockfiles: LOCKFILES.map(path => {
      const absolute = resolve(root, path);
      if (!existsSync(absolute)) return { path, missing: true };
      const bytes = readFileSync(absolute);
      return { path, bytes: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex') };
    }),
  };
}

function actualIdentity(value) {
  return typeof value === 'string' && value.trim().length > 0
    && !/\bpending\b|^(?:unknown|not-applicable)$/i.test(value.trim());
}

function updateFixtureTools(identity, tools) {
  assertIdentity(identity);
  const file = resolve(identity.state, 'manifest.json');
  const manifest = JSON.parse(readFileSync(file, 'utf8'));
  manifest.tools = { ...manifest.tools, ...tools };
  writeFileSync(file, sanitizeFixtureEvidence(manifest, fixtureEvidenceSecrets(identity)));
}

export function recordFixtureToolIdentities(identity, run) {
  let docker;
  const output = run('docker', ['version', '--format', '{{json .}}'], { timeout: 30_000 });
  try { docker = JSON.parse(output); } catch { throw new Error('Docker identity output is not valid JSON'); }
  const versions = { client: docker?.Client?.Version, server: docker?.Server?.Version };
  if (!Object.values(versions).every(actualIdentity)) throw new Error('Docker identity is incomplete');
  updateFixtureTools(identity, { docker: versions });
  const compose = run('docker', ['compose', 'version', '--short'], { timeout: 30_000 }).trim();
  if (!actualIdentity(compose)) throw new Error('Compose identity is incomplete');
  updateFixtureTools(identity, { compose });
}

export function recordFixtureBrowserIdentity(identity, { channel, version }) {
  updateFixtureTools(identity, {
    chromium: {
      status: version === undefined ? 'pending' : 'recorded', channel,
      ...(version === undefined ? {} : { version }),
    },
  });
}

function fixtureMetadataError(manifest, identity) {
  const { tools, source, browserMode } = manifest;
  const missing = [];
  for (const [name, value] of [
    ['node', tools?.node], ['platform', tools?.platform],
    ['docker.client', tools?.docker?.client], ['docker.server', tools?.docker?.server],
    ['compose', tools?.compose],
  ]) {
    if (!actualIdentity(value)) missing.push(name);
  }
  const browser = tools?.chromium;
  if (identity.kind === 'isolation' && browserMode !== 'browser') missing.push('production browser applicability');
  if (browserMode === 'transport-only') {
    if (browser?.status !== 'not-applicable' || !actualIdentity(browser?.reason)) missing.push('chromium applicability');
  } else if (browserMode === 'browser') {
    if (browser?.status !== 'recorded' || !actualIdentity(browser?.channel)
      || !actualIdentity(browser?.version)) missing.push('chromium channel/version');
  } else missing.push('browser mode');
  const locks = source?.lockfiles;
  if (!Array.isArray(locks) || locks.length !== LOCKFILES.length
    || LOCKFILES.some(path => {
      const proof = locks.find(lock => lock?.path === path);
      return !proof || proof.missing || !Number.isSafeInteger(proof.bytes) || proof.bytes <= 0
        || typeof proof.sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(proof.sha256);
    })) missing.push('lockfile SHA256 proof');
  return missing.length ? `C0 metadata identity/lock proof incomplete: ${missing.join(', ')}` : undefined;
}

export function initializeFixtureEvidence(identity, source, {
  privateState = false, browserMode = 'browser',
} = {}) {
  assertIdentity(identity);
  mkdirSync(dirname(identity.state), { recursive: true });
  // Exclusive creation prevents a reused run identity from consuming stale evidence.
  let statePermissions;
  if (privateState) statePermissions = createPrivateDirectory(identity.state);
  else mkdirSync(identity.state);
  const manifest = {
    runId: identity.runId, project: identity.project, status: 'pending',
    scope: 'C0 fixture + original runtime probes; C1 pending',
    database: { name: 'dragon_test', host: 'db' }, source,
    browserMode,
    tools: {
      node: process.version, platform: process.platform, docker: 'pending', compose: 'pending',
      chromium: browserMode === 'transport-only'
        ? { status: 'not-applicable', reason: TRANSPORT_BROWSER_REASON } : { status: 'pending' },
    },
    inputs: { version: 'C1 pending', publications: [] },
    ...(statePermissions ? { statePermissions } : {}),
  };
  writeFileSync(resolve(identity.state, 'manifest.json'), sanitizeFixtureEvidence(manifest, fixtureEvidenceSecrets(identity)));
  writeFileSync(resolve(identity.state, 'commands.jsonl'), '');
  return manifest;
}

export function recordFixtureCommand(identity, record) {
  appendFileSync(resolve(identity.state, 'commands.jsonl'),
    `${sanitizeFixtureEvidence(record, fixtureEvidenceSecrets(identity))}\n`);
}

export function finishFixtureEvidence(identity, {
  error, cleanup, result, permissions, environment, secretFiles = [],
} = {}, { remove = rmSync } = {}) {
  assertIdentity(identity);
  let secretCleanupError;
  for (const path of [identity.envFile, identity.overrideFile, identity.secretsDir, ...secretFiles]) {
    try {
      remove(path, { recursive: true, force: true });
      if (existsSync(path)) throw new Error(`Secret remains after removal: ${path}`);
    }
    catch (failure) { secretCleanupError ??= failure.message; }
  }
  const manifestFile = resolve(identity.state, 'manifest.json');
  const previous = JSON.parse(readFileSync(manifestFile, 'utf8'));
  delete previous.result;
  delete previous.probeResult;
  delete previous.error;
  delete previous.secretCleanupError;
  delete previous.metadataError;
  const metadataError = fixtureMetadataError(previous, identity);
  const failure = error?.message ?? (cleanup?.status === 'failed' ? cleanup.error ?? 'Cleanup failed' : secretCleanupError);
  const passed = !failure && !secretCleanupError && !metadataError && cleanup?.status === 'removed';
  const manifest = {
    ...previous, status: passed ? 'passed' : 'failed', cleanup,
    ...(previous.schema === 'contract-c5-native-c3-v1' ? { finishedAt: new Date().toISOString() } : {}),
    secretsRemoved: !secretCleanupError,
    ...(secretCleanupError ? { secretCleanupError } : {}),
    ...(metadataError ? { metadataError } : {}),
    ...(permissions ? { permissions } : {}),
    ...(environment ? { environment } : {}),
    ...(passed ? { result } : {
      ...(result ? { probeResult: result } : {}),
      error: failure ?? (cleanup?.status !== 'removed' ? 'Cleanup was not verified' : metadataError),
    }),
  };
  const sanitized = sanitizeFixtureEvidence(manifest, fixtureEvidenceSecrets(identity));
  writeFileSync(manifestFile, sanitized);
  return JSON.parse(sanitized);
}

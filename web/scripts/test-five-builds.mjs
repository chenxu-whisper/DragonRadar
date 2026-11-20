import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import {
  cpSync, existsSync, lstatSync, mkdirSync, mkdtempSync, readFileSync, readdirSync,
  realpathSync, rmSync, writeFileSync,
} from 'node:fs';
import { delimiter, dirname, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { inspectBundle } from './check-default-bundle.mjs';

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
export const FIVE_BUILDS = Object.freeze([
  ['default', '0000'], ['sharing', '0100'], ['reset', '1000'], ['private', '0010'], ['owner', '1011'],
].map(([name, bits]) => Object.freeze({
  name, flags: Object.freeze(Object.fromEntries(
    ['RESET_WORKBENCH', 'PUBLIC_SHARING', 'PRIVATE_REVIEW', 'PRIVATE_AI']
      .map((key, index) => [`VITE_${key}`, bits[index]]))),
})));
const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');
const posix = path => path.replaceAll('\\', '/');
const generatedDirectories = new Set([
  '.git', '.temp', '.venv', 'node_modules', 'dist', '__pycache__', '.pytest_cache',
  '.ruff_cache', '.mypy_cache', 'coverage', 'test-results', 'playwright-report',
]);

function inventory(root, source = false) {
  const files = [];
  function walk(directory) {
    for (const item of readdirSync(directory, { withFileTypes: true })) {
      if (source && (generatedDirectories.has(item.name) || item.name === '.coverage')) continue;
      const path = resolve(directory, item.name);
      if (item.isSymbolicLink()) throw new Error(`Linked input/output is not evidence: ${posix(relative(root, path))}`);
      if (item.isDirectory()) walk(path);
      else if (item.isFile()) {
        const bytes = readFileSync(path);
        files.push({ path: posix(relative(root, path)), size: bytes.length, sha256: sha256(bytes) });
      } else throw new Error('Non-regular input/output is not evidence');
    }
  }
  walk(root);
  return files.sort((a, b) => a.path.localeCompare(b.path, 'en'));
}

function captureSource(root) {
  const files = inventory(root, true);
  return { algorithm: 'sha256', sha256: sha256(JSON.stringify(files)), files };
}

export function resolveNpmCli(env = process.env) {
  const candidates = [
    resolve(dirname(process.execPath), 'node_modules/npm/bin/npm-cli.js'),
    resolve(dirname(process.execPath), '../lib/node_modules/npm/bin/npm-cli.js'),
  ];
  if (env.npm_execpath && /(?:^|[\\/])npm-cli\.js$/.test(env.npm_execpath)) candidates.push(env.npm_execpath);
  for (const directory of (env.PATH ?? env.Path ?? '').split(delimiter).filter(Boolean)) {
    candidates.push(resolve(directory, 'node_modules/npm/bin/npm-cli.js'));
    candidates.push(resolve(directory, '../lib/node_modules/npm/bin/npm-cli.js'));
    const npm = resolve(directory, 'npm');
    if (existsSync(npm)) {
      const target = realpathSync(npm);
      if (target.endsWith('npm-cli.js')) candidates.push(target);
    }
  }
  const candidate = candidates.find(file => existsSync(file) && lstatSync(realpathSync(file)).isFile());
  if (!candidate) throw new Error('npm CLI was not found; use the existing Node/npm installation');
  return realpathSync(candidate);
}

function childEnvironment(parent, flags, runRoot) {
  // Allow infrastructure only. In particular never forward NODE_OPTIONS, VITE_*
  // from the parent, DRAGON_*, AI/model credentials or npm configuration.
  const allowed = new Set(['PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT',
    'TEMP', 'TMP', 'TMPDIR', 'LANG', 'LC_ALL', 'TZ']);
  const env = {};
  for (const [key, value] of Object.entries(parent)) {
    if (allowed.has(key.toUpperCase())) env[key] = value;
  }
  const home = resolve(runRoot, 'home');
  return {
    ...env, ...flags, HOME: home, USERPROFILE: home,
    NODE_ENV: 'production', CI: '1',
    npm_config_cache: resolve(runRoot, 'npm-cache'),
    npm_config_userconfig: resolve(home, '.npmrc'),
    npm_config_globalconfig: resolve(home, 'global.npmrc'),
    npm_config_update_notifier: 'false', npm_config_audit: 'false', npm_config_fund: 'false',
  };
}

function redactor(parent) {
  const values = Object.entries(parent)
    .filter(([key, value]) => value && !Object.hasOwn(FIVE_BUILDS[0].flags, key)
      && /(?:AI|MODEL|SECRET|TOKEN|PASSWORD|CREDENTIAL|DRAGON)/i.test(key))
    .flatMap(([, value]) => [String(value), encodeURIComponent(String(value))])
    .sort((a, b) => b.length - a.length);
  return text => {
    let result = String(text ?? '');
    for (const value of new Set(values)) result = result.replaceAll(value, '[REDACTED]');
    return result;
  };
}

function rejectConfiguration(root) {
  for (const directory of [root, resolve(root, 'web')]) {
    for (const name of ['.env', '.env.local', '.env.production', '.env.production.local', '.npmrc']) {
      if (existsSync(resolve(directory, name))) {
        throw new Error(`Daily dotenv/npm configuration is forbidden: ${posix(relative(root, resolve(directory, name)))}`);
      }
    }
  }
}

export function runFiveBuilds({
  root = projectRoot, evidenceRoot, env = process.env, execute = spawnSync, npmCli,
} = {}) {
  root = realpathSync(root);
  rejectConfiguration(root);
  npmCli ??= resolveNpmCli(env);
  const outputRoot = resolve(evidenceRoot ?? resolve(root, '.temp/five-builds'));
  // Evidence must never become an input, nor overwrite the web output.
  if (outputRoot !== resolve(root, '.temp/five-builds')
    && !relative(root, outputRoot).startsWith('..')) {
    throw new Error('Custom evidence root must be outside the source tree');
  }
  mkdirSync(outputRoot, { recursive: true });
  const runRoot = mkdtempSync(resolve(outputRoot, `run-${new Date().toISOString().replaceAll(/[:.]/g, '-')}-`));
  mkdirSync(resolve(runRoot, 'home'));
  const redact = redactor(env);
  const dist = resolve(root, 'web/dist');
  const result = {
    schema: 'contract-c4-five-builds-v1', status: 'running', runRoot, root,
    startedAt: new Date().toISOString(), source: captureSource(root), builds: [],
  };
  const save = () => writeFileSync(resolve(runRoot, 'run.json'), `${JSON.stringify(result, null, 2)}\n`);
  function assertSource() {
    rejectConfiguration(root);
    if (captureSource(root).sha256 !== result.source.sha256) throw new Error('Source inputs changed during the finite run');
  }
  save();
  let current;
  try {
    for (const combination of FIVE_BUILDS) {
      current = { name: combination.name, flags: combination.flags, status: 'running', commands: [] };
      result.builds.push(current);
      const base = resolve(runRoot, combination.name);
      mkdirSync(base);
      rmSync(dist, { recursive: true, force: true });
      assertSource();
      const childEnv = childEnvironment(env, combination.flags, runRoot);
      for (const [stage, args] of [
        ['build', [npmCli, 'run', 'build', '--', '--manifest']],
        ['check', [npmCli, 'run', 'check:bundle']],
      ]) {
        const started = Date.now();
        let child;
        try {
          child = execute(process.execPath, args, {
            cwd: resolve(root, 'web'), env: childEnv, shell: false, encoding: 'utf8',
            timeout: 600_000, maxBuffer: 32 * 1024 * 1024, windowsHide: true,
          });
        } catch (error) {
          child = { status: null, error };
        }
        const log = (stream, text) => {
          const path = `${combination.name}/${stage}.${stream}.log`;
          const bytes = Buffer.from(redact(text));
          writeFileSync(resolve(runRoot, path), bytes);
          return { path, size: bytes.length, sha256: sha256(bytes) };
        };
        const command = {
          stage, command: process.execPath, args, exitCode: child.status ?? null,
          signal: child.signal ?? null, durationMs: Date.now() - started,
          stdout: log('stdout', child.stdout), stderr: log('stderr', child.stderr),
          ...(child.error ? { error: redact(child.error.message) } : {}),
        };
        current.commands.push(command);
        save();
        if (child.error || child.status !== 0) {
          throw new Error(`${combination.name} ${stage} exited ${child.status ?? 'without an exit code'}${child.error ? `: ${redact(child.error.message)}` : ''}`);
        }
        assertSource();
      }
      const bundle = inspectBundle({ dist, env: childEnv, requireManifest: true });
      const files = inventory(dist);
      const archive = resolve(base, 'dist');
      // Publish an archive only after both commands and all isolation checks pass.
      cpSync(dist, archive, { recursive: true, errorOnExist: true, force: false });
      if (JSON.stringify(inventory(archive)) !== JSON.stringify(files)
        || JSON.stringify(inventory(dist)) !== JSON.stringify(files)) {
        throw new Error('Output changed while archiving');
      }
      assertSource();
      Object.assign(current, {
        status: 'passed', source: result.source, files, bundle,
        manifestSha256: files.find(file => file.path === '.vite/manifest.json').sha256,
      });
      save();
    }
    assertSource();
    result.status = 'passed';
    result.finishedAt = new Date().toISOString();
    save();
    return result;
  } catch (error) {
    result.status = 'failed';
    result.error = redact(error.message);
    result.finishedAt = new Date().toISOString();
    if (current) {
      current.status = 'failed';
      try { current.failedOutput = existsSync(dist) ? inventory(dist) : []; }
      catch { current.failedOutput = null; }
      rmSync(resolve(runRoot, current.name, 'dist'), { recursive: true, force: true });
    }
    rmSync(dist, { recursive: true, force: true });
    save();
    throw new Error(`${result.error}; failure facts: ${resolve(runRoot, 'run.json')}`);
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2);
  if (args.length === 1 && args[0] === '--help') {
    console.log('Finite five-build runner: node web/scripts/test-five-builds.mjs [--evidence-root PATH]');
  } else if (args.length === 0 || (args.length === 2 && args[0] === '--evidence-root')) {
    try {
      const result = runFiveBuilds({ evidenceRoot: args[1] });
      console.log(JSON.stringify({ status: result.status, evidence: resolve(result.runRoot, 'run.json') }));
    } catch (error) {
      console.error(error.message);
      process.exitCode = 1;
    }
  } else {
    console.error('Usage: node web/scripts/test-five-builds.mjs [--evidence-root PATH]');
    process.exitCode = 1;
  }
}

import { readFileSync, readdirSync, lstatSync, existsSync, realpathSync } from 'node:fs';
import { dirname, resolve, relative, isAbsolute } from 'node:path';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

export function bundleFlags(env) {
  return Object.fromEntries(['RESET_WORKBENCH', 'PUBLIC_SHARING', 'PRIVATE_REVIEW', 'PRIVATE_AI']
    .map(name => [`VITE_${name}`, env[`VITE_${name}`] === '1' ? '1' : '0']));
}

export function inspectBundle({ dist, env = process.env, requireManifest = false }) {
  dist = realpathSync(dist);
  const pathInDist = path => {
    const file = resolve(dist, path);
    const rel = relative(dist, file);
    if (!rel || rel.startsWith('..') || isAbsolute(rel)) throw new Error('Asset path escapes dist');
    if (realpathSync(file) !== file) throw new Error('Linked assets are not evidence');
    if (!lstatSync(file).isFile()) throw new Error('Asset is not a regular file');
    return file;
  };
  const nameOf = file => relative(dist, file).replaceAll('\\', '/');
  const allFiles = [];
  function walk(directory) {
    for (const item of readdirSync(directory, { withFileTypes: true })) {
      const file = resolve(directory, item.name);
      if (item.isSymbolicLink()) throw new Error('Linked assets are not evidence');
      if (item.isDirectory()) walk(file);
      else if (item.isFile()) allFiles.push(file);
      else throw new Error('Non-regular dist output');
    }
  }
  walk(dist);
  const html = readFileSync(pathInDist('index.html'), 'utf8');
  const staticAssets = new Set();
  const pending = [];
  function add(file) {
    if (!staticAssets.has(file)) {
      staticAssets.add(file);
      pending.push(file);
    }
  }
  const localAsset = (specifier, base) => {
    if (/^(?:https?:|data:|\/\/)/.test(specifier)) throw new Error('External entry/static asset');
    const path = specifier.split(/[?#]/)[0];
    return pathInDist(relative(dist, path.startsWith('/')
      ? resolve(dist, `.${path}`) : resolve(base, path)));
  };
  const entries = [];
  for (const tag of html.matchAll(/<(script|link)\b[^>]*>/gi)) {
    const attrs = Object.fromEntries([...tag[0].matchAll(/([\w-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')/g)]
      .map(match => [match[1].toLowerCase(), match[2] ?? match[3]]));
    if (tag[1].toLowerCase() === 'script' && attrs.src) {
      const file = localAsset(attrs.src, dist);
      entries.push(file);
      add(file);
    } else if (tag[1].toLowerCase() === 'link' && attrs.href
      && ['stylesheet', 'modulepreload'].includes(attrs.rel)) {
      add(localAsset(attrs.href, dist));
    }
  }
  if (!entries.length) throw new Error('Build entry script was not found');
  const manifestPath = resolve(dist, '.vite/manifest.json');
  let manifest;
  if (existsSync(manifestPath)) {
    manifest = JSON.parse(readFileSync(pathInDist('.vite/manifest.json'), 'utf8'));
    if (!manifest || Array.isArray(manifest) || typeof manifest !== 'object') throw new Error('Invalid manifest');
    const byFile = new Map();
    for (const [key, row] of Object.entries(manifest)) {
      if (!row || typeof row.file !== 'string') throw new Error(`Invalid manifest row ${key}`);
      const file = pathInDist(row.file);
      byFile.set(file, row);
      for (const asset of [...(row.css ?? []), ...(row.assets ?? [])]) pathInDist(asset);
      for (const dependency of [...(row.imports ?? []), ...(row.dynamicImports ?? [])]) {
        if (!Object.hasOwn(manifest, dependency)) throw new Error(`Missing manifest dependency ${dependency}`);
      }
    }
    if (!entries.every(file => byFile.get(file)?.isEntry)) throw new Error('Manifest does not bind HTML entry');
    const visited = new Set();
    function visit(file) {
      if (visited.has(file)) return;
      visited.add(file);
      const row = byFile.get(file);
      if (!row) return;
      add(file);
      for (const asset of [...(row.css ?? []), ...(row.assets ?? [])]) add(pathInDist(asset));
      for (const key of row.imports ?? []) visit(pathInDist(manifest[key].file));
    }
    for (const file of [...staticAssets]) visit(file);
  } else if (requireManifest) {
    throw new Error('Fresh Vite manifest is required');
  }
  while (pending.length) {
    const file = pending.pop();
    const source = readFileSync(file, 'utf8');
    if (file.endsWith('.js') || file.endsWith('.mjs')) {
      const parsed = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, false, ts.ScriptKind.JS);
      for (const statement of parsed.statements) {
        if ((ts.isImportDeclaration(statement) || ts.isExportDeclaration(statement))
          && statement.moduleSpecifier && ts.isStringLiteral(statement.moduleSpecifier)) {
          add(localAsset(statement.moduleSpecifier.text, dirname(file)));
        }
      }
    } else if (file.endsWith('.css')) {
      for (const match of source.matchAll(/@import\s+(?:url\(\s*)?["']([^"']+)["']/g)) {
        add(localAsset(match[1], dirname(file)));
      }
    }
  }
  const flags = bundleFlags(env);
  const reset = flags.VITE_RESET_WORKBENCH === '1';
  const sharing = flags.VITE_PUBLIC_SHARING === '1';
  const privateReview = flags.VITE_PRIVATE_REVIEW === '1';
  const privateAI = flags.VITE_PRIVATE_AI === '1';
  // App's reset LOCAL flow is separate from PrivateApp's owner session.
  const rules = [
    { name: 'local', markers: ['dragon-radar-local-v3'], allowed: reset && !sharing },
    { name: 'review', markers: ['我的复盘', '人工验证', '.review-workspace'],
      allowed: (reset && !sharing) || privateReview },
    { name: 'owner', markers: ['/api/owner/'], allowed: privateReview },
    { name: 'ai', markers: ['/api/ai/'], allowed: privateAI && !sharing },
    { name: 'query', markers: ['/api/query/'], allowed: reset, lazyOnly: true },
    { name: 'sentimentResearch', markers: ['/api/market/sentiment/research'], allowed: reset, lazyOnly: true },
    { name: 'ownerResearch', markers: ['/api/research', '仅研究模拟', '.backtest-panel'],
      allowed: reset && !sharing, lazyOnly: true },
  ];
  const isolation = {};
  for (const rule of rules) {
    const hits = [];
    for (const file of allFiles) {
      if (file === manifestPath) continue;
      const source = readFileSync(file, 'utf8');
      for (const marker of rule.markers) {
        if (!source.includes(marker)) continue;
        hits.push({ file: nameOf(file), marker, static: staticAssets.has(file) });
        if (!rule.allowed) throw new Error(`Build emitted forbidden ${rule.name} marker "${marker}": ${nameOf(file)}`);
        if (rule.lazyOnly && staticAssets.has(file)) {
          throw new Error(`Research/query marker "${marker}" reached the entry static closure: ${nameOf(file)}`);
        }
      }
    }
    isolation[rule.name] = { allowed: rule.allowed, lazyOnly: !!rule.lazyOnly, passed: true, hits };
  }
  const entry = entries[0];
  const entryBytes = lstatSync(entry).size;
  const entryBudget = sharing ? 320_000 : 315_500;
  if (entryBytes > entryBudget) throw new Error(`Entry is ${entryBytes} bytes; budget is ${entryBudget} bytes`);
  return {
    flags, entry: nameOf(entry), entries: entries.map(nameOf), entryBytes, entryBudget,
    staticAssets: [...staticAssets].map(nameOf).sort(),
    manifest: manifest ? '.vite/manifest.json' : null, isolation,
  };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  console.log(JSON.stringify(inspectBundle({ dist: resolve(process.cwd(), 'dist') })));
}

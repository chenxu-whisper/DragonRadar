import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, resolve } from 'node:path';

const dist = resolve(process.cwd(), 'dist');
const html = readFileSync(resolve(dist, 'index.html'), 'utf8');
const entryMatch = html.match(/<script[^>]+src="([^"]+)"[^>]*><\/script>/);
if (!entryMatch) throw new Error('Default build entry script was not found');

const entry = resolve(dist, entryMatch[1].replace(/^\//, ''));
const entryBytes = statSync(entry).size;
const staticAssets = new Set([entry]);
for (const match of html.matchAll(/<link[^>]+href="([^"]+)"/g)) {
  if (match[1].startsWith('/assets/')) staticAssets.add(resolve(dist, match[1].slice(1)));
}
const pending = [entry];
while (pending.length) {
  const file = pending.pop();
  const source = readFileSync(file, 'utf8');
  for (const match of source.matchAll(/\bfrom\s*["']([^"']+)["']/g)) {
    if (!match[1].startsWith('.')) continue;
    const dependency = resolve(dirname(file), match[1]);
    if (!staticAssets.has(dependency)) {
      staticAssets.add(dependency);
      pending.push(dependency);
    }
  }
}

const allScripts = readdirSync(resolve(dist, 'assets'))
  .filter(file => file.endsWith('.js'))
  .map(file => resolve(dist, 'assets', file));
const forbidden = [
  '我的复盘',
  '人工验证',
  '.review-workspace',
  'dragon-radar-local-v3',
  '/api/owner/',
  '/api/ai/',
];
for (const file of new Set([...staticAssets, ...allScripts])) {
  const source = readFileSync(file, 'utf8');
  for (const marker of forbidden) {
    if (source.includes(marker)) throw new Error(`Default static asset contains private marker "${marker}": ${file}`);
  }
}
const entryBudget = process.env.VITE_PUBLIC_SHARING === '1' ? 320_000 : 315_100;
if (entryBytes > entryBudget) {
  throw new Error(`Entry is ${entryBytes} bytes; budget is ${entryBudget} bytes`);
}

console.log(JSON.stringify({
  entry: entry.slice(dist.length + 1).replaceAll('\\', '/'),
  entryBytes,
  staticAssets: [...staticAssets].map(file => file.slice(dist.length + 1).replaceAll('\\', '/')).sort(),
}));

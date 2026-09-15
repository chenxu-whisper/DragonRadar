import { readFileSync, statSync } from 'node:fs';
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

const forbidden = ['我的复盘', '人工验证', '.review-workspace'];
for (const file of staticAssets) {
  const source = readFileSync(file, 'utf8');
  for (const marker of forbidden) {
    if (source.includes(marker)) throw new Error(`Default static asset contains private marker "${marker}": ${file}`);
  }
}
if (entryBytes > 315_000) throw new Error(`Default entry is ${entryBytes} bytes; budget is 315000 bytes`);

console.log(JSON.stringify({
  entry: entry.slice(dist.length + 1).replaceAll('\\', '/'),
  entryBytes,
  staticAssets: [...staticAssets].map(file => file.slice(dist.length + 1).replaceAll('\\', '/')).sort(),
}));

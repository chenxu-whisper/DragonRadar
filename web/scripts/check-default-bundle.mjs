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
// 私有 marker：默认/reset/sharing 镜像的任何产物（含懒 chunk）都不得包含。
const privateMarkers = [
  '我的复盘',
  '人工验证',
  '.review-workspace',
  'dragon-radar-local-v3',
  '/api/owner/',
  '/api/ai/',
];
for (const file of new Set([...staticAssets, ...allScripts])) {
  const source = readFileSync(file, 'utf8');
  for (const marker of privateMarkers) {
    if (source.includes(marker)) throw new Error(`Asset contains private marker "${marker}": ${file}`);
  }
}
// D5：/api/query/ 是 reset-only 白名单查询路径。任何构建下它都不得进入 entry
// 静态闭包（只能以 env 折叠后的懒 chunk 存在）；默认构建（VITE_RESET_WORKBENCH
// 不为 '1'）连该懒 chunk 都不得产出，因此对全部产物兜底扫描。
const resetBuild = process.env.VITE_RESET_WORKBENCH === '1';
const queryScope = resetBuild ? [...staticAssets] : allScripts;
for (const file of queryScope) {
  if (readFileSync(file, 'utf8').includes('/api/query/')) {
    throw new Error(resetBuild
      ? `Reset-only query path reached the entry static closure: ${file}`
      : `Default build emitted a reset-only query asset: ${file}`);
  }
}
// Task 28：/api/market/sentiment/research 与 query 同构：reset-only 研究分区懒
// chunk 不得进入 entry 静态闭包；默认构建（VITE_RESET_WORKBENCH 不为 '1'）连该
// 懒 chunk 都不得产出，因此对全部产物兜底扫描。
const researchScope = resetBuild ? [...staticAssets] : allScripts;
for (const file of researchScope) {
  if (readFileSync(file, 'utf8').includes('/api/market/sentiment/research')) {
    throw new Error(resetBuild
      ? `Reset-only research path reached the entry static closure: ${file}`
      : `Default build emitted a reset-only research asset: ${file}`);
  }
}
const entryBudget = process.env.VITE_PUBLIC_SHARING === '1' ? 320_000 : 315_500;
if (entryBytes > entryBudget) {
  throw new Error(`Entry is ${entryBytes} bytes; budget is ${entryBudget} bytes`);
}

console.log(JSON.stringify({
  entry: entry.slice(dist.length + 1).replaceAll('\\', '/'),
  entryBytes,
  staticAssets: [...staticAssets].map(file => file.slice(dist.length + 1).replaceAll('\\', '/')).sort(),
}));

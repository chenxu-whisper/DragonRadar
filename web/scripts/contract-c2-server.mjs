import { createServer } from 'node:http';
import { createReadStream, existsSync } from 'node:fs';
import { resolve, extname, sep } from 'node:path';

const roots = ['public', 'local', 'owner'];
for (const [index, variant] of roots.entries()) {
  const root = resolve(process.env.C2_EVIDENCE_DIR || '/evidence', 'builds', variant);
  if (!existsSync(resolve(root, 'index.html'))) throw new Error(`Missing actual build: ${root}`);
  createServer((req, res) => {
    const url = new URL(req.url, 'http://127.0.0.1');
    if (url.pathname.startsWith('/api/')) {
      res.writeHead(502, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ code: 'C2_UNMOCKED_API_FAIL_CLOSED', synthetic_proxy: 'http://127.0.0.1:1' }));
      return;
    }
    let path = resolve(root, `.${decodeURIComponent(url.pathname)}`);
    if (!path.startsWith(root + sep)) path = resolve(root, 'index.html');
    if (!existsSync(path) || !extname(path)) path = resolve(root, 'index.html');
    const type = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml' }[extname(path)] || 'application/octet-stream';
    res.writeHead(200, { 'Content-Type': type });
    createReadStream(path).pipe(res);
  }).listen(4171 + index, '127.0.0.1');
}

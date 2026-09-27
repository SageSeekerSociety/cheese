// Serves a docs build under /docs, the way nginx does: /docs/<page> → <page>.html.
//   node gen/serve.mjs <build dir> [port, default 5500]
import { createServer } from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import { extname, join } from 'node:path';
const ROOT = process.argv[2]; const PORT = Number(process.argv[3] || 5500);
const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'application/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.json': 'application/json', '.png': 'image/png', '.md': 'text/markdown; charset=utf-8', '.txt': 'text/plain; charset=utf-8', '.xml': 'application/xml', '.webp': 'image/webp', '.jpg': 'image/jpeg' };
async function file(p) { try { const s = await stat(p); return s.isFile() ? p : null } catch { return null } }
createServer(async (req, res) => {
  let path = decodeURIComponent((req.url || '/').split('?')[0]);
  if (path === '/docs') { res.writeHead(301, { location: '/docs/' }); return res.end() }
  if (!path.startsWith('/docs/')) { res.writeHead(404); return res.end() }
  const rel = path.slice('/docs/'.length);
  const base = join(ROOT, rel);
  const hit = (await file(base)) || (await file(base + '.html')) || (await file(join(base, 'index.html')));
  if (!hit) { res.writeHead(404, { 'content-type': TYPES['.html'] }); return res.end(await readFile(join(ROOT, '404.html'))) }
  res.writeHead(200, { 'content-type': TYPES[extname(hit)] || 'application/octet-stream' });
  res.end(await readFile(hit));
}).listen(PORT, '127.0.0.1', () => console.log('docs on', PORT));

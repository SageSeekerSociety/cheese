const http = require('http'), net = require('net'), fs = require('fs');
const PORT = 5320, UP = { before: 5311, after: 5312 };
const pick = (h) => UP[(/cheese_build=(before|after)/.exec(h.cookie || '') || [])[1] || 'after'];
http.createServer((req, res) => {
  if (req.url.startsWith('/__repro.js')) { res.writeHead(200, { 'content-type': 'text/javascript', 'cache-control': 'no-store' }); return res.end(fs.readFileSync('/tmp/preview/repro.js')); }
  const port = pick(req.headers); const headers = { ...req.headers, host: `127.0.0.1:${port}` }; delete headers['accept-encoding']; delete headers.origin;
  const up = http.request({ host: '127.0.0.1', port, method: req.method, path: req.url, headers }, (r) => {
    const type = r.headers['content-type'] || '';
    if (type.includes('text/html')) {
      let body = ''; r.setEncoding('utf8'); r.on('data', (c) => (body += c));
      r.on('end', () => { body = body.replace('<head>', '<head><script src="/__repro.js"></script>'); const h = { ...r.headers, 'cache-control': 'no-store' }; delete h['content-length']; res.writeHead(r.statusCode, h); res.end(body); });
    } else { res.writeHead(r.statusCode, r.headers); r.pipe(res); }
  });
  up.on('error', (e) => { res.writeHead(502); res.end(String(e)); });
  req.pipe(up);
}).on('upgrade', (req, sock, head) => {
  const u = net.connect(pick(req.headers), '127.0.0.1', () => {
    u.write(`${req.method} ${req.url} HTTP/1.1\r\n` + Object.entries({ ...req.headers, host: `127.0.0.1:${pick(req.headers)}` }).map(([k, v]) => `${k}: ${v}`).join('\r\n') + '\r\n\r\n'); u.write(head); sock.pipe(u).pipe(sock);
  });
  u.on('error', () => sock.destroy()); sock.on('error', () => u.destroy());
}).listen(PORT, '127.0.0.1', () => console.log('preview proxy on', PORT));

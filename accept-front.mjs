// #5210 预览的门口，不进仓库：根地址用开发号进入；按 cookie 把页面转给改前或改后的本地前端。
import http from 'node:http'
const VITES = { before: { host: '127.0.0.1', port: 3427 }, after: { host: '127.0.0.1', port: 3417 } }
const PROXY = { host: '127.0.0.1', port: 3418 }
const which = (req) => (/accept_v=before/.test(req.headers.cookie || '') ? 'before' : 'after')
const forward = (target, req, res, path = req.url, extra = {}) => {
  const p = http.request({ ...target, path, method: req.method, headers: { ...req.headers, ...extra } }, (r) => { res.writeHead(r.statusCode, r.headers); r.pipe(res) })
  p.on('error', (e) => { res.writeHead(502); res.end(String(e)) }); req.pipe(p)
}
http.createServer((req, res) => {
  const dest = req.headers['sec-fetch-dest']
  const nav = dest ? dest === 'document' || dest === 'iframe' : String(req.headers.accept || '').startsWith('text/html')
  if (req.url.startsWith('/__accept/use')) {
    const v = new URL(req.url, 'http://x').searchParams.get('v') === 'before' ? 'before' : 'after'
    res.writeHead(303, { 'set-cookie': `accept_v=${v}; Path=/; SameSite=Lax`, location: '/api/__accept' }); return res.end()
  }
  if (req.method === 'GET' && (req.url === '/' || req.url.startsWith('/?')) && nav) return forward(PROXY, req, res, '/api/__accept/enter')
  if (req.url.startsWith('/api/__accept')) return forward(PROXY, req, res, req.url, { 'x-accept-version': which(req) })
  forward(VITES[which(req)], req, res)
}).listen(3419, '127.0.0.1', () => console.log('front on 3419'))

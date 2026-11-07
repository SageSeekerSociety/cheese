// 预览的门口：打开根地址就用开发号进入、直达 AI 队友名册；其余照转本地前端（含热更新长连接）。
import http from 'node:http'
import net from 'node:net'
const VITE = { host: '127.0.0.1', port: 3417 }, PROXY = { host: '127.0.0.1', port: 3418 }
const forward = (target, req, res, path = req.url) => {
  const p = http.request({ ...target, path, method: req.method, headers: req.headers }, (r) => { res.writeHead(r.statusCode, r.headers); r.pipe(res) })
  p.on('error', (e) => { res.writeHead(502); res.end(String(e)) }); req.pipe(p)
}
import { readFileSync } from 'node:fs'
const PANEL = new URL('./panel.js', import.meta.url)
const TAG = '<script src="/__accept/panel.js"></script>'
const server = http.createServer((req, res) => {
  if (req.method === 'GET' && (req.url === '/' || req.url.startsWith('/?'))) return forward(PROXY, req, res, '/api/__accept/enter')
  if (req.url.startsWith('/__accept/panel.js')) { res.writeHead(200, { 'content-type': 'text/javascript; charset=utf-8', 'cache-control': 'no-store' }); return res.end(readFileSync(PANEL)) }
  // 产品页的 HTML：在 <head> 最前插一行面板脚本（要比应用先包好 fetch），别的原样转
  const accept = String(req.headers.accept || '')
  if (req.method === 'GET' && accept.includes('text/html')) {
    const headers = { ...req.headers }; delete headers['accept-encoding']
    const p = http.request({ ...VITE, path: req.url, method: 'GET', headers }, (r) => {
      if (!String(r.headers['content-type'] || '').includes('text/html')) { res.writeHead(r.statusCode, r.headers); return r.pipe(res) }
      const c = []; r.on('data', (d) => c.push(d)); r.on('end', () => {
        const html = Buffer.concat(c).toString().replace(/<head([^>]*)>/, (m) => `${m}${TAG}`)
        const h = { ...r.headers }; delete h['content-length']; delete h.etag
        res.writeHead(r.statusCode, h); res.end(html)
      })
    })
    p.on('error', (e) => { res.writeHead(502); res.end(String(e)) }); return p.end()
  }
  forward(VITE, req, res)
})
server.on('upgrade', (req, socket, head) => {
  const up = net.connect(VITE.port, VITE.host, () => {
    up.write(`${req.method} ${req.url} HTTP/1.1\r\n` + Object.entries(req.headers).map(([k, v]) => `${k}: ${v}`).join('\r\n') + '\r\n\r\n'); up.write(head); socket.pipe(up); up.pipe(socket)
  })
  up.on('error', () => socket.destroy()); socket.on('error', () => up.destroy())
})
server.listen(3419, '127.0.0.1', () => console.log('front on 3419'))

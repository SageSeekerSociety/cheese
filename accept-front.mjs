// 预览的门口：打开根地址就用开发号进入、直达 AI 队友名册；其余照转本地前端（含热更新长连接）。
import http from 'node:http'
import net from 'node:net'
const VITE = { host: '127.0.0.1', port: 3417 }, PROXY = { host: '127.0.0.1', port: 3418 }
const forward = (target, req, res, path = req.url) => {
  const p = http.request({ ...target, path, method: req.method, headers: req.headers }, (r) => { res.writeHead(r.statusCode, r.headers); r.pipe(res) })
  p.on('error', (e) => { res.writeHead(502); res.end(String(e)) }); req.pipe(p)
}
const server = http.createServer((req, res) => {
  if (req.method === 'GET' && (req.url === '/' || req.url.startsWith('/?'))) return forward(PROXY, req, res, '/api/__accept/enter')
  forward(VITE, req, res)
})
server.on('upgrade', (req, socket, head) => {
  const up = net.connect(VITE.port, VITE.host, () => {
    up.write(`${req.method} ${req.url} HTTP/1.1\r\n` + Object.entries(req.headers).map(([k, v]) => `${k}: ${v}`).join('\r\n') + '\r\n\r\n'); up.write(head); socket.pipe(up); up.pipe(socket)
  })
  up.on('error', () => socket.destroy()); socket.on('error', () => up.destroy())
})
server.listen(3419, '127.0.0.1', () => console.log('front on 3419'))

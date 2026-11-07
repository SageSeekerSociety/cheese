// PR #5194 验收控制层：只给本地隔离测试栈用，不进仓库。
// 在 vite(23000) 前面转发一切（含 HMR websocket），往页面里注入一块控制面板；
// 能扣住/放行 alice 查自己资料库的请求、记录每次上传带的是谁的凭据、列出两人资料库。
import http from 'node:http'
import net from 'node:net'
const PORT = Number(process.env.ACCEPT_PORT || 23100)
const VITE = { host: '127.0.0.1', port: 23000 }
const BACKEND = 'http://127.0.0.1:28081'
const USERS = { alice: 'demo12345', bobby: 'demo12345' }

let holding = false
let waiters = []                      // 被扣住的请求：放行时依次转发
const uploads = []                    // 上传相关请求的记录
const lookups = []                    // 查资料库归属的请求记录

const who = (auth) => {
  try {
    const p = JSON.parse(Buffer.from((auth || '').replace(/^Bearer /, '').split('.')[1], 'base64url').toString())
    return { 1: 'alice', 2: 'bobby' }[p.sub] ?? `用户 ${p.sub}`
  } catch { return '无凭据' }
}
const now = () => new Date().toLocaleTimeString('zh-CN', { hour12: false, timeZone: 'Asia/Shanghai' })

async function backend(path, opts = {}, token) {
  const r = await fetch(BACKEND + path, { ...opts, headers: { 'content-type': 'application/json', ...(token ? { authorization: `Bearer ${token}` } : {}) } })
  return (await r.json()).data
}
async function shelf(user) {
  const { accessToken } = await backend('/users/auth/login', { method: 'POST', body: JSON.stringify({ username: user, password: USERS[user] }) })
  await backend('/teams/my-teams', {}, accessToken)
  const { team } = await backend(`/teams/by-handle/${user}`, {}, accessToken)
  const list = (await backend(`/teams/${team.id}/library`, {}, accessToken)).data
  return { token: accessToken, team: team.id, files: list.filter((f) => f.type === 'file').map((f) => ({ name: f.path, by: f.added_by, at: f.added_at })) }
}

function forward(req, res, body) {
  const up = http.request({ ...VITE, method: req.method, path: req.url, headers: { ...req.headers, host: '127.0.0.1:23000' } }, (r) => {
    const html = (r.headers['content-type'] || '').includes('text/html') && req.method === 'GET'
    if (!html) { res.writeHead(r.statusCode, r.headers); r.pipe(res); return }
    let chunks = []
    r.on('data', (c) => chunks.push(c))
    r.on('end', () => {
      const text = Buffer.concat(chunks).toString().replace('</body>', '<script type="module" src="/__accept/panel.js"></script></body>')
      const h = { ...r.headers }; delete h['content-length']
      res.writeHead(r.statusCode, h); res.end(text)
    })
  })
  up.on('error', (e) => { res.writeHead(502); res.end(String(e)) })
  up.end(body)
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://x')
  if (url.pathname === '/' && req.method === 'GET') {   // 打开根地址直接进演示项目，面板在那里接手
    res.writeHead(302, { location: '/projects/0v5lb0qt/overview' }); res.end(); return
  }
  if (url.pathname === '/__accept/panel.js') {
    res.writeHead(200, { 'content-type': 'text/javascript; charset=utf-8', 'cache-control': 'no-store' })
    res.end(await import('node:fs').then((fs) => fs.readFileSync(new URL('./panel.js', import.meta.url))))
    return
  }
  if (url.pathname === '/__accept/hold') {
    holding = url.searchParams.get('on') === '1'
    if (!holding) { const w = waiters; waiters = []; w.forEach((go) => go()) }
    res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify({ holding }))
    return
  }
  if (url.pathname === '/__accept/state') {
    let libraries = {}
    try { for (const u of Object.keys(USERS)) { const s = await shelf(u); libraries[u] = { team: s.team, files: s.files } } } catch (e) { libraries = { error: String(e) } }
    res.writeHead(200, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' })
    res.end(JSON.stringify({ holding, waiting: waiters.length, uploads: uploads.slice(-30), lookups: lookups.slice(-20), libraries }))
    return
  }
  if (url.pathname === '/__accept/clear' && req.method === 'POST') {
    for (const u of Object.keys(USERS)) {
      const s = await shelf(u)
      for (const f of s.files) await fetch(`${BACKEND}/teams/${s.team}/library?path=${encodeURIComponent(f.name)}`, { method: 'DELETE', headers: { authorization: `Bearer ${s.token}` } })
    }
    uploads.length = 0; lookups.length = 0
    res.writeHead(200, { 'content-type': 'application/json' }); res.end('{"ok":true}')
    return
  }
  const chunks = []
  req.on('data', (c) => chunks.push(c))
  req.on('end', () => {
    const body = Buffer.concat(chunks)
    const p = url.pathname
    if (/^\/api\/teams\/by-handle\//.test(p)) {
      const entry = { time: now(), path: p.replace('/api', ''), as: who(req.headers.authorization), held: false }
      lookups.push(entry)
      if (holding && p.endsWith('/alice')) {
        entry.held = true
        waiters.push(() => { entry.released = now(); forward(req, res, body) })
        return
      }
    }
    if (/^\/api\/teams\/\d+\/library(\/uploads(\/part)?)?$/.test(p) && req.method === 'POST') {
      const entry = { time: now(), path: p.replace('/api', ''), as: who(req.headers.authorization) }
      uploads.push(entry)
      res.on('finish', () => { entry.status = res.statusCode })
    }
    forward(req, res, body)
  })
})
server.on('upgrade', (req, socket, head) => {
  const up = net.connect(VITE.port, VITE.host, () => {
    up.write(`${req.method} ${req.url} HTTP/1.1\r\n` + Object.entries({ ...req.headers, host: "127.0.0.1:23000" }).map(([k, v]) => `${k}: ${v}`).join('\r\n') + '\r\n\r\n')
    up.write(head); up.pipe(socket); socket.pipe(up)
  })
  up.on('error', () => socket.destroy()); socket.on('error', () => up.destroy())
})
server.listen(PORT, '127.0.0.1', () => console.log(`accept proxy on ${PORT}`))

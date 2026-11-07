// Isolated acceptance preview for PR #5197 (bookmark rename draft).
// Front proxy on PREVIEW_PORT: the app itself comes from the branch's vite dev
// server (VITE_PORT); /api reads go to production as the shared dev account;
// every write is answered here and never reaches production; the test channel's
// bookmarks are two fake rows kept in memory; /__preview/* drives the test.
'use strict'
const http = require('http')
const net = require('net')
const { WebSocketServer, WebSocket } = require('ws')

const PREVIEW_PORT = +(process.env.PREVIEW_PORT || 3218)
const VITE_PORT = +(process.env.VITE_PORT || 3217)
const UP = 'https://okcheese.com/api'
const PID = 'de808b13-ffd2-4b8a-9d1d-fba7babe389f'
const ROOM = '0139507e-c89b-4cdd-9b9d-699e91f2bf1f' // 测试频道
const HOME = `/projects/${PID}/topics/${ROOM}`
const CRED = { username: 'wangchangxin', password: process.env.PW }

// ---- dev-account token, refreshed before it expires ----
let auth = null
let authExp = 0
async function token() {
  if (auth && Date.now() < authExp - 120_000) return auth
  const r = await fetch(`${UP}/users/auth/login`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(CRED),
  })
  auth = (await r.json()).data
  const payload = JSON.parse(Buffer.from(auth.accessToken.split('.')[1], 'base64url').toString())
  authExp = payload.exp * 1000
  return auth
}

// ---- fake bookmarks ----
let blocks = []
let rows = []
let otherRenames = 0
const stats = { chapterReads: 0, blockedWrites: 0, lastBlocked: null, savedByYou: [] }
const TITLES = ['发布前检查', '回滚预案']
function resetRows() {
  otherRenames = 0
  rows = blocks.map((b, i) => ({ pinned_by: 'wangchangxin', pinned_at: b.created_at, title: TITLES[i], block: b }))
}
async function loadBlocks() {
  const a = await token()
  const j = await (
    await fetch(`${UP}/topics/${ROOM}/blocks?limit=30`, { headers: { authorization: 'Bearer ' + a.accessToken } })
  ).json()
  blocks = j.data.data.filter((x) => x.kind === 'message').slice(0, 2)
  resetRows()
}

// ---- room sockets, for the pushed "bookmarks changed" frame ----
const roomSockets = new Set()
function pushChaptersChanged() {
  let n = 0
  for (const s of roomSockets)
    if (s.readyState === WebSocket.OPEN) {
      s.send(JSON.stringify({ type: 'state', resource: 'chapters' }))
      n++
    }
  return n
}
function swapChapters(o) {
  if (o && typeof o === 'object')
    for (const k of Object.keys(o)) {
      if (k === 'chapters' && Array.isArray(o[k])) o[k] = rows
      else swapChapters(o[k])
    }
}

const ok = (data = null) => JSON.stringify({ code: 200, message: 'ok', data })
function sendJson(res, body, status = 200) {
  res.writeHead(status, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' })
  res.end(body)
}
function readBody(req) {
  return new Promise((resolve) => {
    const parts = []
    req.on('data', (c) => parts.push(c))
    req.on('end', () => resolve(Buffer.concat(parts).toString()))
  })
}

async function handleApi(req, res, path) {
  const method = req.method
  if (method === 'POST' && /^\/users\/auth\/(login|refresh)$/.test(path)) return sendJson(res, ok(await token()))
  const ch = path.match(new RegExp(`^/topics/${ROOM}/chapters(?:/([^/?]+))?(?:\\?.*)?$`))
  if (ch) {
    const id = ch[1] && decodeURIComponent(ch[1])
    if (method === 'GET' && !id) {
      stats.chapterReads++
      return sendJson(res, ok(rows))
    }
    const body = await readBody(req)
    if (method === 'PATCH' && id) {
      const title = JSON.parse(body || '{}').title
      rows = rows.map((r) => (r.block.id === id ? { ...r, title } : r))
      stats.savedByYou.push({ id, title, at: new Date().toISOString() })
    } else if (method === 'DELETE' && id) rows = rows.filter((r) => r.block.id !== id)
    // PUT (add) is not modelled: the two fake rows are the whole list.
    return sendJson(res, ok())
  }
  if (method !== 'GET' && method !== 'HEAD') {
    await readBody(req)
    stats.blockedWrites++
    stats.lastBlocked = `${method} ${path.split('?')[0]}`
    return sendJson(res, ok())
  }
  const a = await token()
  const up = await fetch(UP + path, {
    headers: { authorization: 'Bearer ' + a.accessToken, accept: req.headers.accept || '*/*' },
  })
  const headers = {}
  up.headers.forEach((v, k) => {
    if (!['content-encoding', 'content-length', 'set-cookie', 'transfer-encoding', 'connection'].includes(k))
      headers[k] = v
  })
  res.writeHead(up.status, headers)
  res.end(Buffer.from(await up.arrayBuffer()))
}

function panelScript(a) {
  return `<script>
try { localStorage.setItem('accessToken', ${JSON.stringify(a.accessToken)}); localStorage.setItem('user', ${JSON.stringify(JSON.stringify(a.user))}); } catch (e) {}
addEventListener('DOMContentLoaded', function () {
  var p = document.createElement('div');
  p.id = 'bm-preview-panel';
  p.style.cssText = 'position:fixed;right:16px;bottom:16px;z-index:2147483647;width:300px;background:#fff;border:2px solid #e8792b;border-radius:10px;box-shadow:0 6px 24px rgba(0,0,0,.18);padding:12px;font:13px/1.5 system-ui,sans-serif;color:#222';
  p.innerHTML = '<b>隔离验收预览 · PR #5197</b>'
    + '<div style="color:#666;margin:4px 0 8px">左上角书签是两条假书签；所有写请求在预览层拦截，不写生产。</div>'
    + '<button id="bmp-other" style="width:100%;padding:8px;border:0;border-radius:6px;background:#e8792b;color:#fff;font-weight:600;cursor:pointer">另一条书签改名 → 触发真实刷新</button>'
    + '<button id="bmp-reset" style="width:100%;margin-top:6px;padding:6px;border:1px solid #ccc;border-radius:6px;background:#fafafa;cursor:pointer">重置两条假书签</button>'
    + '<div id="bmp-state" style="margin-top:8px;color:#444;white-space:pre-line"></div>';
  document.body.appendChild(p);
  // The panel sits outside the rail: pressing it must not count as a click outside
  // that closes the rename box, so keep the pointer/focus events to itself.
  ['pointerdown','mousedown'].forEach(function (t) { p.addEventListener(t, function (e) { e.preventDefault(); e.stopPropagation(); }, true); });
  function act(path) { fetch(path, { method: 'POST' }).then(function (r) { return r.json(); }).then(show); }
  document.getElementById('bmp-other').addEventListener('click', function () { act('/__preview/rename-other'); });
  document.getElementById('bmp-reset').addEventListener('click', function () { act('/__preview/reset'); });
  function show(s) {
    var last = s.savedByYou.length ? s.savedByYou[s.savedByYou.length - 1].title : '（还没有）';
    document.getElementById('bmp-state').textContent =
      '假书签：1「' + s.rows[0] + '」 2「' + s.rows[1] + '」\\n'
      + '名单被重取：' + s.chapterReads + ' 次\\n'
      + '你回车存下的名字：' + last + '\\n'
      + '已拦截写请求：' + s.blockedWrites + (s.lastBlocked ? '（最近 ' + s.lastBlocked + '）' : '')
      + (s.pushed != null ? '\\n刚推送变更到 ' + s.pushed + ' 个连接' : '');
  }
  function poll() { fetch('/__preview/state').then(function (r) { return r.json(); }).then(show).catch(function () {}); }
  poll(); setInterval(poll, 1500);
});
</script>`
}

function state(extra = {}) {
  return JSON.stringify({ ...stats, rows: rows.map((r) => r.title), ...extra })
}

async function handlePreview(req, res, path) {
  if (path === '/__preview/rename-other' && req.method === 'POST') {
    otherRenames++
    rows = rows.map((r, i) => (i === 1 ? { ...r, title: `${TITLES[1]}（别人第 ${otherRenames} 次改名）` } : r))
    return sendJson(res, state({ pushed: pushChaptersChanged() }))
  }
  if (path === '/__preview/reset' && req.method === 'POST') {
    resetRows()
    stats.savedByYou = []
    return sendJson(res, state({ pushed: pushChaptersChanged() }))
  }
  return sendJson(res, state())
}

function proxyToVite(req, res) {
  const headers = { ...req.headers, host: `127.0.0.1:${VITE_PORT}` }
  delete headers['accept-encoding']
  const up = http.request({ host: '127.0.0.1', port: VITE_PORT, method: req.method, path: req.url, headers }, (ur) => {
    const type = ur.headers['content-type'] || ''
    if (!type.includes('text/html')) {
      res.writeHead(ur.statusCode, ur.headers)
      return ur.pipe(res)
    }
    const parts = []
    ur.on('data', (c) => parts.push(c))
    ur.on('end', async () => {
      let html = Buffer.concat(parts).toString()
      html = html.replace('<head>', '<head>' + panelScript(await token()))
      const h = { ...ur.headers, 'cache-control': 'no-store' }
      delete h['content-length']
      delete h.etag
      res.writeHead(ur.statusCode, h)
      res.end(html)
    })
  })
  up.on('error', (e) => {
    res.writeHead(502)
    res.end('vite not reachable: ' + e.message)
  })
  req.pipe(up)
}

const server = http.createServer(async (req, res) => {
  try {
    const url = req.url
    if (url === '/' || url === '') {
      res.writeHead(302, { location: HOME })
      return res.end()
    }
    if (url.startsWith('/__preview/')) return await handlePreview(req, res, url.split('?')[0])
    if (url.startsWith('/api/')) return await handleApi(req, res, url.slice(4))
    if (url.startsWith('/users/') && !(req.headers.accept || '').includes('text/html'))
      return await handleApi(req, res, url)
    return proxyToVite(req, res)
  } catch (e) {
    console.error('request failed', req.method, req.url, e)
    if (!res.headersSent) sendJson(res, JSON.stringify({ code: 500, message: String(e) }), 500)
  }
})

const wss = new WebSocketServer({ noServer: true })
server.on('upgrade', async (req, socket, head) => {
  if (req.url.startsWith('/api/')) {
    const a = await token()
    const u = new URL('wss://okcheese.com' + req.url)
    if (u.searchParams.has('token')) u.searchParams.set('token', a.accessToken)
    wss.handleUpgrade(req, socket, head, (client) => {
      const isRoom = req.url.startsWith('/api/rooms/live')
      const upstream = new WebSocket(u.toString(), { headers: { authorization: 'Bearer ' + a.accessToken } })
      const queue = []
      upstream.on('open', () => queue.splice(0).forEach((m) => upstream.send(m)))
      upstream.on('message', (data, isBinary) => {
        let m = isBinary ? data : data.toString()
        if (!isBinary && m.includes('"chapters"')) {
          try {
            const o = JSON.parse(m)
            swapChapters(o)
            m = JSON.stringify(o)
          } catch {}
        }
        if (client.readyState === WebSocket.OPEN) client.send(m)
      })
      client.on('message', (data, isBinary) => {
        const m = isBinary ? data : data.toString()
        if (upstream.readyState === WebSocket.OPEN) upstream.send(m)
        else queue.push(m)
      })
      const close = () => {
        roomSockets.delete(client)
        try { client.close() } catch {}
        try { upstream.close() } catch {}
      }
      upstream.on('close', close)
      upstream.on('error', close)
      client.on('close', close)
      if (isRoom) roomSockets.add(client)
    })
    return
  }
  // Anything else (vite's HMR socket) goes to vite untouched.
  const v = net.connect(VITE_PORT, '127.0.0.1', () => {
    let raw = `${req.method} ${req.url} HTTP/1.1\r\n`
    for (let i = 0; i < req.rawHeaders.length; i += 2) raw += `${req.rawHeaders[i]}: ${req.rawHeaders[i + 1]}\r\n`
    v.write(raw + '\r\n')
    if (head && head.length) v.write(head)
    v.pipe(socket)
    socket.pipe(v)
  })
  v.on('error', () => socket.destroy())
  socket.on('error', () => v.destroy())
})

loadBlocks().then(() => {
  server.listen(PREVIEW_PORT, '127.0.0.1', () => console.log(`preview on :${PREVIEW_PORT}, vite :${VITE_PORT}`))
})

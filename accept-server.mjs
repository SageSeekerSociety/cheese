// #5208 验收入口：一个进程两个口。
//  3419 门口：打开根地址用开发号登录、直达有芝士面板的页面；产品页、模块照转本地前端（3417）；
//        「版本」切到改前时，芝士对话那个模块换成主干原文（frontend/src/stores/agentThread.ts @ main）。
//  3418 后端代理：读照转 okcheese.com（开发号身份）；芝士对话的读写全在这里假造，别的写请求一律拦下不到生产。
// 芝士对话的时序由控制台（/api/__accept）定：第一问写一段字后不送 done（服务端已答完、收尾帧在途），
// 这时插话回 409「芝士现在没有在回答」（或 500、或正常收下），点「放行收尾帧」才送 done。
import http from 'node:http'
import https from 'node:https'
import net from 'node:net'
import { readFileSync } from 'node:fs'
import { URL } from 'node:url'

const VITE = { host: '127.0.0.1', port: 3417 }
const UPSTREAM = 'https://okcheese.com'
const DEV_USER = process.env.DEV_USER, DEV_PASS = process.env.DEV_PASS
const MAIN_MODULE = process.env.MAIN_MODULE // vite 转换好的主干版模块文本
const ENTRY = process.env.ENTRY_PATH || '/settings'

const DEFAULTS = () => ({ version: 'after', say: '409', autoDone: 0 })
let cfg = DEFAULTS(), conv, answering, log
const sleep = (s) => new Promise((r) => setTimeout(r, s * 1000))
const note = (s) => { log.unshift(`${new Date().toISOString().slice(11, 19)} UTC  ${s}`); log.length = Math.min(log.length, 200) }
function reset() {
  if (answering) answering.finish()
  conv = null; answering = null; log = []
}
reset()

const send = (res, status, obj) => { res.writeHead(status, { 'content-type': 'application/json; charset=utf-8' }); res.end(JSON.stringify(obj)) }
const ok = (res, data) => send(res, 200, { code: 200, message: 'ok', data })
const readBody = (req) => new Promise((r) => { const c = []; req.on('data', (d) => c.push(d)); req.on('end', () => r(Buffer.concat(c))) })
const frame = (event, data) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`
let seq = 0
const add = (role, text) => conv.messages.push({ role, text, at: new Date().toISOString(), seq: ++seq })

function upstream(req, path, body, { buffer = false } = {}) {
  return new Promise((resolve, reject) => {
    const headers = { ...req.headers, host: 'okcheese.com' }
    delete headers['accept-encoding']; delete headers['content-length']; delete headers.origin; delete headers.referer
    if (body?.length) headers['content-length'] = body.length
    const u = https.request(new URL(path, UPSTREAM), { method: req.method, headers }, (r) => {
      if (!buffer) return resolve({ stream: r })
      const c = []; r.on('data', (d) => c.push(d)); r.on('end', () => resolve({ status: r.statusCode, headers: r.headers, body: Buffer.concat(c) }))
    })
    u.on('error', reject); if (body?.length) u.write(body); u.end()
  })
}
async function pass(req, res, path, body) {
  try {
    const { stream } = await upstream(req, path, body)
    const h = { ...stream.headers }; delete h['content-security-policy']
    res.writeHead(stream.statusCode, h); stream.pipe(res)
  } catch (e) { send(res, 502, { message: `生产读取失败：${e.message}` }) }
}
async function devLogin() {
  const body = Buffer.from(JSON.stringify({ username: DEV_USER, password: DEV_PASS }))
  const r = await upstream({ method: 'POST', headers: { 'content-type': 'application/json' } }, '/api/users/auth/login', body, { buffer: true })
  const j = JSON.parse(r.body.toString()); if (!j.data?.accessToken) throw new Error(`开发号登录失败 ${r.status}`)
  return j.data
}

function controlPage() {
  const sel = (name, value, opts) => `<select name="${name}">${opts.map(([v, l]) => `<option value="${v}"${String(value) === v ? ' selected' : ''}>${l}</option>`).join('')}</select>`
  return `<!doctype html><meta charset="utf-8"><title>#5208 验收控制台</title>
<style>body{font:14px/1.6 system-ui,sans-serif;max-width:860px;margin:20px auto;padding:0 16px}fieldset{margin:8px 0}legend{font-weight:600}pre{background:#f5f5f5;padding:8px;max-height:320px;overflow:auto;white-space:pre-wrap}
#hold{padding:8px;border:1px solid #f0c36d;background:#fff8e6;margin:6px 0}button{padding:4px 12px}</style>
<h2>#5208 芝士插话验收控制台</h2>
<p>预览页右下角的芝士面板读写的是这里假造的一段对话，不碰生产。步骤：在面板里问一句 → 看到第一段字后（这时回答「答完了、收尾帧还在路上」）再发一句补充 → 回这里点「放行收尾帧」。</p>
<div id="hold">（还没有在答的问题）</div>
<form method="post" action="/api/__accept/config">
<fieldset><legend>页面上跑哪一版</legend>${sel('version', cfg.version, [['after', '改后（本 PR 分支）'], ['before', '改前（主干 3776784 的 agentThread.ts）']])} 改完要刷新预览页</fieldset>
<fieldset><legend>收尾帧在途时，插话（POST /say）怎么回</legend>${sel('say', cfg.say, [['409', '409 芝士现在没有在回答（服务端已答完，本问题的时序）'], ['500', '500 服务器出错（别的失败：不该重发）'], ['ok', '正常收下（芝士还在答）']])}</fieldset>
<fieldset><legend>收尾帧</legend>${sel('autoDone', cfg.autoDone, [['0', '手动放行'], ['3', '插话后 3 秒自动送']])}</fieldset>
<button>保存设置</button> &nbsp; <a href="/api/__accept/reset">清空对话与日志</a> &nbsp; <a href="/" target="_blank">打开预览页</a>
</form>
<h3>假造对话里服务端存下的消息</h3><pre id="msgs"></pre>
<h3>日志（新的在上）</h3><pre id="log"></pre>
<script>
async function tick(){try{const j=await (await fetch('/api/__accept/state')).json();
document.getElementById('msgs').textContent=j.messages.join(String.fromCharCode(10))||'（还没有）';
document.getElementById('log').textContent=j.log.join(String.fromCharCode(10))||'（还没有）';
const box=document.getElementById('hold');
if(j.holding){box.innerHTML='<b>在答：</b>'+j.holding+' — 第一段字已送出，收尾帧还没送 ';const b=document.createElement('button');b.textContent='放行收尾帧';b.onclick=async()=>{await fetch('/api/__accept/done');tick()};box.appendChild(b)}
else box.textContent='（没有挂着的收尾帧）'}catch(e){}}
tick();setInterval(tick,1000)
</script>`
}

// ---- 3418 后端代理 ----
http.createServer(async (req, res) => {
  const path = req.url, m = req.method
  const body = m === 'GET' || m === 'HEAD' ? null : await readBody(req)
  try {
    if (path === '/api/__accept' || path === '/api/__accept/') { res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' }); return res.end(controlPage()) }
    if (path === '/api/__accept/config' && m === 'POST') {
      const p = new URLSearchParams(body.toString()); cfg = { version: p.get('version'), say: p.get('say'), autoDone: Number(p.get('autoDone')) }
      note(`控制台：设置 ${JSON.stringify(cfg)}`); res.writeHead(303, { location: '/api/__accept' }); return res.end()
    }
    if (path.startsWith('/api/__accept/set?')) { const q = new URL(path, 'http://x').searchParams; for (const [k, v] of q) cfg[k] = k === 'autoDone' ? Number(v) : v; note(`控制台：设置 ${JSON.stringify(cfg)}`); return send(res, 200, cfg) }
    if (path === '/api/__accept/reset') { reset(); note('控制台：清空'); res.writeHead(303, { location: '/api/__accept' }); return res.end() }
    if (path === '/api/__accept/done') { const a = answering; if (a) { note('控制台：放行收尾帧'); a.finish() } return send(res, 200, { ok: !!a }) }
    if (path === '/api/__accept/state') return send(res, 200, { messages: (conv?.messages ?? []).map((x) => `#${x.seq} ${x.role}: ${x.text}`), log, holding: answering?.question ?? null })
    if (path.startsWith('/api/__accept/enter')) {
      const d = await devLogin()
      res.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' })
      return res.end(`<!doctype html><meta charset="utf-8"><p>正在用开发号进入 #5208 验收预览…</p><script>
localStorage.setItem('accessToken', ${JSON.stringify(d.accessToken)});${d.user ? `localStorage.setItem('user', ${JSON.stringify(JSON.stringify(d.user))});` : ''}
location.replace(${JSON.stringify(ENTRY)})</script>`)
    }

    // 芝士对话：全部假造
    let mm
    if (path.startsWith('/api/assistant/conversations')) {
      const p = path.split('?')[0]
      if (p === '/api/assistant/conversations' && m === 'GET') return ok(res, { conversations: conv ? [{ id: conv.id, title: conv.title, lastActiveAt: new Date().toISOString(), questions: 1 }] : [], next: null })
      if (p === '/api/assistant/conversations' && m === 'POST') { conv = { id: 'c-accept-5208', title: '#5208 验收', messages: [] }; note('新建对话'); return send(res, 201, { code: 200, data: { id: conv.id, title: conv.title, lastActiveAt: new Date().toISOString() } }) }
      if ((mm = p.match(/^\/api\/assistant\/conversations\/([^/]+)$/)) && m === 'GET') {
        if (!conv || conv.id !== mm[1]) return send(res, 404, { message: 'not found' })
        return ok(res, { id: conv.id, title: conv.title, lastActiveAt: new Date().toISOString(), messages: conv.messages, answering: null, earlier: false })
      }
      if (p.endsWith('/messages')) return ok(res, { messages: [], earlier: false })
      if (p.endsWith('/looking') || p.endsWith('/stop')) return ok(res, {})
      if (p.endsWith('/ask') && m === 'POST') {
        const q = JSON.parse(body.toString()).question
        add('user', q)
        res.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' })
        if (answering === null && conv.messages.filter((x) => x.role === 'user').length === 1) {
          const text = '先把三份报告各读一遍，再按评分表逐项打分。'
          note(`问「${q}」：送出第一段字，收尾帧挂着（服务端这一轮已答完）`)
          res.write(frame('answering', { id: 'q1' })); res.write(frame('delta', { text, at: 0 }))
          answering = { question: q, finish() { answering = null; add('assistant', text); res.write(frame('done', {})); res.end(); note('收尾帧送达') } }
          return
        }
        const text = `收到，按你后来说的「${q}」来。`
        note(`问「${q}」：直接答完`)
        res.write(frame('answering', { id: `q${seq}` })); res.write(frame('delta', { text, at: 0 })); add('assistant', text); res.write(frame('done', {})); return res.end()
      }
      if (p.endsWith('/say') && m === 'POST') {
        const t = JSON.parse(body.toString()).text
        if (cfg.autoDone && answering) { const a = answering; sleep(cfg.autoDone).then(() => a === answering && a.finish()) }
        if (cfg.say === '409' || !answering) { note(`插话「${t}」→ 409 芝士现在没有在回答（服务端没存）`); return send(res, 409, { code: 409, message: '芝士现在没有在回答，直接提问即可', error: { name: 'ConflictError', message: '芝士现在没有在回答，直接提问即可', data: null, retryable: false, i18n: { key: 'assistantNotAnswering', params: {} } } }) }
        if (cfg.say === '500') { add('user', t); note(`插话「${t}」→ 服务端存下了这句，然后回 500`); return send(res, 500, { code: 500, message: '服务器出错了（验收模拟）', error: { name: 'InternalServerError', message: '服务器出错了（验收模拟）', data: null, retryable: false } }) }
        add('user', t); note(`插话「${t}」→ 收下`); return ok(res, { said: true })
      }
      note(`拦下：${m} ${p}`); return ok(res, {})
    }
    if (m !== 'GET' && m !== 'HEAD' && m !== 'OPTIONS') {
      if (path.startsWith('/api/users/auth/refresh-token')) { const d = await devLogin(); note('令牌到期：代理用开发号重新登录'); return ok(res, { accessToken: d.accessToken, user: d.user }) }
      if (/^\/api\/users\/auth\/login/.test(path)) return pass(req, res, path, body)
      note(`拦下：${m} ${path.split('?')[0]}（没有转给生产）`)
      return ok(res, {})
    }
    return pass(req, res, path, body)
  } catch (e) { send(res, 500, { message: `验收代理出错：${e.message}` }) }
}).listen(3418, '127.0.0.1', () => console.log('proxy on 3418'))

// ---- 3419 门口 ----
const forward = (target, req, res, path = req.url) => {
  const p = http.request({ ...target, path, method: req.method, headers: req.headers }, (r) => { res.writeHead(r.statusCode, r.headers); r.pipe(res) })
  p.on('error', (e) => { res.writeHead(502); res.end(String(e)) }); req.pipe(p)
}
const server = http.createServer((req, res) => {
  const dest = req.headers['sec-fetch-dest']
  const isNavigation = dest ? dest === 'document' : String(req.headers.accept || '').startsWith('text/html')
  if (req.method === 'GET' && (req.url === '/' || req.url.startsWith('/?')) && isNavigation) return forward({ host: '127.0.0.1', port: 3418 }, req, res, '/api/__accept/enter')
  if (req.url.startsWith('/api/__accept')) return forward({ host: '127.0.0.1', port: 3418 }, req, res)
  if (cfg.version === 'before' && MAIN_MODULE && /^\/src\/stores\/agentThread\.ts(\?|$)/.test(req.url)) {
    res.writeHead(200, { 'content-type': 'text/javascript; charset=utf-8', 'cache-control': 'no-store' })
    return res.end(readFileSync(MAIN_MODULE))
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

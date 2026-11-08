// #5210 验收代理：挡在本地前端和 okcheese.com 之间，不进仓库。
// 读：照转生产，一律以平台开发号的身份（/api/__accept/enter 自动登录，令牌到期代理自己续）。
// 写：除登录/刷新令牌外一律在这里拦下，不到生产。
// 测试对象：名册最前多一位假队友「测试队友A（假）」，设备列表换成一台假机器，机器上扫出 pdf。
// 控制台（/api/__accept）定：A 带什么（什么都没带 / 带上 pdf），机器上有什么。
import http from 'node:http'
import https from 'node:https'
import { URL } from 'node:url'

const PORT = Number(process.env.PORT || 3418)
const UPSTREAM = 'https://okcheese.com'
const A = '00000000-0000-4000-8000-00000000000a'
const DEV = 'accept-demo-device'
const PID = 'de808b13-ffd2-4b8a-9d1d-fba7babe389f'
const DEV_USER = process.env.DEV_USER, DEV_PASS = process.env.DEV_PASS

const DEFAULTS = () => ({ carried: 'none', machine: 'pdf' })
let cfg = DEFAULTS(), version = 1, log = []
const note = (s) => { log.unshift(`${new Date().toISOString().slice(11, 19)} UTC  ${s}`); log.length = Math.min(log.length, 200) }
const carriedNow = () => (cfg.carried === 'pdf' ? ['pdf'] : [])
const machineNow = () => (cfg.machine === 'pdf' ? [{ name: 'pdf', description: '把 PDF 里的文字和表格读出来（验收模拟，机器上的技能）', source_root: '/demo/skills/pdf', digest: 'x', state: 'seen', adopted: false }] : [])

function fakeRow(template) {
  return {
    ...template,
    id: A, handle: 'accept-a', seat_handle: 'accept-a', display_name: '测试队友A（假）', name_source: 'human',
    is_default: false, is_active: true, version, editing: null,
    configuration: { ...(template?.configuration ?? {}), body: '', skills: carriedNow() },
  }
}
const send = (res, status, obj) => { res.writeHead(status, { 'content-type': 'application/json; charset=utf-8' }); res.end(JSON.stringify(obj)) }
const ok = (res, data) => send(res, 200, { code: 200, data })
const fail = (res, status, message) => send(res, status, { message, error: { message } })
const readBody = (req) => new Promise((r) => { const c = []; req.on('data', (d) => c.push(d)); req.on('end', () => r(Buffer.concat(c))) })

function upstream(req, path, body, { buffer = false } = {}) {
  return new Promise((resolve, reject) => {
    const headers = { ...req.headers, host: 'okcheese.com' }
    delete headers['accept-encoding']; delete headers['content-length']; delete headers.origin; delete headers.referer; delete headers.cookie
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
  } catch (e) { fail(res, 502, `生产读取失败：${e.message}`) }
}
async function devLogin() {
  const body = Buffer.from(JSON.stringify({ username: DEV_USER, password: DEV_PASS }))
  const r = await upstream({ method: 'POST', headers: { 'content-type': 'application/json' } }, '/api/users/auth/login', body, { buffer: true })
  const j = JSON.parse(r.body.toString()); if (!j.data?.accessToken) throw new Error(`开发号登录失败 ${r.status}`)
  return j.data
}

function controlPage(version_) {
  const radio = (name, value, opts) => opts.map(([v, l]) => `<label><input type="radio" name="${name}" value="${v}"${cfg[name] === v ? ' checked' : ''}> ${l}</label>`).join('<br>')
  return `<!doctype html><meta charset="utf-8"><title>#5210 验收控制台</title>
<style>body{font:14px/1.6 system-ui,sans-serif;max-width:860px;margin:20px auto;padding:0 16px}fieldset{margin:10px 0}legend{font-weight:600}pre{background:#f5f5f5;padding:8px;max-height:260px;overflow:auto;white-space:pre-wrap}.v{display:inline-block;padding:2px 10px;border-radius:12px;background:${version_ === 'before' ? '#fde2e2' : '#dff5e3'}}</style>
<h2>#5210 队友技能名单验收控制台</h2>
<p>预览页现在跑的是：<span class="v">${version_ === 'before' ? '改前（main + #5195 + #5201，不含本修复）' : '改后（#5210 当前头）'}</span>
　切换：<a href="/__accept/use?v=before">改前</a> · <a href="/__accept/use?v=after">改后</a>（切完回预览页刷新）</p>
<p>名册最前的<b>测试队友A（假）</b>和那台<b>验收用机器（假）</b>由下面决定；真实队友照生产原样读。所有写请求都被代理拦下，不会写进生产。</p>
<form method="post" action="/api/__accept/config">
<fieldset><legend>测试队友A 带什么（它的技能名单 configuration.skills）</legend>${radio('carried', cfg.carried, [['none', '什么都没带 []'], ['pdf', '带上了机器上的 pdf ["pdf"]']])}</fieldset>
<fieldset><legend>验收用机器上扫出什么</legend>${radio('machine', cfg.machine, [['pdf', '一个 pdf'], ['none', '什么都没有']])}</fieldset>
<button>保存设置</button>　<a href="/api/__accept/reset">全部重置</a>　<a href="/" target="_blank">打开预览页（开发号）</a>
</form>
<p>改完设置后，在预览页里<b>关掉技能面板再打开</b>（或刷新页面），面板才会重读。</p>
<h3>拦下的写请求（新的在上）</h3><pre>${log.join('\n') || '（还没有）'}</pre>`
}

http.createServer(async (req, res) => {
  let path = req.url
  if (path.startsWith('/api/connector/')) path = path.slice(4)
  const m = req.method
  const body = m === 'GET' || m === 'HEAD' ? null : await readBody(req)
  try {
    if (path === '/api/__accept' || path === '/api/__accept/') { res.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' }); return res.end(controlPage(req.headers['x-accept-version'] || 'after')) }
    if (path === '/api/__accept/config' && m === 'POST') {
      const p = new URLSearchParams(body.toString()); cfg = { carried: p.get('carried') || 'none', machine: p.get('machine') || 'pdf' }; version += 1
      note(`控制台：A 带 ${JSON.stringify(carriedNow())}，机器上 ${JSON.stringify(machineNow().map((s) => s.name))}`)
      res.writeHead(303, { location: '/api/__accept' }); return res.end()
    }
    if (path === '/api/__accept/reset') { cfg = DEFAULTS(); version += 1; log = []; note('控制台：全部重置'); res.writeHead(303, { location: '/api/__accept' }); return res.end() }
    if (path.startsWith('/api/__accept/enter')) {
      const d = await devLogin()
      res.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' })
      return res.end(`<!doctype html><meta charset="utf-8"><title>进入 #5210 验收预览</title><p>正在用开发号进入验收预览…</p><script>
localStorage.setItem('accessToken', ${JSON.stringify(d.accessToken)});${d.user ? `localStorage.setItem('user', ${JSON.stringify(JSON.stringify(d.user))});` : ''}
location.replace('/projects/${PID}/settings/agents')</script>`)
    }
    let mm
    if (m === 'GET' && (mm = path.match(/^\/api\/projects\/([^/]+)\/agents(\?.*)?$/))) {
      const r = await upstream(req, path, null, { buffer: true })
      try {
        const j = JSON.parse(r.body.toString()); const list = j.data?.data
        if (Array.isArray(list)) { const tpl = list.find((x) => x.is_active) ?? list[0]; j.data.data = [fakeRow(tpl), ...list]; j.data.total = j.data.data.length }
        return send(res, r.status, j)
      } catch { res.writeHead(r.status, r.headers); return res.end(r.body) }
    }
    if ((mm = path.match(/^\/api\/projects\/[^/]+\/agents\/([^/]+)\/editing/)) && m !== 'GET') { note(`占用/释放 ${mm[1].slice(0, 8)} → 成功（模拟）`); return ok(res, { editing: null }) }
    if ((mm = path.match(/^\/api\/projects\/[^/]+\/agents\/([^/?]+)$/)) && m === 'PUT') {
      note(`拦下：保存队友 ${mm[1].slice(0, 8)} ${body.toString().slice(0, 120)}`)
      return fail(res, 403, '验收预览里的保存已被拦下，没有写进生产')
    }
    if (m === 'GET' && path.startsWith('/connector/skills/market')) return send(res, 200, { skills: [], plugins: [], plugin_total: 0, mcp: [], notes: [] })
    if (m === 'GET' && path.startsWith('/connector/my/devices') && !path.includes('/skills')) return send(res, 200, { devices: [{ device_id: DEV, name: '验收用机器（假）', online: true, project_ids: [], team_ids: [], screens: [] }] })
    if (m === 'GET' && path.startsWith(`/connector/my/devices/${DEV}/skills`)) return send(res, 200, { skills: machineNow() })
    if (m !== 'GET' && m !== 'HEAD' && m !== 'OPTIONS') {
      if (path.startsWith('/api/users/auth/refresh-token')) { const d = await devLogin(); return ok(res, { accessToken: d.accessToken, user: d.user }) }
      if (/^\/api\/users\/auth\/login/.test(path)) return pass(req, res, path, body)
      note(`拦下：${m} ${path.split('?')[0]}（没有转给生产）`)
      return ok(res, {})
    }
    return pass(req, res, path, body)
  } catch (e) { fail(res, 500, `验收代理出错：${e.message}`) }
}).listen(PORT, '127.0.0.1', () => console.log(`accept proxy on ${PORT}`))

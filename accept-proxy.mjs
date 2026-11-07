// #5195 验收代理：挡在本地前端和 okcheese.com 之间。
// 读：照转生产（登录用验收人自己的账号）。写：除登录/刷新令牌外一律在这里拦下，不到生产。
// 测试对象：名册里多出两位假队友、设备列表换成一台假机器；它们的占用、保存、扫描、安装
// 回包都由控制页（/api/__accept）决定：延迟几秒、成功还是失败。
import http from 'node:http'
import https from 'node:https'
import { URL } from 'node:url'

const PORT = Number(process.env.PORT || 3418)
const UPSTREAM = 'https://okcheese.com'
const A = '00000000-0000-4000-8000-00000000000a'
const B = '00000000-0000-4000-8000-00000000000b'
const DEV = 'accept-demo-device'
const sleep = (s) => new Promise((r) => setTimeout(r, s * 1000))

const DEFAULTS = () => ({
  hold: { target: 'A', outcome: '409', delay: 4, once: true },
  save: { delay: 0, outcome: 'ok' },
  scan: { delay: 0, outcome: 'ok', onceOnly: true },
  install: { delay: 4, outcome: 'ok' },
  catalogDropAfterFirst: false,
})
let cfg = DEFAULTS()
let state, log, catalogReads
function reset() {
  state = {
    [A]: { name: '测试队友A（假）', handle: 'accept-a', skills: ['documents'], version: 1 },
    [B]: { name: '测试队友B（假）', handle: 'accept-b', skills: ['lean'], version: 1 },
  }
  log = []
  catalogReads = 0
  cfg = DEFAULTS()
}
reset()
const who = (id) => state[id]?.name ?? `真实队友 ${id.slice(0, 8)}`
const note = (s) => { log.unshift(`${new Date().toISOString().slice(11, 19)} UTC  ${s}`); log.length = Math.min(log.length, 200) }

function fakeRow(id, template) {
  const s = state[id]
  return {
    ...template,
    id, handle: s.handle, seat_handle: s.handle, display_name: s.name, name_source: 'human',
    is_default: false, is_active: true, version: s.version, editing: null,
    configuration: { ...(template?.configuration ?? {}), body: '', skills: [...s.skills] },
  }
}
const send = (res, status, obj) => { res.writeHead(status, { 'content-type': 'application/json; charset=utf-8' }); res.end(JSON.stringify(obj)) }
const ok = (res, data) => send(res, 200, { code: 200, data })
const fail = (res, status, message) => send(res, status, { message, error: { message } })
const readBody = (req) => new Promise((r) => { const c = []; req.on('data', (d) => c.push(d)); req.on('end', () => r(Buffer.concat(c))) })

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
  } catch (e) { fail(res, 502, `生产读取失败：${e.message}`) }
}

function controlPage() {
  const sel = (name, value, opts) => `<select name="${name}">${opts.map(([v, l]) => `<option value="${v}"${String(value) === v ? ' selected' : ''}>${l}</option>`).join('')}</select>`
  const num = (name, v) => `<input name="${name}" type="number" min="0" max="30" step="0.5" value="${v}" style="width:5em"> 秒`
  return `<!doctype html><meta charset="utf-8"><title>#5195 验收控制台</title>
<style>body{font:14px/1.6 system-ui,sans-serif;max-width:860px;margin:24px auto;padding:0 16px}fieldset{margin:10px 0}legend{font-weight:600}pre{background:#f5f5f5;padding:8px;max-height:320px;overflow:auto}</style>
<h2>#5195 技能面板验收控制台</h2>
<p>这里定的是预览里<b>测试队友A/B 与假机器</b>的回包。改完点「保存设置」，再回到 项目设置 → AI 队友 操作。真实队友和其它所有写请求一律被拦下、不会写进生产。</p>
<form method="post" action="/api/__accept/config">
<fieldset><legend>占用（打开面板时的「正在编辑」租约）</legend>
对 ${sel('hold.target', cfg.hold.target, [['A', '测试队友A'], ['B', '测试队友B'], ['both', '两位都']])}
延迟 ${num('hold.delay', cfg.hold.delay)} 后回 ${sel('hold.outcome', cfg.hold.outcome, [['ok', '成功'], ['409', '409 有人正在编辑']])}
${sel('hold.once', cfg.hold.once, [['true', '只对下一次生效'], ['false', '每次都这样']])}</fieldset>
<fieldset><legend>保存技能名单（PUT 队友）</legend>
延迟 ${num('save.delay', cfg.save.delay)} 后 ${sel('save.outcome', cfg.save.outcome, [['ok', '按版本号校验后成功（版本不对回 409）'], ['409', '一律 409'], ['500', '一律 500']])}</fieldset>
<fieldset><legend>扫描假机器上的技能</legend>
延迟 ${num('scan.delay', cfg.scan.delay)} 后 ${sel('scan.outcome', cfg.scan.outcome, [['ok', '成功（空）'], ['500', '失败 500']])}
${sel('scan.onceOnly', cfg.scan.onceOnly, [['true', '只对下一次生效'], ['false', '每次都这样']])}<br>
${sel('catalogDropAfterFirst', cfg.catalogDropAfterFirst, [['false', '技能目录照生产原样'], ['true', '第 2 次起的目录去掉 wolfram（好看出池子错没错）']])}</fieldset>
<fieldset><legend>从网上装技能（技能市场卡片上的 +）</legend>
延迟 ${num('install.delay', cfg.install.delay)} 后 ${sel('install.outcome', cfg.install.outcome, [['ok', '装好点的那一颗'], ['500', '失败 500']])}</fieldset>
<button>保存设置</button> &nbsp; <a href="/api/__accept/reset">全部重置（队友名单、版本号、日志、设置）</a>
</form>
<h3>测试队友现在的状态（代理里存的「真值」）</h3>
<pre>${Object.entries(state).map(([id, s]) => `${s.name}  版本 ${s.version}  技能 ${JSON.stringify(s.skills)}`).join('\n')}</pre>
<h3>拦下的写请求与模拟回包（新的在上）</h3><pre>${log.join('\n') || '（还没有）'}</pre>`
}

function parseForm(buf) {
  const p = new URLSearchParams(buf.toString())
  const next = DEFAULTS()
  for (const [k, v] of p) {
    const [a, b] = k.split('.'); const val = v === 'true' ? true : v === 'false' ? false : b === 'delay' ? Number(v) : v
    if (b) next[a][b] = val; else next[a] = val
  }
  return next
}

http.createServer(async (req, res) => {
  let path = req.url
  if (path.startsWith('/api/connector/')) path = path.slice(4) // vite 的 /connector 反代会多带一层 /api
  const m = req.method
  const body = m === 'GET' || m === 'HEAD' ? null : await readBody(req)
  try {
    // 控制台
    if (path === '/api/__accept' || path === '/api/__accept/') { res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' }); return res.end(controlPage()) }
    if (path === '/api/__accept/config' && m === 'POST') { cfg = parseForm(body); note(`控制台：设置已更新 ${JSON.stringify(cfg)}`); res.writeHead(303, { location: '/api/__accept' }); return res.end() }
    if (path === '/api/__accept/reset') { reset(); note('控制台：全部重置'); res.writeHead(303, { location: '/api/__accept' }); return res.end() }

    // 名册：生产那份后面接上两位测试队友
    let mm
    if (m === 'GET' && (mm = path.match(/^\/api\/projects\/([^/]+)\/agents(\?.*)?$/))) {
      const r = await upstream(req, path, null, { buffer: true })
      try {
        const j = JSON.parse(r.body.toString())
        const list = j.data?.data
        if (Array.isArray(list)) { const tpl = list.find((x) => x.is_active) ?? list[0]; j.data.data = [...list, fakeRow(A, tpl), fakeRow(B, tpl)]; j.data.total = j.data.data.length }
        return send(res, r.status, j)
      } catch { res.writeHead(r.status, r.headers); return res.end(r.body) }
    }
    // 占用 / 释放
    if ((mm = path.match(/^\/api\/projects\/[^/]+\/agents\/([^/]+)\/editing/)) && m !== 'GET') {
      const id = mm[1]; const held = /true/.test(body?.toString() ?? '')
      if (!held) { note(`释放占用 ${who(id)} → 成功`); return ok(res, { editing: null }) }
      const h = cfg.hold
      const hit = state[id] && (h.target === 'both' || (h.target === 'A' && id === A) || (h.target === 'B' && id === B))
      if (!hit) { note(`占用 ${who(id)} → 立即成功`); return ok(res, { editing: null }) }
      if (h.once) cfg.hold = { ...h, outcome: 'ok', delay: 0 }
      note(`占用 ${who(id)} → 等 ${h.delay} 秒后${h.outcome === '409' ? '回 409' : '成功'}（模拟）`)
      await sleep(h.delay)
      note(`占用 ${who(id)} 的回包现在送达：${h.outcome === '409' ? '409' : '成功'}`)
      return h.outcome === '409' ? fail(res, 409, '有人正在编辑这位队友（验收模拟）') : ok(res, { editing: null })
    }
    // 保存队友
    if ((mm = path.match(/^\/api\/projects\/[^/]+\/agents\/([^/?]+)$/)) && m === 'PUT') {
      const id = mm[1]; const payload = JSON.parse(body?.toString() || '{}'); const skills = payload.configuration?.skills
      if (!state[id]) { note(`拦下：对真实队友 ${who(id)} 的保存（预览里只能改测试队友）`); return fail(res, 403, '验收预览里只能改测试队友A/B，真实队友的写入已被拦下') }
      const s = state[id]
      note(`保存 ${who(id)} 技能=${JSON.stringify(skills)} 带版本 ${payload.expected_version}，等 ${cfg.save.delay} 秒（模拟）`)
      await sleep(cfg.save.delay)
      if (cfg.save.outcome === '500') { note(`保存 ${who(id)} → 500（模拟）`); return fail(res, 500, '保存失败（验收模拟）') }
      if (cfg.save.outcome === '409' || payload.expected_version !== s.version) { note(`保存 ${who(id)} → 409（带的 ${payload.expected_version}，现在是 ${s.version}）`); return fail(res, 409, '别人刚改过这个队友（验收模拟）') }
      s.skills = [...skills]; s.version += 1
      note(`保存 ${who(id)} → 成功，版本 ${s.version}`)
      return ok(res, fakeRow(id, { configuration: payload.configuration }))
    }
    // 技能市场：生产那份依赖 GitHub、常被限流成空，验收时换成两张固定卡片
    if (m === 'GET' && path.startsWith('/connector/skills/market')) {
      const card = (name, description) => ({ name, description, url: `https://example.invalid/accept/${name}`, source: 'anthropic', kind: 'skill' })
      return send(res, 200, { skills: [card('pdf', '读 PDF（验收模拟卡片）'), card('xlsx', '读写表格（验收模拟卡片）')], plugins: [], plugin_total: 0, mcp: [], notes: [] })
    }
    // 假机器
    if (m === 'GET' && path.startsWith('/connector/my/devices') && !path.includes('/skills')) {
      return send(res, 200, { devices: [{ device_id: DEV, name: '验收用机器（假）', online: true, project_ids: [], team_ids: [], screens: [] }] })
    }
    if (m === 'GET' && path.startsWith(`/connector/my/devices/${DEV}/skills`)) {
      const sc = cfg.scan
      if (sc.onceOnly) cfg.scan = { ...sc, outcome: 'ok', delay: 0 }
      note(`扫描假机器 → 等 ${sc.delay} 秒后${sc.outcome === '500' ? '失败' : '成功'}（模拟）`)
      await sleep(sc.delay)
      note(`扫描假机器的回包现在送达：${sc.outcome === '500' ? '500' : '成功'}`)
      return sc.outcome === '500' ? fail(res, 500, '机器离线（验收模拟）') : send(res, 200, { skills: [] })
    }
    if (m === 'POST' && path.startsWith(`/connector/my/devices/${DEV}/skills/web`)) {
      let skill = 'pdf'; try { skill = (JSON.parse(body.toString()).url || '').split('/').pop() || 'pdf' } catch {}
      note(`网上装技能 ${skill} → 等 ${cfg.install.delay} 秒（模拟）`)
      await sleep(cfg.install.delay)
      note(`网上装技能 ${skill} 的回包现在送达：${cfg.install.outcome === '500' ? '失败' : '装好了'}`)
      if (cfg.install.outcome === '500') return fail(res, 500, '安装失败（验收模拟）')
      return send(res, 200, { landed: true, plan: [{ name: skill, description: `${skill}（验收模拟）`, files: ['SKILL.md'] }],
        skills: [{ name: skill, description: `${skill}（验收模拟）`, source_root: `/demo/web-skills/${skill}`, digest: 'x', state: 'seen', adopted: false }], problems: [], roots: [], workspaces: [] })
    }
    // 技能目录（可选：第 2 次起去掉 wolfram）
    if (m === 'GET' && /\/skill-catalog(\?|$)/.test(path) && cfg.catalogDropAfterFirst) {
      const r = await upstream(req, path, null, { buffer: true }); catalogReads += 1
      const j = JSON.parse(r.body.toString())
      if (catalogReads > 1 && Array.isArray(j.data?.data)) { j.data.data = j.data.data.filter((x) => x.name !== 'wolfram'); note(`技能目录第 ${catalogReads} 次读：去掉了 wolfram（模拟）`) }
      return send(res, r.status, j)
    }
    // 其余写请求：登录和令牌刷新照转，别的一律拦下
    if (m !== 'GET' && m !== 'HEAD' && m !== 'OPTIONS') {
      if (/^\/api\/users\/auth\/(login|refresh|logout)/.test(path)) return pass(req, res, path, body)
      note(`拦下：${m} ${path.split('?')[0]}（没有转给生产）`)
      return ok(res, {})
    }
    return pass(req, res, path, body)
  } catch (e) { fail(res, 500, `验收代理出错：${e.message}`) }
}).listen(PORT, '127.0.0.1', () => console.log(`accept proxy on ${PORT}`))

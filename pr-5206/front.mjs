// FB-108 验收入口（只在演示机上，不进 PR）。
//   /            以 alice 登录后进「待办」（预览里是 iframe，导航也认 iframe）
//   /__fb108     控制台：按钮用 fetch 调 GET /__fb108/api/run，回包在同一页显示，不跳页
//   其余         照转本地前端（含 /api 与热更新长连接）；续期令牌在这里就地重新登录（预览 iframe 里刷新 cookie 可能被拦）
import http from 'node:http'
import net from 'node:net'
import { execFile } from 'node:child_process'
const VITE = { host: '127.0.0.1', port: 13100 }
const API = 'http://127.0.0.1:18181'
const BACKENDS = { before: 'http://127.0.0.1:18281', after: 'http://127.0.0.1:18381' }
const LABEL = { before: '改前（主干 37767844d）', after: '修后（本 PR a5118f39）' }
let runs = []; let busy = null; let flows = []; let flowState = {}
import { readFileSync, existsSync } from 'node:fs'
const require_state = () => existsSync('/tmp/fb108-demo/flow-state.json') ? readFileSync('/tmp/fb108-demo/flow-state.json', 'utf8') : '{}'
const say = (...a) => console.log(new Date().toISOString(), ...a)
const forward = (req, res, path = req.url) => {
  const p = http.request({ ...VITE, path, method: req.method, headers: req.headers }, (r) => { res.writeHead(r.statusCode, r.headers); r.pipe(res) })
  p.on('error', (e) => { res.writeHead(502); res.end(String(e)) }); req.pipe(p)
}
async function login(user = 'alice') {
  const r = await fetch(API + '/users/auth/login', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ username: user === 'bobby' ? 'bobby' : 'alice', password: 'demo12345' }) })
  return await r.json()
}
const json = (res, code, body) => { res.writeHead(code, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' }); res.end(JSON.stringify(body)) }
const html = (res, body) => { res.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' }); res.end(body) }
const ENTER = `<!doctype html><meta charset="utf-8"><title>进入 FB-108 验收预览</title><p style="font:15px system-ui;margin:24px">正在以 alice 进入「待办」…</p><script>
fetch('/__fb108/api/login'+location.search).then(r=>r.json()).then(j=>{localStorage.setItem('accessToken',j.data.accessToken);localStorage.setItem('user',JSON.stringify(j.data.user));localStorage.setItem('fb108-user',j.data.user.username);document.cookie='fb108-user='+j.data.user.username+';path=/'+(location.protocol==='https:'?';SameSite=None;Secure':'');location.replace(new URLSearchParams(location.search).get('to')||'/inbox')}).catch(e=>{document.body.textContent='进入失败：'+e})</script>`
const CONSOLE = `<!doctype html><meta charset="utf-8"><title>FB-108 验收控制台</title>
<style>body{font:15px/1.6 system-ui,sans-serif;max-width:900px;margin:20px auto;padding:0 16px}button{padding:8px 14px;margin-right:8px;cursor:pointer}button:disabled{opacity:.5}pre{background:#f6f6f6;padding:10px;white-space:pre-wrap;margin:6px 0 14px}.run{border-left:4px solid #999;padding-left:10px;margin:10px 0}.run.before{border-color:#c0392b}.run.after{border-color:#27ae60}#status{color:#b26a00}</style>
<h2>FB-108 递卡并发 · 验收控制台</h2>
<p>每按一次：新建一条任务，对它<b>同时递两次验收卡</b>（第二次比第一次晚 1 秒发出，直接打后端接口）。模拟条件：后端读分支差异（forge）人为延迟下面的秒数，返回一份固定差异；本机没有接 forge。</p>
<p>改前 = 主干 37767844d 的后端；修后 = 本 PR a5118f39 的后端。两个后端连同一个隔离测试库，产品页看到的是同一份数据。</p>
<h3>分步：读卡、换交付物、退回重交（都打修后后端 a5118f39；forge 替身延迟 0 秒）</h3>
<p>验收人是种子用户 bobby。<a href="/?as=bobby" target="_blank">以 bobby 打开产品页</a> · <a href="/" target="_blank">以 alice 打开</a>。按顺序点：</p>
<p><b>A 读卡后：</b><button data-step="a1">A1 递第一张卡</button><button data-step="a2">A2 bobby 读完后看状态</button><button data-step="a3">A3 交付更新后再递一张</button><button data-step="a4">A4 换交付物更新原卡</button></p>
<p id="a2tip"></p>
<p><b>B 退回后：</b><button data-step="b1">B1 递第一张卡</button><button data-step="b2">B2 bobby 退回</button><button data-step="b3">B3 再递第 2 版</button> &nbsp; <button data-step="reset">清空分步记录</button></p>
<h3>分步记录（新的在上）</h3><div id="flows">（还没有）</div>
<h3>同时递两次卡</h3>
<p>延迟 <input id="delay" value="5" size="3"> 秒 &nbsp;
<button data-which="before">改前代码：同时递两次卡</button><button data-which="after">修后代码：同时递两次卡</button></p>
<p id="status"></p>
<p><a href="/">打开产品页（alice，待办）</a>：改前那条任务显示「第 2 版」，修后那条「第 1 版」。看完点浏览器返回，或把地址改回 /__fb108 回到这里。</p>
<h3>运行记录（新的在上）</h3><div id="runs">（还没有）</div>
<script>
const $=(s)=>document.querySelector(s)
function esc(s){return String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'})[c])}
async function refresh(){
  const j=await (await fetch('/__fb108/api/state',{cache:'no-store'})).json()
  $('#status').textContent=j.busy?('正在跑：'+j.busy+'（约 10–20 秒）…'):''
  document.querySelectorAll('button').forEach(b=>b.disabled=!!j.busy)
  $('#flows').innerHTML=j.flows.length?j.flows.map(f=>'<div class="run after"><b>'+esc(f.step.toUpperCase())+'</b><pre>'+esc(f.out)+'</pre></div>').join(''):'（还没有）'
  const P='/projects/4eb748cb-c2f4-4423-a793-74dfded56910/topics/ae1a7cc2-e0eb-4962-b9da-02b45d76aa2d/tasks/'
  $('#a2tip').innerHTML=j.flowState&&j.flowState.a?'A2 之前：<a target="_blank" href="/?as=bobby&to='+encodeURIComponent(P+j.flowState.a)+'">以 bobby 打开 A 任务读卡</a>（点右侧「改动」看交出去的链接）'+(j.flowState.b?' · <a target="_blank" href="/?as=bobby&to='+encodeURIComponent(P+j.flowState.b)+'">以 bobby 打开 B 任务</a>':''):''
  $('#runs').innerHTML=j.runs.length?j.runs.map(r=>'<div class="run '+r.which+'"><b>'+esc(r.label)+'</b> · 延迟 '+r.delay+' 秒 · '+esc(r.at)+'<pre>'+esc(r.out)+'</pre></div>').join(''):'（还没有）'
}
document.querySelectorAll('button[data-step]').forEach(b=>b.onclick=async()=>{
  const r=await fetch('/__fb108/api/flow?step='+b.dataset.step,{cache:'no-store'})
  if(!r.ok){$('#status').textContent='没跑起来：'+(await r.text())}
  refresh()
})
document.querySelectorAll('button[data-which]').forEach(b=>b.onclick=async()=>{
  const r=await fetch('/__fb108/api/run?which='+b.dataset.which+'&delay='+encodeURIComponent($('#delay').value),{cache:'no-store'})
  if(!r.ok){$('#status').textContent='没跑起来：'+(await r.text())}
  refresh()
})
refresh(); setInterval(refresh,1500)
</script>`
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://x')
  const dest = req.headers['sec-fetch-dest']
  const navigation = dest ? dest === 'document' || dest === 'iframe' : String(req.headers.accept || '').startsWith('text/html')
  try {
    if (url.pathname === '/__fb108/api/login') return json(res, 200, await login(url.searchParams.get('as') || 'alice'))
    if (url.pathname === '/__fb108/api/flow') {
      const step = url.searchParams.get('step') || ''
      if (!/^(a[1-4]|b[1-3]|reset)$/.test(step)) return json(res, 400, { message: 'bad step' })
      if (busy) return json(res, 409, { message: '上一次还在跑' })
      busy = 'flow'; say('flow', step)
      execFile('python3', ['-I', '/tmp/fb108-demo/flows.py', step], { timeout: 60000 }, (err, out, errout) => {
        busy = null
        if (step === 'reset') flows = []
        else flows.unshift({ step, out: out + (err ? '\n出错：' + errout : '') })
        try { flowState = JSON.parse(require_state()) } catch { flowState = {} }
      })
      return json(res, 202, { started: step })
    }
    if (url.pathname === '/__fb108/api/state') return json(res, 200, { busy: busy && (LABEL[busy] || '分步流程'), runs, flows, flowState })
    if (url.pathname === '/__fb108/api/run') {
      const which = url.searchParams.get('which') === 'after' ? 'after' : 'before'
      const delay = String(Math.min(30, Math.max(1, Number(url.searchParams.get('delay')) || 5)))
      if (busy) return json(res, 409, { message: '上一次还在跑' })
      busy = which; say('run', which, delay)
      const title = (which === 'after' ? '修后' : '改前') + '：同时递两次卡 ' + new Date(Date.now() + 8 * 3600e3).toISOString().slice(11, 19)
      execFile('python3', ['-I', '/tmp/fb108-demo/race.py', title, delay, BACKENDS[which]], { timeout: 120000 }, (err, out, errout) => {
        runs.unshift({ which, label: LABEL[which], delay, at: new Date(Date.now() + 8 * 3600e3).toISOString().slice(0, 19).replace('T', ' ') + '（北京时间）', out: out + (err ? '\n出错：' + errout : '') })
        say('done', which, err ? 'error' : 'ok'); busy = null
      })
      return json(res, 202, { started: which })
    }
    if (url.pathname === '/__fb108' || url.pathname === '/__fb108/') return html(res, CONSOLE)
    if (req.method === 'POST' && url.pathname === '/api/users/auth/refresh-token') { const who = /fb108-user=bobby/.test(req.headers.cookie || '') ? 'bobby' : 'alice'; say('refresh -> login', who); return json(res, 200, await login(who)) }
    if (req.method === 'GET' && url.pathname === '/' && navigation) { say('enter', dest); return html(res, ENTER) }
  } catch (e) { say('error', String(e)); return json(res, 500, { message: String(e) }) }
  forward(req, res)
})
server.on('upgrade', (req, socket, head) => {
  const up = net.connect(VITE.port, VITE.host, () => {
    up.write(`${req.method} ${req.url} HTTP/1.1\r\n` + Object.entries(req.headers).map(([k, v]) => `${k}: ${v}`).join('\r\n') + '\r\n\r\n'); up.write(head); socket.pipe(up); up.pipe(socket)
  })
  up.on('error', () => socket.destroy()); socket.on('error', () => up.destroy())
})
server.listen(13200, '127.0.0.1', () => say('fb108 front on 13200'))

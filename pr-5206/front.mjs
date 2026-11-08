// FB-108 验收入口：根地址以 alice 登录直达「待办」；/__fb108 是控制页，按钮触发「同一条任务同时递两次卡」；其余照转本地前端。
import http from 'node:http'
import net from 'node:net'
import { execFile } from 'node:child_process'
const VITE = { host: '127.0.0.1', port: 13100 }
const BACKENDS = { before: 'http://127.0.0.1:18281', after: 'http://127.0.0.1:18381' }
let log = []; let busy = false
const forward = (req, res, path = req.url) => {
  const p = http.request({ ...VITE, path, method: req.method, headers: req.headers }, (r) => { res.writeHead(r.statusCode, r.headers); r.pipe(res) })
  p.on('error', (e) => { res.writeHead(502); res.end(String(e)) }); req.pipe(p)
}
const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[c])
const page = () => `<!doctype html><meta charset="utf-8"><title>FB-108 验收控制台</title>
<style>body{font:15px/1.6 system-ui,sans-serif;max-width:860px;margin:24px auto;padding:0 16px}button{padding:8px 14px;margin-right:8px}pre{background:#f6f6f6;padding:10px;white-space:pre-wrap}</style>
<h2>FB-108 递卡并发 · 验收控制台</h2>
<p>每按一次：新建一条任务，对它<b>同时递两次验收卡</b>（第二次比第一次晚 1 秒发出）。模拟条件：后端读分支差异（forge）人为延迟下面填的秒数，返回一份固定差异；本机没有接 forge。</p>
<p>改前 = 主干 37767844d 的后端；修后 = 本任务分支的后端。两个后端连同一个隔离测试库，页面里看到的是同一份数据。</p>
<form method="post" action="/__fb108/run">延迟 <input name="delay" value="5" size="3"> 秒 &nbsp;
<button name="which" value="before">改前代码：同时递两次卡</button><button name="which" value="after">修后代码：同时递两次卡</button></form>
<p><a href="/" target="_blank">打开产品页（alice，待办）</a> —— 改前那条显示「第 2 版」，修后那条「第 1 版」。</p>
<h3>运行记录（新的在上）${busy ? ' · 正在跑…（约 15 秒，刷新看结果）' : ''}</h3><pre>${esc(log.join('\n\n')) || '（还没有）'}</pre>`
const server = http.createServer((req, res) => {
  const dest = req.headers['sec-fetch-dest']
  const nav = dest ? dest === 'document' : String(req.headers.accept || '').startsWith('text/html')
  if (req.url.startsWith('/__fb108/run') && req.method === 'POST') {
    let body = ''; req.on('data', (d) => (body += d)); req.on('end', () => {
      const q = new URLSearchParams(body); const which = q.get('which') === 'after' ? 'after' : 'before'
      const delay = String(Math.min(30, Math.max(1, Number(q.get('delay')) || 5)))
      if (!busy) {
        busy = true
        const title = (which === 'after' ? '修后' : '改前') + '：同时递两次卡 ' + new Date().toISOString().slice(11, 19)
        execFile('python3', ['-I', '/tmp/fb108-demo/race.py', title, delay, BACKENDS[which]], { timeout: 120000 }, (err, out, errout) => {
          log.unshift(`[${new Date().toISOString()}] ${which === 'after' ? '修后' : '改前'}，延迟 ${delay}s\n${out}${err ? '\n出错：' + errout : ''}`); busy = false
        })
      }
      res.writeHead(303, { location: '/__fb108' }); res.end()
    }); return
  }
  if (req.url.startsWith('/__fb108')) { res.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' }); return res.end(page()) }
  if (req.method === 'GET' && (req.url === '/' || req.url.startsWith('/?')) && nav) {
    res.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' })
    return res.end(`<!doctype html><meta charset="utf-8"><p>正在以 alice 进入…</p><script>
fetch('/api/users/auth/login',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({username:'alice',password:'demo12345'})}).then(r=>r.json()).then(j=>{localStorage.setItem('accessToken',j.data.accessToken);localStorage.setItem('user',JSON.stringify(j.data.user));location.replace('/inbox')})</script>`)
  }
  forward(req, res)
})
server.on('upgrade', (req, socket, head) => {
  const up = net.connect(VITE.port, VITE.host, () => {
    up.write(`${req.method} ${req.url} HTTP/1.1\r\n` + Object.entries(req.headers).map(([k, v]) => `${k}: ${v}`).join('\r\n') + '\r\n\r\n'); up.write(head); socket.pipe(up); up.pipe(socket)
  })
  up.on('error', () => socket.destroy()); socket.on('error', () => up.destroy())
})
server.listen(13200, '127.0.0.1', () => console.log('fb108 front on 13200'))

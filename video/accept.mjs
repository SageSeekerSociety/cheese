// 验收入口（只在本机隔离栈前面套一层，不改产品代码）：
//   /            → 自动用种子用户 alice 登录、进演示项目 General 频道
//   /__accept?ws=off|on  → 同上，并切换实时连接（默认 off：只看首屏 /blocks）
// 其余请求原样转给 vite(13471)；WebSocket 在 off 时被拒（vite 自己的热更新照放行）。
import http from 'node:http';
import net from 'node:net';
import crypto from 'node:crypto';
const UP = { host: '127.0.0.1', port: Number(process.env.UP_PORT || 13471) };
const PORT = Number(process.env.ACCEPT_PORT || 13480);
const PID = 'ef363abd-5a65-4531-b715-1ec5b98dd79f', ROOM = 'a85f44e4-fd6a-49b6-a419-eb97d3274325', WORKING = '038aaa37-8862-4a76-82e9-b3c78e44383f';
const VERSION = process.env.VERSION_LABEL || '改后：任务分支 task/db494696 @ 9dcdcda5（PR #5214 当前 head；后端与前端都是这一版）';
const wsOff = (req) => !/(?:^|;\s*)accept_ws=on/.test(req.headers.cookie || '');

const entry = (ws) => `<!doctype html><meta charset="utf-8"><title>PR #5214 验收入口</title>
<body style="font:16px system-ui;padding:24px">正在以种子用户 alice 进入演示频道（实时连接：${ws}）…<pre id=log></pre>
<script>
(async () => {
  const log = (s) => (document.getElementById('log').textContent += s + '\\n');
  try {
    const r = await fetch('/api/users/auth/login', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ username: 'alice', password: 'demo12345' }) });
    const j = await r.json();
    if (!r.ok) throw new Error('login ' + r.status);
    localStorage.setItem('accessToken', j.data.accessToken);
    localStorage.setItem('user', JSON.stringify(j.data.user));
    localStorage.setItem('cheese.tour.done', '1');
    localStorage.setItem('cheese.betaNotice.alice', 'seen');
    const auth = { authorization: 'Bearer ' + j.data.accessToken, 'content-type': 'application/json' };
    const c = await (await fetch('/api/users/me/consents', { headers: auth })).json();
    const docs = Object.fromEntries((c.data?.pending || []).map((d) => [d.document, d.version]));
    if (Object.keys(docs).length) await fetch('/api/users/me/consents', { method: 'POST', headers: auth, body: JSON.stringify({ documents: docs }) });
    location.replace('/projects/${PID}/topics/${ROOM}');
  } catch (e) { log('进入失败：' + e.message); }
})();
</script>`;

const banner = (ws) => `<div id="accept-banner" style="position:fixed;left:50%;bottom:8px;transform:translateX(-50%);z-index:2147483647;background:#fff8e1;border:1px solid #c9a227;border-radius:8px;padding:6px 12px;font:12px/1.5 system-ui;color:#333;box-shadow:0 2px 8px rgba(0,0,0,.15);max-width:860px">
<b>PR #5214 验收入口 · ${process.env.SIDE || '改后'}</b>（本机隔离种子库，产品代码未改）· 版本：${VERSION}<br>
模拟条件：只给「首屏标记·会话在跑（模拟）」宣告了队友会话 RUNNING（session_state.declare，未接真模型）；「首屏标记·没有会话」未动。<br>
实时连接：<b>${ws === 'off' ? '已关（只看首屏 /blocks）' : '已开（订阅快照会重画）'}</b> ·
<a href="/__accept?ws=off">关掉实时连接重进</a> · <a href="/__accept?ws=on">打开实时连接重进</a></div>`;

const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://x');
  if (url.pathname === '/' || url.pathname === '/__accept') {
    const ws = url.searchParams.get('ws') === 'on' ? 'on' : (url.pathname === '/' ? (wsOff(req) ? 'off' : 'on') : 'off');
    res.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'set-cookie': `accept_ws=${ws}; Path=/; SameSite=Lax`, 'cache-control': 'no-store' });
    return res.end(entry(ws === 'on' ? '开' : '关'));
  }
  const headers = { ...req.headers, host: `${UP.host}:${UP.port}` };
  if (headers.origin) headers.origin = `http://${UP.host}:${UP.port}`;
  delete headers['accept-encoding'];
  const up = http.request({ ...UP, method: req.method, path: req.url, headers }, (ur) => {
    const type = ur.headers['content-type'] || '';
    if (!type.includes('text/html')) { res.writeHead(ur.statusCode, ur.headers); return ur.pipe(res); }
    const chunks = [];
    ur.on('data', (c) => chunks.push(c));
    ur.on('end', () => {
      const html = Buffer.concat(chunks).toString('utf8').replace('</body>', banner(wsOff(req) ? 'off' : 'on') + '</body>');
      const h = { ...ur.headers }; delete h['content-length'];
      res.writeHead(ur.statusCode, h); res.end(html);
    });
  });
  up.on('error', (e) => { res.writeHead(502); res.end(String(e)); });
  req.pipe(up);
});
server.on('upgrade', (req, socket, head) => {
  const hmr = /vite-hmr/.test(req.headers['sec-websocket-protocol'] || '');
  if (!hmr && wsOff(req)) {
    // 关掉实时连接：握手照接、之后一个字也不发（和截图时 Playwright 挡长连接的做法一样）。
    // 直接拒掉的话前端会反复重连、跳页，首屏反而看不稳。
    const accept = crypto.createHash('sha1').update(req.headers['sec-websocket-key'] + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest('base64');
    socket.write('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ' + accept + '\r\n\r\n');
    socket.on('data', () => {}); socket.on('error', () => {});
    return;
  }
  const up = net.connect(UP.port, UP.host, () => {
    up.write(`${req.method} ${req.url} HTTP/1.1\r\n` + Object.entries({ ...req.headers, host: `${UP.host}:${UP.port}`, ...(req.headers.origin ? { origin: `http://${UP.host}:${UP.port}` } : {}) }).map(([k, v]) => `${k}: ${v}`).join('\r\n') + '\r\n\r\n');
    up.write(head); socket.pipe(up).pipe(socket);
  });
  up.on('error', () => socket.destroy()); socket.on('error', () => up.destroy());
});
server.listen(PORT, '127.0.0.1', () => console.log('accept entry on', PORT));

// 验收控制面板（只在本地测试栈的预览里注入）。换人走产品自己的退出/登录：
// 和账号菜单「退出登录」、登录页「登录」调的是同一组函数，换人时产品自己的清理照常触发。
// 必须拿到和应用同一份模块：vite 热更新过的文件带 ?t=，地址不同就是另一份实例。
async function sameModule(path) {
  for (let i = 0; i < 100; i++) {
    const hit = performance.getEntriesByType('resource').map((e) => new URL(e.name)).find((u) => u.pathname === path)
    if (hit) return import(hit.pathname + hit.search)
    await new Promise((r) => setTimeout(r, 100))
  }
  return import(path)
}
const { UserAuthApi } = await sameModule('/src/api/userAuth.ts')
const { default: AccountService } = await sameModule('/src/services/account.ts')
const { default: router } = await sameModule('/src/router/index.ts')

const PASS = 'demo12345'
const me = () => (AccountService.loggedIn ? AccountService.user?.username : null)

async function signIn(username) {
  const data = await UserAuthApi.login({ username, password: PASS })
  AccountService.login(data.accessToken, data.user)        // 同登录页 signedIn()
  await router.replace('/projects/0v5lb0qt/overview')   // 种子里的「知是 2.0 融合演示」，两人都在
}
async function signOut() {
  try { await UserAuthApi.logout() } catch {}               // 同账号菜单 onLogout()
  await AccountService.logout()
  await router.push('/')
}
async function switchTo(username) {
  if (me() === username) return
  if (me()) await signOut()
  await signIn(username)
}

const box = document.createElement('div')
box.id = 'accept-panel'
box.innerHTML = `
<style>
#accept-panel{position:fixed;left:84px;bottom:12px;z-index:99999;width:400px;max-height:70vh;overflow:auto;background:#fff;border:2px solid #f5821f;border-radius:10px;box-shadow:0 6px 24px rgba(0,0,0,.18);font:13px/1.5 system-ui,sans-serif;color:#222}
#accept-panel header{display:flex;justify-content:space-between;align-items:center;padding:8px 12px;background:#fff4ea;border-bottom:1px solid #f3d3b4;font-weight:600}
#accept-panel section{padding:8px 12px;border-bottom:1px solid #eee}
#accept-panel button{margin:2px 4px 2px 0;padding:4px 10px;border:1px solid #ccc;border-radius:6px;background:#fafafa;cursor:pointer;font:inherit}
#accept-panel button.on{background:#f5821f;color:#fff;border-color:#f5821f}
#accept-panel .muted{color:#777}
#accept-panel ul{margin:2px 0 0 0;padding-left:18px}
#accept-panel .warn{color:#c0392b;font-weight:600}
#accept-panel code{background:#f3f3f3;padding:0 3px;border-radius:3px}
</style>
<header><span>PR #5194 验收控制</span><button data-act="fold">收起</button></header>
<div class="body">
<section>当前登录：<b data-id="me">…</b><br>
<button data-act="alice">切到 alice</button><button data-act="bobby">切到 bobby</button><span class="muted">同一页面，不刷新</span></section>
<section>alice 查资料库的请求：<b data-id="hold">…</b><br>
<button data-act="holdOn">扣住</button><button data-act="holdOff">放行</button><span class="muted" data-id="waiting"></span></section>
<section><b>上传请求</b>（团队 4 = alice，团队 5 = bobby）<ul data-id="uploads"></ul></section>
<section><b>两人资料库</b> <button data-act="clear">清空两人资料库</button>
<div data-id="libs"></div></section>
<section class="muted">页面加载次数：<span data-id="loads"></span>（不变就说明全程没刷新）</section>
</div>`
document.body.appendChild(box)
const $ = (k) => box.querySelector(`[data-id="${k}"]`)
const loads = Number(sessionStorage.getItem('accept-loads') || 0) + 1
sessionStorage.setItem('accept-loads', String(loads))
$('loads').textContent = loads

box.addEventListener('click', async (e) => {
  const act = e.target.dataset?.act
  if (!act) return
  e.target.disabled = true
  try {
    if (act === 'alice' || act === 'bobby') await switchTo(act)
    if (act === 'holdOn' || act === 'holdOff') await fetch(`/__accept/hold?on=${act === 'holdOn' ? 1 : 0}`)
    if (act === 'clear' && confirm('删掉 alice、bobby 资料库里的全部文件，并清空上传记录？')) await fetch('/__accept/clear', { method: 'POST' })
    if (act === 'fold') { const b = box.querySelector('.body'); b.hidden = !b.hidden; e.target.textContent = b.hidden ? '展开' : '收起' }
  } catch (err) { alert(`没做成：${err?.message ?? err}`) }
  finally { e.target.disabled = false; refresh() }
})

const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[c])
async function refresh() {
  $('me').textContent = me() ?? '未登录'
  try {
    const s = await (await fetch('/__accept/state')).json()
    $('hold').textContent = s.holding ? '扣住中' : '正常'
    $('hold').className = s.holding ? 'warn' : ''
    $('waiting').textContent = s.waiting ? `已扣住 ${s.waiting} 个请求` : ''
    box.querySelector('[data-act="holdOn"]').classList.toggle('on', s.holding)
    const stage = (p) => (p.endsWith('/uploads') ? '开始上传' : p.endsWith('/part') ? '传分片' : '放进资料库')
    $('uploads').innerHTML = s.uploads.map((u) => {
      const team = u.path.match(/teams\/(\d+)/)[1]
      const mismatch = (team === '4' && u.as !== 'alice') || (team === '5' && u.as !== 'bobby')
      return `<li class="${mismatch ? 'warn' : ''}">${u.time} ${stage(u.path)} → 团队 ${team}，凭据 ${esc(u.as)}${u.status ? '，回 ' + u.status : ''}${mismatch ? '（串号）' : ''}</li>`
    }).join('') || '<li class="muted">还没有</li>'
    $('libs').innerHTML = s.libraries.error ? `<span class="warn">${esc(s.libraries.error)}</span>` : Object.entries(s.libraries).map(([u, l]) =>
      `<div><b>${u}</b>（团队 ${l.team}）：${l.files.length ? '<ul>' + l.files.map((f) => `<li><code>${esc(f.name)}</code> <span class="muted">由 ${esc(f.by)} 放入</span></li>`).join('') + '</ul>' : '<span class="muted">空</span>'}</div>`).join('')
  } catch {}
}
setInterval(refresh, 2000)
refresh()
// 打开就是 alice：没登录时先用产品自己的登录接口登上，再整页进演示项目一次（这一次是准备，不算验收步骤）。
if (!me() && !document.cookie.includes('cheese_signed_in')) {
  const data = await UserAuthApi.login({ username: 'alice', password: PASS })
  AccountService.login(data.accessToken, data.user)
  location.replace('/projects/0v5lb0qt/overview')
}

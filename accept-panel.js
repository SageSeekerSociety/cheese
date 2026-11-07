// #5195 验收浮动面板：注入产品页右下角，只用 fetch 调 /api/__accept/*，不导航、不刷新产品页。
(() => {
  if (window.__accept5195) return; window.__accept5195 = true
  const css = `#acc5195{position:fixed;right:16px;bottom:16px;z-index:2147483000;width:360px;max-height:70vh;display:flex;flex-direction:column;
  font:12px/1.5 system-ui,sans-serif;color:#222;background:#fff;border:1px solid #d9a441;border-radius:10px;box-shadow:0 6px 24px rgba(0,0,0,.18)}
  #acc5195 header{display:flex;align-items:center;gap:6px;padding:6px 10px;background:#fff4dc;border-radius:10px 10px 0 0;cursor:pointer;font-weight:600}
  #acc5195 header span{flex:1}#acc5195 .body{overflow:auto;padding:8px 10px}#acc5195.min .body{display:none}#acc5195.min{width:auto}
  #acc5195 button{font:inherit;padding:3px 8px;margin:2px 2px 2px 0;border:1px solid #bbb;border-radius:6px;background:#fafafa;cursor:pointer}
  #acc5195 button:hover{background:#f0f0f0}#acc5195 .q button{border-color:#d9a441;background:#fff8e6}
  #acc5195 .p{border:1px solid #f0c36d;background:#fffaf0;border-radius:6px;padding:4px 6px;margin:4px 0}
  #acc5195 h4{margin:8px 0 2px;font-size:12px}#acc5195 pre{margin:0;white-space:pre-wrap;background:#f6f6f6;border-radius:4px;padding:4px;max-height:140px;overflow:auto}
  #acc5195 .msg{color:#8a5a00;min-height:1em}`
  const st = document.createElement('style'); st.textContent = css; document.head.appendChild(st)
  const el = document.createElement('div'); el.id = 'acc5195'
  el.innerHTML = `<header title="点击折叠/展开"><span>#5195 验收面板（模拟回包）</span><b class="n"></b><small class="t">收起</small></header>
  <div class="body">
   <div class="msg"></div>
   <div><button data-act="reset">全部重置</button><button data-act="preset-scan">设：扫描手动放行一次 + 第 2 次起去掉 wolfram</button><button data-act="preset-save">设：保存手动放行</button></div>
   <div class="q"><button data-q="占用 测试队友A" data-as="fail">A 占用 → 放行失败(409)</button><button data-q="网上装技能" data-as="ok">安装 → 放行成功</button><button data-q="扫描假机器" data-as="fail">扫描 → 放行失败(500)</button><button data-q="保存 " data-as="ok">保存 → 放行成功</button></div>
   <h4>等放行</h4><div class="pend">（没有）</div>
   <h4>测试队友（代理里的真值）</h4><pre class="state"></pre>
   <h4>保存记录（版本号）</h4><pre class="saves">（还没有）</pre>
  </div>`
  document.body.appendChild(el)
  const $ = (s) => el.querySelector(s)
  const say = (t) => { $('.msg').textContent = t; clearTimeout(say.t); say.t = setTimeout(() => ($('.msg').textContent = ''), 4000) }
  el.querySelector('header').onclick = () => { el.classList.toggle('min'); $('.t').textContent = el.classList.contains('min') ? '展开' : '收起' }
  const form = (o) => fetch('/api/__accept/config', { method: 'POST', redirect: 'manual', headers: { 'content-type': 'application/x-www-form-urlencoded' }, body: new URLSearchParams(o).toString() })
  const base = { 'hold.target': 'A', 'hold.outcome': 'ok', 'hold.delay': '0', 'hold.once': 'false', 'save.outcome': 'ok', 'save.delay': '0', 'scan.outcome': 'ok', 'scan.delay': '0', 'scan.onceOnly': 'false', 'install.outcome': 'manual', 'install.delay': '0', catalogDropAfterFirst: 'false' }
  let last = { pending: [] }
  async function release(id, as) { await fetch(`/api/__accept/release?id=${id}&as=${as}`); tick() }
  el.addEventListener('click', async (e) => {
    const b = e.target.closest('button'); if (!b) return; e.stopPropagation()
    if (b.dataset.act === 'reset') { await fetch('/api/__accept/reset', { redirect: 'manual' }); say('已重置：占用 A 下一次手动放行、安装手动放行，测试队友名单回到初始。请点「AI 队友」卡片右上角的刷新按钮重读名册（不用刷新页面）。') }
    if (b.dataset.act === 'preset-scan') { await form({ ...base, 'hold.outcome': 'ok', 'scan.outcome': 'manual', 'scan.onceOnly': 'true', catalogDropAfterFirst: 'true' }); say('已设：下一次扫描手动放行，第 2 次起的目录去掉 wolfram。') }
    if (b.dataset.act === 'preset-save') { await form({ ...base, 'save.outcome': 'manual' }); say('已设：保存手动放行（成功时按版本号校验）。') }
    if (b.dataset.q) { last = await (await fetch('/api/__accept/state', { cache: 'no-store' })).json(); const p = last.pending.find((x) => x.what.startsWith(b.dataset.q)); if (!p) return say(`现在没有挂着的「${b.dataset.q.trim()}」`); await release(p.id, b.dataset.as); say(`已放行 #${p.id}：${b.dataset.as === 'ok' ? '成功' : '失败'}`) }
    if (b.dataset.id) { await release(b.dataset.id, b.dataset.as) }
    tick()
  })
  async function tick() {
    try {
      const j = await (await fetch('/api/__accept/state', { cache: 'no-store' })).json(); last = j
      $('.n').textContent = j.pending.length ? `（${j.pending.length} 条等放行）` : ''
      const box = $('.pend'); box.innerHTML = j.pending.length ? '' : '（没有）'
      for (const p of j.pending) { const d = document.createElement('div'); d.className = 'p'; d.textContent = `#${p.id} ${p.what}（已挂 ${p.age} 秒） `
        for (const [as, l] of [['ok', '放行·成功'], ['fail', '放行·失败']]) { const bt = document.createElement('button'); bt.textContent = l; bt.dataset.id = p.id; bt.dataset.as = as; d.appendChild(bt) }
        box.appendChild(d) }
      $('.state').textContent = j.state.join('\n')
      const saves = j.log.filter((l) => /保存 测试队友|保存 真实|拦下：对真实队友/.test(l)).reverse()
      $('.saves').textContent = saves.length ? saves.join('\n') : '（还没有）'
    } catch {}
  }
  tick(); setInterval(tick, 1000)
})()

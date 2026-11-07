// #5195 验收面板 + 浏览器端挂起。注入产品页 <head>，比应用自己的脚本先跑。
// 挂起在浏览器里：命中的请求先不发，把交给应用的 Promise 扣住；点「放行·成功」才真正发给代理（代理立即回），
// 点「放行·失败」就直接给应用一个 409/500 回包。不经过公网预览的 30 秒，也不碰应用自己的超时。
(() => {
  if (window.__accept5195) return; window.__accept5195 = true
  const A = '00000000-0000-4000-8000-00000000000a', B = '00000000-0000-4000-8000-00000000000b'
  const DEV = 'accept-demo-device'
  const realFetch = window.fetch.bind(window)
  // 每类：off 不挂、once 只挂下一次、always 每次都挂
  const DEFAULT_MODES = () => ({ holdA: 'once', scan: 'off', install: 'always', save: 'off' })
  const modes = DEFAULT_MODES()
  const pending = []; let seq = 0
  const KIND = {
    holdA: { label: '占用 测试队友A', fail: [409, { message: '有人正在编辑这位队友（验收模拟）', error: { message: '有人正在编辑这位队友（验收模拟）' } }], failText: '409 有人正在编辑' },
    scan: { label: '扫描假机器上的技能', fail: [500, { detail: '机器离线（验收模拟）', message: '机器离线（验收模拟）' }], failText: '500 机器离线' },
    install: { label: '网上装技能', fail: [500, { detail: '安装失败（验收模拟）', message: '安装失败（验收模拟）' }], failText: '500 安装失败' },
    save: { label: '保存测试队友', fail: [500, { message: '保存失败（验收模拟）', error: { message: '保存失败（验收模拟）' } }], failText: '500 保存失败' },
  }
  function classify(url, init) {
    const m = (init?.method || 'GET').toUpperCase(); const body = typeof init?.body === 'string' ? init.body : ''
    if (m !== 'GET' && url.includes(`/agents/${A}/editing`) && /true/.test(body)) return 'holdA'
    if (m === 'GET' && new RegExp(`/connector/my/devices/${DEV}/skills(\\?|$)`).test(url)) return 'scan'
    if (m === 'POST' && url.includes(`/connector/my/devices/${DEV}/skills/web`)) return 'install'
    if (m === 'PUT' && new RegExp(`/agents/(${A}|${B})(\\?|$)`).test(url)) return 'save'
    return null
  }
  const describe = (kind, url, init) => {
    let extra = ''
    try { const j = JSON.parse(init?.body || '{}'); if (kind === 'install') extra = ` ${(j.url || '').split('/').pop()}`; if (kind === 'save') extra = ` ${url.includes(A) ? 'A' : 'B'} 技能=${JSON.stringify(j.configuration?.skills)} 带版本 ${j.expected_version}` } catch {}
    return KIND[kind].label + extra
  }
  window.fetch = function (input, init) {
    const url = typeof input === 'string' ? input : input?.url || String(input)
    const kind = classify(url, init)
    if (!kind || modes[kind] === 'off') return realFetch(input, init)
    if (modes[kind] === 'once') modes[kind] = 'off'
    return new Promise((resolve, reject) => {
      pending.push({ id: ++seq, kind, what: describe(kind, url, init), since: Date.now(), go: (as) => {
        if (as === 'fail') { const [status, body] = KIND[kind].fail; resolve(new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })) }
        else realFetch(input, init).then(resolve, reject)
      } })
      render()
    })
  }
  function release(id, as) { const i = pending.findIndex((p) => p.id === id); if (i < 0) return false; const [p] = pending.splice(i, 1); p.go(as); log.unshift(`${new Date().toTimeString().slice(0, 8)} 放行 #${p.id} ${p.what} → ${as === 'fail' ? KIND[p.kind].failText : '成功（真正发出）'}`); render(); return true }
  const log = []

  // ---- 面板 ----
  let el, $
  const css = `#acc5195{position:fixed;right:16px;bottom:16px;z-index:2147483000;width:380px;max-height:72vh;display:flex;flex-direction:column;
  font:12px/1.5 system-ui,sans-serif;color:#222;background:#fff;border:1px solid #d9a441;border-radius:10px;box-shadow:0 6px 24px rgba(0,0,0,.18)}
  #acc5195 header{display:flex;align-items:center;gap:6px;padding:6px 10px;background:#fff4dc;border-radius:10px 10px 0 0;cursor:pointer;font-weight:600}
  #acc5195 header span{flex:1}#acc5195 .body{overflow:auto;padding:8px 10px}#acc5195.min .body{display:none}#acc5195.min{width:auto}
  #acc5195 button{font:inherit;padding:3px 8px;margin:2px 2px 2px 0;border:1px solid #bbb;border-radius:6px;background:#fafafa;cursor:pointer}
  #acc5195 .q button{border-color:#d9a441;background:#fff8e6}#acc5195 .p{border:1px solid #f0c36d;background:#fffaf0;border-radius:6px;padding:4px 6px;margin:4px 0}
  #acc5195 h4{margin:8px 0 2px;font-size:12px}#acc5195 pre{margin:0;white-space:pre-wrap;background:#f6f6f6;border-radius:4px;padding:4px;max-height:130px;overflow:auto}
  #acc5195 .note{color:#666}#acc5195 .msg{color:#8a5a00;min-height:1em}#acc5195 label{margin-right:8px;white-space:nowrap}`
  const modeSel = (k, label) => `<label>${label} <select data-mode="${k}"><option value="off">不挂</option><option value="once">挂下一次</option><option value="always">每次都挂</option></select></label>`
  function mount() {
    const st = document.createElement('style'); st.textContent = css; document.head.appendChild(st)
    el = document.createElement('div'); el.id = 'acc5195'
    el.innerHTML = `<header title="点击折叠/展开"><span>#5195 验收面板（模拟回包）</span><b class="n"></b><small class="t">收起</small></header>
    <div class="body">
     <div class="note">挂起在浏览器端：请求先不发出，交给应用的 Promise 扣在这里，挂多久都行；放行·成功时才真正发给验收代理，放行·失败直接给应用 409/500。所有写请求仍由代理拦下，不到生产。</div>
     <div class="msg"></div>
     <div><button data-act="reset">全部重置</button><button data-act="preset-scan">设：扫描挂下一次 + 第 2 次起去掉 wolfram</button></div>
     <div>${modeSel('holdA', '占用A')}${modeSel('scan', '扫描')}${modeSel('install', '安装')}${modeSel('save', '保存')}</div>
     <div class="q"><button data-q="holdA" data-as="fail">A 占用 → 放行失败(409)</button><button data-q="install" data-as="ok">安装 → 放行成功</button><button data-q="scan" data-as="fail">扫描 → 放行失败(500)</button><button data-q="save" data-as="ok">保存 → 放行成功</button></div>
     <h4>等放行（浏览器端）</h4><div class="pend">（没有）</div>
     <h4>测试队友（代理里的真值）</h4><pre class="state"></pre>
     <h4>保存记录（版本号，代理记的）</h4><pre class="saves">（还没有）</pre>
     <h4>放行记录</h4><pre class="rel">（还没有）</pre>
    </div>`
    document.body.appendChild(el); $ = (s) => el.querySelector(s)
    el.querySelector('header').onclick = () => { el.classList.toggle('min'); $('.t').textContent = el.classList.contains('min') ? '展开' : '收起' }
    el.addEventListener('change', (e) => { const s = e.target.closest('select[data-mode]'); if (s) { modes[s.dataset.mode] = s.value; say(`已设：${s.parentElement.textContent.trim().split(' ')[0]} = ${s.selectedOptions[0].text}`) } })
    el.addEventListener('click', async (e) => {
      const b = e.target.closest('button'); if (!b) return; e.stopPropagation()
      if (b.dataset.act === 'reset') {
        for (const p of pending.splice(0)) p.go('ok')
        Object.assign(modes, DEFAULT_MODES()); log.length = 0
        await realFetch('/api/__accept/reset', { redirect: 'manual' })
        say('已重置：占用A 挂下一次、安装每次都挂，测试队友名单回到初始。请点「AI 队友」卡片右上角的刷新按钮重读名册（不用刷新页面）。')
      }
      if (b.dataset.act === 'preset-scan') {
        modes.scan = 'once'
        await realFetch('/api/__accept/config', { method: 'POST', redirect: 'manual', headers: { 'content-type': 'application/x-www-form-urlencoded' }, body: 'catalogDropAfterFirst=true' })
        say('已设：下一次扫描挂起，第 2 次起的目录去掉 wolfram。')
      }
      if (b.dataset.q) { const p = pending.find((x) => x.kind === b.dataset.q); if (!p) say(`现在没有挂着的「${KIND[b.dataset.q].label}」`); else { release(p.id, b.dataset.as); say(`已放行 #${p.id}`) } }
      if (b.dataset.id) release(Number(b.dataset.id), b.dataset.as)
      render(); poll()
    })
    render(); poll(); setInterval(poll, 1500); setInterval(render, 1000)
  }
  function say(t) { if (!$) return; $('.msg').textContent = t; clearTimeout(say.t); say.t = setTimeout(() => ($('.msg').textContent = ''), 6000) }
  function render() {
    if (!$) return
    for (const s of el.querySelectorAll('select[data-mode]')) s.value = modes[s.dataset.mode]
    $('.n').textContent = pending.length ? `（${pending.length} 条等放行）` : ''
    const box = $('.pend'); box.innerHTML = pending.length ? '' : '（没有）'
    for (const p of pending) { const d = document.createElement('div'); d.className = 'p'; d.textContent = `#${p.id} ${p.what}（已挂 ${Math.round((Date.now() - p.since) / 1000)} 秒） `
      for (const [as, l] of [['ok', '放行·成功'], ['fail', '放行·失败']]) { const bt = document.createElement('button'); bt.textContent = l; bt.dataset.id = p.id; bt.dataset.as = as; d.appendChild(bt) }
      box.appendChild(d) }
    $('.rel').textContent = log.length ? log.join('\n') : '（还没有）'
  }
  async function poll() {
    if (!$) return
    try { const j = await (await realFetch('/api/__accept/state', { cache: 'no-store' })).json()
      $('.state').textContent = j.state.join('\n')
      const saves = j.log.filter((l) => /保存 测试队友|拦下：对真实队友/.test(l)).reverse(); $('.saves').textContent = saves.length ? saves.join('\n') : '（还没有）'
    } catch {}
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount); else mount()
})()

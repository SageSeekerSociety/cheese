// The flow demo (fence `demo-flow`): one request walked across lanes.
//
// The actors are columns, every step is an arrow from one column to another,
// and a route picks which steps belong to this telling — so the same lanes tell
// the happy path, the refused path and the expired path, and where a route is
// stopped is a step you can point at. `block: true` paints that step red.
//
// Like the context window this builds its own DOM; the prerendered list in the
// figure is the narrow-screen and no-script version, and demoText in demos.mjs
// is what a model reads instead.
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c])
const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches

export function mountFlow(fig) {
  const cfg = JSON.parse(fig.querySelector('[data-fl]')?.textContent || 'null')
  if (!cfg) return
  const { actors, routes, steps, title, note, resident } = cfg
  const at = (key) => actors.findIndex((a) => a.key === key)
  let route = routes[0].key
  let pos = 1 // steps emphasised so far
  let pinned = null
  let hovered = null
  let playing = false
  let timer = 0

  const root = document.createElement('div')
  root.className = 'fl'
  root.innerHTML = `
    <div class="fl-head">
      <div class="fl-titles"><b>${esc(title)}</b><span>${esc(note || '')}</span></div>
      <div class="fl-routes" data-routes role="group" aria-label="路线"></div>
    </div>
    <div class="fl-stage">
      <div class="fl-names">${actors.map((a) => `<div class="fl-name"><b>${esc(a.label)}</b>${a.sub ? `<span>${esc(a.sub)}</span>` : ''}</div>`).join('')}</div>
      <div class="fl-canvas" data-canvas>
        ${actors.map((a, i) => `<div class="fl-life" style="left:${(((i + 0.5) / actors.length) * 100).toFixed(2)}%"></div>`).join('')}
        <div data-steps></div>
      </div>
    </div>
    ${resident.length ? `<div class="fl-resident"><b>常驻、不随发版替换</b>${resident.map((x) => `<span>${esc(x)}</span>`).join('')}<small>下面这层不参与交接，连接不断。</small></div>` : ''}
    <div class="fl-foot">
      <button type="button" class="fl-btn" data-prev aria-label="上一步">←</button>
      <button type="button" class="fl-btn fl-play" data-play>播放 <span aria-hidden="true">▶</span></button>
      <button type="button" class="fl-btn" data-next aria-label="下一步">→</button>
      <div class="fl-track"><i data-progress></i></div>
      <span class="fl-count" data-count aria-live="polite"></span>
    </div>
    <div class="fl-result" data-result></div>`
  fig.appendChild(root)
  fig.classList.add('fl-live')
  root.style.setProperty('--n', actors.length) // the lane grid, one column per actor

  const $ = (s) => root.querySelector(s)
  const stepsEl = $('[data-steps]')
  const routesEl = $('[data-routes]')

  routesEl.innerHTML = routes.map((r) => `<button type="button" class="fl-route" data-route="${esc(r.key)}">${esc(r.label)}</button>`).join('')
  // One element per step of the whole fence, built once: paint() only decides
  // which of them this route shows.
  stepsEl.innerHTML = steps.map((s, i) => {
    const a = at(s.from)
    const b = at(s.to)
    const lo = Math.min(a, b)
    const hi = Math.max(a, b)
    const left = ((lo + 0.5) / actors.length) * 100
    const width = ((hi - lo) / actors.length) * 100
    return `<div class="fl-step${s.block ? ' block' : ''}" data-i="${i}">
      <div class="fl-arrow${b < a ? ' rtl' : ''}" style="left:${left.toFixed(2)}%;width:${width.toFixed(2)}%">
        <i></i>
        <span>${esc(s.label)}${s.ref ? `<code>${esc(s.ref)}</code>` : ''}</span>
      </div>
      ${s.desc ? `<p class="fl-desc">${esc(s.desc)}</p>` : ''}
      ${s.link ? `<a class="fl-link" href="${esc(s.link)}">看这一节 →</a>` : ''}
      ${s.block ? '<span class="fl-block">这一条路线拦在这里</span>' : ''}
    </div>`
  }).join('')

  const mine = () => steps.filter((s) => s.routes.includes(route))
  const shown = () => mine().slice(0, pos)

  function paint() {
    const list = mine()
    const cur = routes.find((r) => r.key === route)
    routesEl.querySelectorAll('[data-route]').forEach((b) => b.classList.toggle('on', b.dataset.route === route))
    root.querySelectorAll('.fl-step').forEach((el) => {
      const s = steps[Number(el.dataset.i)]
      const on = s.routes.includes(route)
      const k = list.indexOf(s)
      el.hidden = !on
      if (!on) return
      const active = pinned ?? hovered
      el.classList.toggle('done', k < pos - 1)
      el.classList.toggle('on', active !== null ? k === active : k === pos - 1)
      el.classList.toggle('dim', active !== null && k !== active)
      el.classList.toggle('hot-lane', active === null || k === active)
    })
    $('[data-count]').textContent = `${Math.min(pos, list.length)} / ${list.length}`
    $('[data-progress]').style.width = `${(list.length ? Math.min(pos, list.length) / list.length : 0) * 100}%`
    $('[data-result]').className = `fl-result fl-${cur.tone}`
    $('[data-result]').innerHTML = `<b>${esc(cur.label)}</b>${cur.note ? `<span>${esc(cur.note)}</span>` : ''}${cur.result ? `<p>${esc(cur.result)}</p>` : ''}`
    const play = $('[data-play]')
    play.firstChild.nodeValue = pos >= list.length ? '重来 ' : playing ? '暂停 ' : '播放 '
    play.setAttribute('aria-pressed', playing ? 'true' : 'false')
  }

  function stop() { playing = false; clearTimeout(timer); paint() }
  function step() {
    timer = 0
    if (pos >= mine().length) return stop()
    pos++
    paint()
    if (pos < mine().length) timer = setTimeout(step, reduced ? 0 : 1100)
    else stop()
  }
  function play() {
    if (playing) return stop()
    if (pos >= mine().length) pos = 0
    playing = true
    paint()
    timer = setTimeout(step, 220)
  }
  const goTo = (n) => { stop(); pos = Math.max(1, Math.min(mine().length, n)); paint() }

  root.addEventListener('click', (e) => {
    const b = e.target.closest('[data-route]')
    if (b) { route = b.dataset.route; pinned = null; pos = 1; stop(); paint(); return }
    if (e.target.closest('[data-play]')) return play()
    if (e.target.closest('[data-prev]')) return goTo(pos - 1)
    if (e.target.closest('[data-next]')) return goTo(pos + 1)
    const el = e.target.closest('.fl-step')
    if (!el) return
    const k = mine().indexOf(steps[Number(el.dataset.i)])
    pinned = pinned === k ? null : k
    if (k >= 0) pos = Math.max(pos, k + 1)
    paint()
  })
  root.addEventListener('mouseover', (e) => {
    const el = e.target.closest('.fl-step')
    const k = el ? mine().indexOf(steps[Number(el.dataset.i)]) : null
    if (k !== hovered) { hovered = k; paint() }
  })
  root.addEventListener('mouseleave', () => { hovered = null; paint() })

  paint()
}

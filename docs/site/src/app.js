// Behaviour for the prerendered docs pages. Every page is complete HTML without
// this script; it adds search, 问芝士, theme, and the home page's motion.
import { build, stageFor, BUBBLE } from 'virtual:motion'
import { ic } from './content.js'
import { sayHtml } from './home.mjs'
import { freshToken, signInUrl } from './session.js'

const $ = (s, r = document) => r.querySelector(s)
const $$ = (s, r = document) => [...r.querySelectorAll(s)]
const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches
const isDark = () => document.documentElement.classList.contains('dark')
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c])
const PAGE = JSON.parse($('#page-data')?.textContent || '{}')

let toastTimer
function toast(m) { const t = $('#toast'); t.textContent = m; t.classList.add('show'); clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove('show'), 1900) }

// ---------- header, sidebar, table of contents ----------
function moveTabs() {
  const ind = $('#tabInd'), on = $('.tab.on')
  if (!ind) return
  if (!on) { ind.classList.remove('on'); return }
  ind.style.left = on.offsetLeft + 'px'; ind.style.width = on.offsetWidth + 'px'; ind.classList.add('on')
}
function moveSidePill(target) {
  const pill = $('#sidePill'); if (!pill) return
  const on = target || $('.side a.on')
  if (!on) { pill.style.opacity = 0; return }
  $$('.side a[data-slug]').forEach((a) => a.classList.toggle('on', a === on))
  pill.style.top = on.offsetTop + 'px'; pill.style.height = on.offsetHeight + 'px'; pill.style.opacity = 1
}
let tocSpy = null
function setupToc() {
  const links = $$('[data-toc]'), ind = $('#tocInd')
  const targets = links.map((l) => document.getElementById(l.dataset.toc)).filter(Boolean)
  if (!targets.length) return
  const set = (id) => { links.forEach((l) => l.classList.toggle('on', l.dataset.toc === id)); const on = links.find((l) => l.dataset.toc === id); if (on && ind) { ind.style.top = on.offsetTop + 'px'; ind.style.height = on.offsetHeight + 'px' } }
  set(targets[0].id)
  tocSpy = () => { let best = targets[0]; for (const t of targets) if (t.getBoundingClientRect().top < 170) best = t; set(best.id) }
}

// ---------- motion helpers ----------
function setupReveal() {
  if (reduced || !('IntersectionObserver' in window)) { $$('[data-reveal]').forEach((el) => el.classList.add('in')); return }
  const io = new IntersectionObserver((es) => es.forEach((e) => { if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target) } }), { threshold: 0.12, rootMargin: '0px 0px -40px 0px' })
  $$('[data-reveal]').forEach((el) => io.observe(el))
}
function splitChars() {
  let i = 0
  $$('[data-split]').forEach((el) => { el.innerHTML = [...el.textContent].map((c) => (c === ' ' ? ' ' : `<span class="ch" style="--i:${i++}">${esc(c)}</span>`)).join('') })
}
function setupSpot(root = document) {
  $$('.spot', root).forEach((el) => {
    if (el.dataset.sp) return
    el.dataset.sp = 1
    el.addEventListener('pointermove', (e) => {
      const r = el.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top
      el.style.setProperty('--sx', x + 'px'); el.style.setProperty('--sy', y + 'px')
      if (el.classList.contains('tilt')) { el.style.setProperty('--ry', ((x / r.width) - 0.5) * 12 + 'deg'); el.style.setProperty('--rx', -((y / r.height) - 0.5) * 10 + 'deg') }
    })
    el.addEventListener('pointerleave', () => { el.style.setProperty('--rx', '0deg'); el.style.setProperty('--ry', '0deg') })
  })
}
function setupMagnetic() {
  if (reduced || matchMedia('(pointer: coarse)').matches) return
  $$('.magnetic').forEach((el) => {
    el.addEventListener('pointermove', (e) => { const r = el.getBoundingClientRect(); el.style.transform = `translate(${(e.clientX - r.left - r.width / 2) * 0.22}px,${(e.clientY - r.top - r.height / 2) * 0.3}px)` })
    el.addEventListener('pointerleave', () => { el.style.transform = '' })
  })
}

function onScroll() {
  const h = document.documentElement.scrollHeight - innerHeight
  $('#progress').style.transform = `scaleX(${h > 0 ? scrollY / h : 0})`
  $('#hdr').classList.toggle('solid', PAGE.kind !== 'home' || scrollY > 20)
  tocSpy?.()
  const tl = $('#timeline')
  if (tl) {
    const r = tl.getBoundingClientRect(), mid = innerHeight * 0.55
    $('#tlFill').style.setProperty('--p', Math.max(0, Math.min(1, (mid - r.top) / r.height)))
    $$('.item', tl).forEach((it) => it.classList.toggle('lit', it.getBoundingClientRect().top < mid))
  }
  onStoryScroll()
}

// ---------- theme: a circle of night spreading from where you clicked ----------
function applyTheme(dark) {
  document.documentElement.classList.toggle('dark', dark)
  try { localStorage.setItem('docs-dark', dark ? '1' : '0') } catch { /* private mode */ }
  loadDiagrams(); syncRoom()
}
function toggleTheme(x, y) {
  const to = !isDark()
  if (!document.startViewTransition || reduced) { applyTheme(to); return }
  const t = document.startViewTransition(() => applyTheme(to))
  t.ready.then(() => {
    const r = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y))
    document.documentElement.animate({ clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${r}px at ${x}px ${y}px)`] }, { duration: 900, easing: 'cubic-bezier(.7,0,.2,1)', pseudoElement: '::view-transition-new(root)' })
  })
}

// ---------- search ----------
let index = null, hits = [], sel = 0
async function loadIndex() {
  if (index) return index
  const get = (u) => fetch(u, { credentials: 'same-origin' }).then((r) => (r.ok ? r.json() : [])).catch(() => [])
  // The developer index is served only to admins (nginx asks the backend); anyone else gets a 401 and an empty list.
  const [pub, dev] = await Promise.all([get('/docs/search.json'), PAGE.section === 'dev' ? get('/docs/dev/search.json') : []])
  index = [...pub, ...dev]
  return index
}
function score(e, terms) {
  let s = 0
  for (const t of terms) {
    const inTitle = e.t.toLowerCase().includes(t), inHead = e.h.toLowerCase().includes(t), inText = e.x.toLowerCase().includes(t)
    if (!inTitle && !inHead && !inText) return 0
    s += (inTitle ? 6 : 0) + (inHead ? 4 : 0) + (inText ? 1 : 0)
  }
  return s
}
const mark = (s, terms) => { let out = esc(s); for (const t of terms) if (t) out = out.replace(new RegExp(esc(t).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi'), (m) => `<mark>${m}</mark>`); return out }
function snippet(x, terms) {
  const low = x.toLowerCase(), at = Math.max(0, Math.min(...terms.map((t) => low.indexOf(t)).filter((i) => i >= 0), 0) - 30)
  return (at ? '…' : '') + x.slice(at, at + 110)
}
async function doSearch() {
  const q = $('#q').value.trim()
  const terms = q.toLowerCase().split(/\s+/).filter(Boolean)
  const all = await loadIndex()
  if (q !== $('#q').value.trim()) return
  if (!terms.length) {
    const seen = new Set()
    hits = all.filter((e) => !e.u.includes('#') && !seen.has(e.t) && seen.add(e.t)).slice(0, 7)
  } else {
    hits = all.map((e) => [score(e, terms), e]).filter(([s]) => s > 0).sort((a, b) => b[0] - a[0]).slice(0, 12).map(([, e]) => e)
  }
  sel = 0
  $('#res').innerHTML = hits.length
    ? hits.map((h, n) => `<a class="hit${n === 0 ? ' on' : ''}" href="${h.u}" data-i="${n}" style="--k:${n}"><span class="hi">${ic(h.u.startsWith('/docs/dev/') ? 'code' : 'doc')}</span><div style="min-width:0"><b>${mark(h.t, terms)}${h.h ? ` <span class="sub-h">› ${mark(h.h, terms)}</span>` : ''}</b><small>${terms.length ? mark(snippet(h.x, terms), terms) : esc(h.g)}</small></div><span class="go">${ic('arrow')}</span></a>`).join('')
    : `<div class="none">文档里没找到「${esc(q)}」——按 ⌘↵ 问问芝士？</div>`
  $('#askTxt').textContent = q ? `问芝士：「${q}」` : '没找到？直接问芝士'
}
function openSearch(v = '') {
  $('#scrim').classList.add('open'); $('#palette').classList.add('open')
  const q = $('#q'); q.value = v; doSearch(); setTimeout(() => q.focus(), 30)
}
function closeAll() {
  ['#scrim', '#palette'].forEach((s) => $(s).classList.remove('open'))
  $('#menu')?.classList.remove('open'); $('#side')?.classList.remove('open')
  if (innerWidth <= 820) closeDock()
}

// ---------- 问芝士 ----------
// The backend answers only from these docs: it retrieves the relevant sections
// first and refuses questions the docs do not cover. Signed-in users only.
const history = []
const SUGGEST = ['怎么邀请同学进项目？', '采纳和合并是一回事吗？', '能用我自己的电脑跑芝士吗？']
let asking = null
function openAsk(q) {
  $('#palette').classList.remove('open'); $('#drawer').classList.add('open')
  if (innerWidth <= 820) $('#scrim').classList.add('open'); else { $('#scrim').classList.remove('open'); document.body.classList.add('docked') }
  if (!$('#chat').children.length) greet()
  if (q && q.trim()) ask(q.trim())
  setTimeout(() => $('#askInput').focus(), 300)
}
function closeDock() { $('#drawer').classList.remove('open'); document.body.classList.remove('docked'); $('#scrim').classList.remove('open') }
function greet() {
  history.length = 0
  $('#chat').innerHTML = `<div class="a"><span class="brand-mark sm"><img src="${$('.brand-mark img').src}" alt=""></span><div class="body"><p>你好，我是芝士。关于知是怎么用，问我就行——我只根据这份文档回答，并告诉你出自哪一节。</p></div></div>`
  $('#suggest').innerHTML = SUGGEST.map((s) => `<button data-sug>${esc(s)}</button>`).join('')
}
// A small, safe subset of Markdown for answers: paragraphs, lists, bold, code, and links back into the docs.
// A link survives only if it points at a page the answer was given to cite; a
// model that invents an address gets its words shown, not a dead link.
function renderAnswer(text, sources = []) {
  const pages = new Set(sources.map((c) => c.url.split('#')[0]))
  const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\[([^\]]+)\]\((\/docs\/[\w/#-]+)\)/g, (m, label, url) => pages.has(url.split('#')[0]) ? `<a class="link" href="${url}">${label}</a>` : label)
  const blocks = text.split(/\n{2,}/).map((b) => {
    const lines = b.split('\n')
    if (lines.every((l) => /^\s*([-*]|\d+\.)\s/.test(l))) return `<ul>${lines.map((l) => `<li>${inline(l.replace(/^\s*([-*]|\d+\.)\s/, ''))}</li>`).join('')}</ul>`
    return `<p>${lines.map(inline).join('<br>')}</p>`
  })
  return blocks.join('')
}
const citeHtml = (c) => `<a class="cite" href="${esc(c.url)}">${ic('doc')}<span>${esc(c.title)}${c.heading ? ` · ${esc(c.heading)}` : ''}</span><small>${esc(c.url)}</small></a>`
async function ask(q) {
  if (asking) return
  const chat = $('#chat')
  $('#suggest').innerHTML = ''
  chat.insertAdjacentHTML('beforeend', `<div class="q">${esc(q)}</div><div class="a"><span class="brand-mark sm"><img src="${$('.brand-mark img').src}" alt=""></span><div class="body"><span class="typing"><i></i><i></i><i></i></span></div></div>`)
  const body = $$('.a .body', chat).pop()
  chat.scrollTop = chat.scrollHeight
  const token = await freshToken()
  if (!token) {
    body.innerHTML = `<p>问芝士需要先登录知是：答案按你的账号限额，防止被滥用。</p><a class="pill" href="${signInUrl()}">登录后再问 ${ic('arrow')}</a>`
    return
  }
  asking = new AbortController()
  $('#askSend').disabled = true
  let text = '', sources = []
  try {
    const res = await fetch('/api/docs/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}`, Accept: 'text/event-stream' },
      body: JSON.stringify({ question: q, page: $('#ctxUse')?.checked && PAGE.kind === 'doc' ? PAGE.md.replace(/^\/docs\/|\.md$/g, '') : null, history: history.slice(-4) }),
      signal: asking.signal,
    })
    if (!res.ok || !res.body) {
      const err = await res.json().catch(() => ({}))
      const msg = res.status === 429 ? (err.message || '提问太频繁了，稍后再试。') : res.status === 401 ? '登录已过期，请重新登录。' : (err.message || '芝士暂时答不上来，稍后再试。')
      body.innerHTML = `<p>${esc(msg)}</p>`
      return
    }
    const reader = res.body.getReader(), dec = new TextDecoder()
    let buf = ''
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      buf += dec.decode(value, { stream: true })
      let cut
      while ((cut = buf.indexOf('\n\n')) >= 0) {
        const raw = buf.slice(0, cut); buf = buf.slice(cut + 2)
        const ev = (/^event: (.+)$/m.exec(raw) || [])[1] || 'message'
        const data = (/^data: (.*)$/m.exec(raw) || [])[1]
        if (!data) continue
        const d = JSON.parse(data)
        if (ev === 'sources') sources = d.sources || []
        else if (ev === 'delta') { text += d.text; body.innerHTML = renderAnswer(text, sources) + '<span class="stream-caret"></span>'; chat.scrollTop = chat.scrollHeight }
        else if (ev === 'error') { text = text || d.message; body.innerHTML = `<p>${esc(d.message)}</p>` }
      }
    }
    body.innerHTML = renderAnswer(text || '没有拿到回答，稍后再试。', sources) + (sources.length ? `<div class="cites">${sources.map(citeHtml).join('')}</div>` : '')
    history.push({ role: 'user', content: q }, { role: 'assistant', content: text.slice(0, 1200) })
  } catch (e) {
    if (e.name !== 'AbortError') body.innerHTML = '<p>网络出了点问题，稍后再试。</p>'
  } finally {
    asking = null; $('#askSend').disabled = false
    chat.scrollTop = chat.scrollHeight
  }
}

// ---------- copy ----------
async function copyPage() {
  if (!PAGE.md) return
  try {
    const md = await fetch(PAGE.md).then((r) => { if (!r.ok) throw new Error(); return r.text() })
    await navigator.clipboard.writeText(md)
    toast('已复制本页 Markdown')
  } catch { toast('复制失败，可以打开 Markdown 原文手动复制') }
}

// ---------- diagrams: archify viewers follow the site theme ----------
function loadDiagrams() {
  $$('iframe[data-diagram]').forEach((f) => { const src = `${f.dataset.diagram}?embed=1&theme=${isDark() ? 'dark' : 'light'}`; if (f.getAttribute('src') !== src) f.setAttribute('src', src) })
}

// ---------- home: logo, pinned room story, role tabs, pickers ----------
let heroVisible = true
function mountHero() {
  const host = $('#heroLogo'); if (!host) return
  host.innerHTML = build('hero')
  const svg = $('svg', host), S = stageFor(svg)
  let clock = 0, last = 0
  const tick = (now) => {
    if (!svg.isConnected) return
    if (heroVisible) {
      const dt = last ? Math.min(now - last, 64) : 0; clock += dt
      S.reset(); if (!reduced) BUBBLE.frame(S, Math.min(clock, BUBBLE.D), clock >= BUBBLE.D ? clock - BUBBLE.D : null)
    }
    last = heroVisible ? now : 0
    requestAnimationFrame(tick)
  }
  requestAnimationFrame(tick)
  new IntersectionObserver((es) => { heroVisible = es[0].isIntersecting }).observe(host)
  host.addEventListener('click', () => { clock = 0 })
}

// The room renders at a real device size and is scaled, never reflowed: desktop
// visitors see the desktop workbench, phone visitors the phone one.
const ROOM_SIZE = { desktop: [1200, 740], phone: [390, 760] }
let storyStep = -1
function fitRoom() {
  const box = $('#roomFit'), f = $('#roomFrame'); if (!box || !f) return
  const phone = matchMedia('(max-width: 820px), (pointer: coarse) and (max-width: 1024px)').matches
  const [w, h] = ROOM_SIZE[phone ? 'phone' : 'desktop']
  box.classList.toggle('phone', phone)
  const maxH = phone ? Math.min(innerHeight * 0.72, 640) : Infinity
  const k = Math.min(box.clientWidth / w, maxH / h, 1)
  f.style.width = w + 'px'; f.style.height = h + 'px'; f.style.transform = `scale(${k})`
  f.style.left = Math.max(0, (box.clientWidth - w * k) / 2) + 'px'
  box.style.height = h * k + 'px'
}
function syncRoom() {
  const w = $('#roomFrame')?.contentWindow
  if (!w?.setStep) return
  w.setTheme(isDark() ? 'dark' : 'light'); w.setStep(Math.max(storyStep, 0))
}
function mountStory() {
  const f = $('#roomFrame'); if (!f) return
  new ResizeObserver(fitRoom).observe($('#roomFit')); addEventListener('resize', fitRoom); fitRoom()
  f.addEventListener('load', () => { syncRoom(); onStoryScroll() })
  // Load the room (1.5 MB) only when the section is about to come into view.
  const io = new IntersectionObserver((es) => { if (es[0].isIntersecting) { f.src = f.dataset.src; io.disconnect() } }, { rootMargin: '600px 0px' })
  io.observe(f)
}
function onStoryScroll() {
  const sec = $('#story'); if (!sec) return
  const r = sec.getBoundingClientRect(), run = sec.offsetHeight - innerHeight
  const p = Math.max(0, Math.min(1, -r.top / Math.max(run, 1)))
  const steps = $$('#storySteps li'), n = Math.min(steps.length - 1, Math.floor(p * steps.length))
  $('#storyBar')?.style.setProperty('--p', p)
  if (n === storyStep) return
  storyStep = n
  steps.forEach((li, i) => { li.classList.toggle('on', i === n); li.classList.toggle('done', i < n) })
  syncRoom()
}

let home = null
async function homeData() {
  home ||= await fetch('/docs/home.json').then((r) => r.json())
  return home
}
const say = { who: '', what: 0, open: '' }
async function renderSay() {
  const { pages, who } = await homeData()
  say.who ||= Object.keys(who)[0]
  const h = sayHtml(say.who, say.what, who, pages, say.open)
  $('#pickWho').innerHTML = h.who; $('#pickWhat').innerHTML = h.what; $('#sayOut').innerHTML = h.out
  const work = $('#workPanel')
  if (work.dataset.who !== say.who) { work.dataset.who = say.who; work.innerHTML = h.work }
}

// ---------- developer docs: the admin check ----------
async function devGate() {
  const msg = $('#gateMsg'), actions = $('#gateActions')
  const token = await freshToken()
  if (!token) {
    msg.textContent = '请先用平台管理员账号登录知是。'
    actions.innerHTML = `<a class="pill" href="${signInUrl()}">登录 ${ic('arrow')}</a><a class="pill alt" href="/docs/">回到使用文档</a>`
    return
  }
  const res = await fetch('/api/docs/dev-access', { method: 'POST', headers: { Authorization: `Bearer ${token}` }, credentials: 'same-origin' }).catch(() => null)
  if (res?.ok) { location.reload(); return }
  msg.textContent = res?.status === 403 ? '你的账号不是平台管理员。开发文档写给维护这个平台的人；需要访问请联系平台管理员。' : '暂时无法确认你的身份，稍后再试。'
  actions.innerHTML = `<a class="pill" href="/docs/">回到使用文档</a>`
}

// ---------- events ----------
document.addEventListener('click', (e) => {
  const t = e.target.closest('[data-open-search],[data-open-ask],[data-close-ask],[data-new-chat],[data-menu],[data-copy-page],[data-copy],[data-f],[data-sug],[data-pick],[data-pick-opt],.code-tab,.side a[href^="#"]')
  if (say.open && !e.target.closest('.x-menu,[data-pick]')) { say.open = ''; renderSay() }
  if (!t) { if (!e.target.closest('.menu')) $('#menu')?.classList.remove('open'); return }
  if (t.matches('[data-open-search]')) { e.preventDefault(); openSearch() }
  else if (t.matches('[data-open-ask]')) { e.preventDefault(); $('#menu')?.classList.remove('open'); if (t.closest('.hdr') && $('#drawer').classList.contains('open')) closeDock(); else openAsk() }
  else if (t.matches('[data-close-ask]')) closeDock()
  else if (t.matches('[data-new-chat]')) { asking?.abort(); greet() }
  else if (t.matches('[data-sug]')) ask(t.textContent)
  else if (t.matches('[data-menu]')) $('#menu').classList.toggle('open')
  else if (t.matches('[data-copy-page]')) { e.preventDefault(); $('#menu')?.classList.remove('open'); copyPage() }
  else if (t.matches('[data-copy]')) {
    const pre = t.closest('.code').querySelector('pre')
    navigator.clipboard?.writeText(pre.innerText).then(() => { t.classList.add('done'); t.innerHTML = ic('check'); setTimeout(() => { t.classList.remove('done'); t.innerHTML = ic('copy') }, 1400) }).catch(() => toast('复制失败'))
  } else if (t.matches('[data-f]')) {
    const f = t.dataset.f
    $$('[data-f]').forEach((b) => b.classList.toggle('on', b === t)); moveFilter()
    $$('.item').forEach((i) => i.classList.toggle('hide', f !== 'all' && i.dataset.t !== f))
    $$('.day').forEach((d) => d.classList.toggle('hide', !d.querySelector('.item:not(.hide)')))
    onScroll()
  } else if (t.matches('[data-pick-opt]')) { if (t.dataset.pickOpt === 'who') { say.who = t.dataset.v; say.what = 0 } else say.what = +t.dataset.v; say.open = ''; renderSay() }
  else if (t.matches('[data-pick]')) { say.open = say.open === t.dataset.pick ? '' : t.dataset.pick; renderSay() }
  else if (t.matches('.code-tab')) t.parentElement.querySelectorAll('.code-tab').forEach((x) => x.classList.toggle('on', x === t))
  else if (t.matches('.side a[href^="#"]')) moveSidePill(t)
})
function moveFilter() {
  const on = $('#filters button.on'), ind = $('#fInd')
  if (on && ind) { ind.style.left = on.offsetLeft + 'px'; ind.style.width = on.offsetWidth + 'px' }
}

$('#q').addEventListener('input', doSearch)
$('#q').addEventListener('keydown', (e) => {
  const all = $$('.hit')
  if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && all.length) { e.preventDefault(); sel = (sel + (e.key === 'ArrowDown' ? 1 : -1) + all.length) % all.length; all.forEach((h, i) => h.classList.toggle('on', i === sel)); all[sel].scrollIntoView({ block: 'nearest' }) }
  if (e.key === 'Enter') { e.preventDefault(); if (e.metaKey || e.ctrlKey || !hits[sel]) openAsk($('#q').value); else location.href = hits[sel].u }
})
$('#res').addEventListener('pointermove', (e) => { const h = e.target.closest('.hit'); if (h && +h.dataset.i !== sel) { sel = +h.dataset.i; $$('.hit').forEach((x, i) => x.classList.toggle('on', i === sel)) } })
$('#askRow').addEventListener('click', () => openAsk($('#q').value))
$('#askForm').addEventListener('submit', (e) => { e.preventDefault(); const v = $('#askInput').value.trim(); if (v) { ask(v); $('#askInput').value = '' } })
$('#scrim').addEventListener('click', () => { closeAll(); closeDock() })
$('#menuBtn').addEventListener('click', () => { $('#side')?.classList.add('open'); $('#scrim').classList.add('open') })
$('#themeBtn').addEventListener('click', (e) => { const r = e.currentTarget.getBoundingClientRect(); toggleTheme(r.left + r.width / 2, r.top + r.height / 2) })
document.addEventListener('keydown', (e) => {
  const typing = /INPUT|TEXTAREA/.test(document.activeElement?.tagName || '')
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); openSearch() }
  else if (e.key === '/' && !typing) { e.preventDefault(); openSearch() }
  else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'i') { e.preventDefault(); $('#drawer').classList.contains('open') ? closeDock() : openAsk() }
  else if (e.key === 'Escape') { if (say.open) { say.open = ''; renderSay() } closeAll() }
})
$('#dockResize').addEventListener('pointerdown', (e) => {
  e.preventDefault(); document.body.classList.add('dragging')
  const move = (ev) => document.documentElement.style.setProperty('--dock-w', Math.max(320, Math.min(720, innerWidth - ev.clientX)) + 'px')
  const up = () => { document.body.classList.remove('dragging'); removeEventListener('pointermove', move); removeEventListener('pointerup', up) }
  addEventListener('pointermove', move); addEventListener('pointerup', up)
})
addEventListener('scroll', onScroll, { passive: true })
addEventListener('resize', () => { moveTabs(); moveFilter(); moveSidePill() })

// ---------- start ----------
splitChars()
setupReveal(); setupSpot(); setupMagnetic(); setupToc()
moveTabs(); moveSidePill(); moveFilter(); onScroll()
document.fonts?.ready.then(() => { moveTabs(); moveSidePill() })
loadDiagrams()
if (PAGE.kind === 'home') { mountHero(); mountStory() }
if (PAGE.kind === 'dev-gate') devGate()
if (location.hash) { const el = document.getElementById(location.hash.slice(1)); el?.classList.add('flash') }

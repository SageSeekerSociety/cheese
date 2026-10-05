// Behaviour for the prerendered docs pages. Every page is complete HTML without
// this script; it adds search, 问芝士, the theme switch and the interactive demos.
import { ic } from './content.js'
import { freshToken, signInUrl } from './session.js'
import { mountDemos } from './demo-dom.mjs'
import { stepOf, walkHtml } from './walk.mjs'
import { recordVisit } from './visit.js'

const $ = (s, r = document) => r.querySelector(s)
const $$ = (s, r = document) => [...r.querySelectorAll(s)]
const isDark = () => document.documentElement.dataset.theme === 'dark'
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c])
const PAGE = JSON.parse($('#page-data')?.textContent || '{}')

let toastTimer
function toast(m) { const t = $('#toast'); t.textContent = m; t.classList.add('show'); clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove('show'), 1900) }

// ---------- table of contents: the section being read ----------
let tocSpy = null
function setupToc() {
  const links = $$('[data-toc]')
  const targets = links.map((l) => document.getElementById(l.dataset.toc)).filter(Boolean)
  if (!targets.length) return
  const set = (id) => links.forEach((l) => l.classList.toggle('on', l.dataset.toc === id))
  set(targets[0].id)
  tocSpy = () => { let best = targets[0]; for (const t of targets) if (t.getBoundingClientRect().top < 120) best = t; set(best.id) }
}

// ---------- theme ----------
// Stored the way the app stores it (`cheesex.theme`, a preference, not a
// result): picking the side the system is already on goes back to following
// the system. The demo scenes embedded from the app read the same key, so they
// are reloaded to pick the new one up; they come back on the step they were at.
function toggleTheme() {
  const dark = !isDark()
  const system = matchMedia('(prefers-color-scheme: dark)').matches
  document.documentElement.dataset.theme = dark ? 'dark' : 'light'
  try { localStorage.setItem('cheesex.theme', dark === system ? 'system' : dark ? 'dark' : 'light') } catch { /* private mode */ }
  loadDiagrams()
  $$('iframe[data-dm-embed]').forEach((f) => { f.src = f.src })
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
    ? hits.map((h, n) => `<a class="hit${n === 0 ? ' on' : ''}" href="${h.u}" data-i="${n}"><span class="hi">${ic(h.u.startsWith('/docs/dev/') ? 'code' : 'doc')}</span><div style="min-width:0"><b>${mark(h.t, terms)}${h.h ? ` <span class="sub-h">› ${mark(h.h, terms)}</span>` : ''}</b><small>${terms.length ? mark(snippet(h.x, terms), terms) : esc(h.g)}</small></div><span class="go">${ic('arrow')}</span></a>`).join('')
    : `<div class="none">文档里没找到「${esc(q)}」，按 ⌘↵ 问问芝士</div>`
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
// 划词问芝士: text the reader selected and asked about, sent with the next question.
let quote = ''
function setQuote(text) {
  quote = text
  const box = $('#askQuote'); if (!box) return
  box.hidden = !text
  $('#askQuoteText').textContent = text.length > 120 ? text.slice(0, 120) + '…' : text
}
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
  const chat = $('#chat'), quoted = quote
  setQuote('')
  $('#suggest').innerHTML = ''
  chat.insertAdjacentHTML('beforeend', `<div class="q">${quoted ? `<span class="q-quote">${esc(quoted.length > 120 ? quoted.slice(0, 120) + '…' : quoted)}</span>` : ''}${esc(q)}</div><div class="a"><span class="brand-mark sm"><img src="${$('.brand-mark img').src}" alt=""></span><div class="body"><span class="typing"><i></i><i></i><i></i></span></div></div>`)
  const body = $$('.a .body', chat).pop()
  chat.scrollTop = chat.scrollHeight
  const token = await freshToken()
  if (!token) {
    body.innerHTML = `<p>问芝士需要先登录知是：答案按你的账号限额，防止被滥用。</p><a class="btn btn-primary" href="${signInUrl()}">登录后再问</a>`
    return
  }
  asking = new AbortController()
  $('#askSend').disabled = true
  let text = '', sources = [], steps = [], answered = false, ended = false
  // The answer, with the walk that found it above: the lines are open while the
  // model is still working and folded into one line the moment it starts to
  // answer. A reader who scrolled away and came back sees the finished shape.
  const paint = () => {
    const walk = walkHtml(steps, !answered, esc)
    // Before anything arrives the dots say "working"; once there are lines to
    // read, those say it instead.
    let answer = ''
    if (text) answer = renderAnswer(text, sources)
    else if (!walk && !answered) answer = '<span class="typing"><i></i><i></i><i></i></span>'
    body.innerHTML = walk + answer + (answered && !ended ? '<span class="stream-caret"></span>' : '')
    chat.scrollTop = chat.scrollHeight
  }
  try {
    const res = await fetch('/api/docs/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}`, Accept: 'text/event-stream' },
      body: JSON.stringify({ question: q, page: $('#ctxUse')?.checked && PAGE.kind === 'doc' ? PAGE.md.replace(/^\/docs\/|\.md$/g, '') : null, history: history.slice(-4), quote: quoted || null }),
      signal: asking.signal,
    })
    if (!res.ok || !res.body) {
      const err = await res.json().catch(() => ({}))
      const msg = res.status === 429 ? (err.message || '提问太频繁了，稍后再试。') : res.status === 401 ? '登录已过期，请重新登录。' : (err.message || '芝士暂时答不上来，稍后再试。')
      // Refused for credits: say where the month's usage is.
      const credits = /^credits/.test((err.error && err.error.i18n && err.error.i18n.key) || '')
      body.innerHTML = `<p>${esc(msg)}${credits ? ` <a href="/users/settings/usage">查看用量</a>` : ''}</p>`
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
        else if (ev === 'tool') { steps.push(stepOf(d)); paint() }
        else if (ev === 'delta') { answered = true; text += d.text; paint() }
        else if (ev === 'error') { answered = true; text = text || d.message; paint() }
      }
    }
    ended = true
    if (!text) text = '没有拿到回答，稍后再试。'
    paint()
    if (sources.length) body.insertAdjacentHTML('beforeend', `<div class="cites">${sources.map(citeHtml).join('')}</div>`)
    history.push({ role: 'user', content: quoted ? `关于「${quoted.slice(0, 300)}」：${q}` : q }, { role: 'assistant', content: text.slice(0, 1200) })
  } catch (e) {
    if (e.name !== 'AbortError') body.innerHTML = '<p>网络出了点问题，稍后再试。</p>'
  } finally {
    asking = null; $('#askSend').disabled = false
    chat.scrollTop = chat.scrollHeight
  }
}

// ---------- 划词问芝士: select text in a page, ask about it ----------
function selectedText() {
  const sel = getSelection()
  if (!sel || sel.isCollapsed || !sel.rangeCount) return null
  const range = sel.getRangeAt(0), article = $('#article')
  if (!article || !article.contains(range.commonAncestorContainer)) return null
  const text = sel.toString().replace(/\s+/g, ' ').trim()
  if (text.length < 2) return null
  return { text: text.slice(0, 600), rect: range.getBoundingClientRect() }
}
function placeSelAsk() {
  const btn = $('#selAsk'); if (!btn) return
  const s = selectedText()
  if (!s) { btn.hidden = true; return }
  btn.hidden = false
  const w = btn.offsetWidth, h = btn.offsetHeight
  const x = Math.max(8, Math.min(innerWidth - w - 8, s.rect.left + s.rect.width / 2 - w / 2))
  // above the selection; below it when there is no room (and on phones, clear of the system menu)
  const above = s.rect.top - h - 10
  const y = above > ($('#hdr')?.offsetHeight || 64) + 4 && !matchMedia('(pointer: coarse)').matches ? above : s.rect.bottom + 12
  btn.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px)`
}
function mountSelAsk() {
  const btn = $('#selAsk'); if (!btn || PAGE.kind !== 'doc') return
  let t = 0
  const later = () => { clearTimeout(t); t = setTimeout(placeSelAsk, 120) }
  document.addEventListener('selectionchange', later)
  addEventListener('scroll', () => { if (!btn.hidden) placeSelAsk() }, { passive: true })
  // keep the selection: pressing the button must not clear it
  btn.addEventListener('mousedown', (e) => e.preventDefault())
  btn.addEventListener('click', () => {
    const s = selectedText(); if (!s) return
    setQuote(s.text)
    getSelection()?.removeAllRanges(); btn.hidden = true
    openAsk()
  })
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

// ---------- screenshots: click to see full size ----------
function openShot(img) {
  const box = document.createElement('div')
  box.className = 'lightbox'
  box.setAttribute('role', 'dialog')
  box.setAttribute('aria-label', img.alt || '截图')
  box.innerHTML = `<img src="${img.currentSrc || img.src}" alt="">`
  const close = () => { box.remove(); document.removeEventListener('keydown', onKey) }
  const onKey = (e) => { if (e.key === 'Escape') close() }
  box.addEventListener('click', close)
  document.addEventListener('keydown', onKey)
  document.body.append(box)
}
document.addEventListener('click', (e) => {
  const img = e.target.closest?.('figure .shot img')
  if (img) openShot(img)
})

// ---------- diagrams: archify viewers follow the site theme ----------
function loadDiagrams() {
  $$('iframe[data-diagram]').forEach((f) => { const src = `${f.dataset.diagram}?embed=1&theme=${isDark() ? 'dark' : 'light'}`; if (f.getAttribute('src') !== src) f.setAttribute('src', src) })
}

// ---------- developer docs: the admin check ----------
async function devGate() {
  const msg = $('#gateMsg'), actions = $('#gateActions')
  const token = await freshToken()
  if (!token) {
    msg.textContent = '请先用平台管理员账号登录知是。'
    actions.innerHTML = `<a class="btn btn-primary" href="${signInUrl()}">登录</a><a class="btn btn-secondary" href="/docs/">回到使用文档</a>`
    return
  }
  const res = await fetch('/api/docs/dev-access', { method: 'POST', headers: { Authorization: `Bearer ${token}` }, credentials: 'same-origin' }).catch(() => null)
  if (res?.ok) { location.reload(); return }
  msg.textContent = res?.status === 403 ? '你的账号不是平台管理员。开发文档写给维护这个平台的人；需要访问请联系平台管理员。' : '暂时无法确认你的身份，稍后再试。'
  actions.innerHTML = `<a class="btn btn-secondary" href="/docs/">回到使用文档</a>`
}

// ---------- events ----------
document.addEventListener('click', (e) => {
  const t = e.target.closest('[data-open-search],[data-open-ask],[data-close-ask],[data-new-chat],[data-menu],[data-copy-page],[data-copy],[data-f],[data-sug],[data-quote-clear],.side a[href^="#"]')
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
    $$('[data-f]').forEach((b) => b.classList.toggle('on', b === t))
    $$('.item').forEach((i) => i.classList.toggle('hide', f !== 'all' && i.dataset.t !== f))
    $$('.day').forEach((d) => d.classList.toggle('hide', !d.querySelector('.item:not(.hide)')))
  } else if (t.matches('[data-quote-clear]')) { setQuote(''); $('#askInput')?.focus() }
  else if (t.matches('.side a[href^="#"]')) { $$('.side a[href^="#"]').forEach((a) => a.classList.toggle('on', a === t)); closeAll() }
})

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
$('#themeBtn').addEventListener('click', toggleTheme)
document.addEventListener('keydown', (e) => {
  const typing = /INPUT|TEXTAREA/.test(document.activeElement?.tagName || '')
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); openSearch() }
  else if (e.key === '/' && !typing) { e.preventDefault(); openSearch() }
  else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'i') { e.preventDefault(); $('#drawer').classList.contains('open') ? closeDock() : openAsk() }
  else if (e.key === 'Escape') closeAll()
})
$('#dockResize').addEventListener('pointerdown', (e) => {
  e.preventDefault(); document.body.classList.add('dragging')
  const move = (ev) => document.documentElement.style.setProperty('--dock-w', Math.max(320, Math.min(720, innerWidth - ev.clientX)) + 'px')
  const up = () => { document.body.classList.remove('dragging'); removeEventListener('pointermove', move); removeEventListener('pointerup', up) }
  addEventListener('pointermove', move); addEventListener('pointerup', up)
})
addEventListener('scroll', () => tocSpy?.(), { passive: true })

// ---------- start ----------
setupToc(); tocSpy?.()
mountDemos()
// One beacon per page load, and nothing else: 「有人来过」 is the only thing the
// docs can report that the server cannot see for itself. See src/visit.js for
// what is sent (two fields) and what is deliberately not.
recordVisit(PAGE)
loadDiagrams()
mountSelAsk()
if (PAGE.kind === 'dev-gate') devGate()

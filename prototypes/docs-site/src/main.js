import { ic, TAG, STEPS } from './content.js'
import { NAV, P, FAQ, RELEASES, WHO, LOGO } from 'virtual:data'
import { build, stageFor, BUBBLE } from 'virtual:motion'

const $ = (s, r = document) => r.querySelector(s)
const $$ = (s, r = document) => [...r.querySelectorAll(s)]
const app = $('#app')
const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches
const isDark = () => document.documentElement.classList.contains('dark')
const esc = (s) => s.replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[c])
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

/* ============================================================
   Pages
   ============================================================ */
const flat = (sec) => NAV[sec].groups.flatMap(([g, items]) => items.map((i) => ({ slug: i[0], title: i[1], group: g })))
const groupOf = (sec, page) => NAV[sec].groups.find(([, items]) => items.some((i) => i[0] === page))?.[0] ?? ''

function buildTabs() {
  const tabs = $('#tabs')
  tabs.insertAdjacentHTML('beforeend', Object.entries(NAV).map(([k, v]) => `<a class="tab" data-sec="${k}" href="#/${k}${v.first ? '/' + v.first : ''}">${ic(v.icon, 'width:15px;height:15px')}${v.label}${v.lock ? `<span class="lock" title="${v.lock}">${ic('lock', 'width:12px;height:12px')}</span>` : ''}</a>`).join(''))
}
function moveTabs(sec) {
  const ind = $('#tabInd')
  $$('.tab').forEach((t) => t.classList.toggle('on', t.dataset.sec === sec))
  const on = $(`.tab[data-sec="${sec}"]`)
  if (!on) { ind.classList.remove('on'); return }
  ind.style.left = on.offsetLeft + 'px'; ind.style.width = on.offsetWidth + 'px'; ind.classList.add('on')
}

function sideHtml(sec) {
  return `<div class="side-inner"><span class="side-pill" id="sidePill"></span>${NAV[sec].groups.map(([g, items]) => `<div class="side-group"><h4>${g}</h4>${items.map(([s, t, draft]) => `<a href="#/${sec}/${s}" data-slug="${s}">${t}${draft && sec === 'guide' ? '<span class="soon">待写</span>' : ''}</a>`).join('')}</div>`).join('')}</div>`
}
function moveSidePill(page) {
  const pill = $('#sidePill'); if (!pill) return
  $$('.side a[data-slug]').forEach((a) => a.classList.toggle('on', a.dataset.slug === page))
  const on = $(`.side a[data-slug="${page}"]`)
  if (!on) { pill.style.opacity = 0; return }
  pill.style.top = on.offsetTop + 'px'; pill.style.height = on.offsetHeight + 'px'; pill.style.opacity = 1
}

function articleHtml(sec, page) {
  const d = P[sec + '/' + page]
  const list = flat(sec), i = list.findIndex((x) => x.slug === page), prev = list[i - 1], next = list[i + 1]
  const tmp = document.createElement('div'); tmp.innerHTML = d.body
  $$(':scope > *', tmp).forEach((el, k) => el.style.setProperty('--k', Math.min(k, 14)))
  const fromRepo = d.src.startsWith('docs/manual/')
  return `${sec === 'dev' ? `<div class="admin-note">${ic('lock', 'width:14px;height:14px')}<span><b>仅管理员可见</b>从后台管理进入。这一栏按当前代码撰写，不沿用仓库 docs/ 下的旧文档。</span></div>` : ''}
    <div class="crumb">${NAV[sec].label}<span>/</span><b>${groupOf(sec, page)}</b>${d.draft ? '<span class="draft-tag">待写</span>' : ''}</div>
    <div class="page-head"><h1>${d.title}<span class="h1-line"></span></h1>
     <div class="copy-wrap"><div class="copy-btn"><button data-copy-page>${ic('copy', 'width:14px;height:14px')}复制本页</button><button data-menu aria-label="更多">${ic('down', 'width:14px;height:14px')}</button></div>
      <div class="menu" id="menu">
       <button data-copy-page>${ic('copy')}<span><b>复制本页</b><small>以 Markdown 格式复制，给大模型用</small></span></button>
       <button data-toast="会打开 /docs/${page}.md">${ic('md')}<span><b>查看 Markdown 原文</b><small>/docs/${page}.md</small></span></button>
       <button data-open-ask>${ic('chat')}<span><b>问芝士这一页</b><small>带着这一页的内容提问</small></span></button>
       <button data-toast="会打开 /docs/llms.txt">${ic('list')}<span><b>llms.txt</b><small>全站目录，给 AI 读的入口</small></span></button>
      </div></div></div>
    <p class="lede">${d.lede}</p>
    <div class="prose">${tmp.innerHTML}</div>
    <div class="helpful">这一页有帮助吗？<button data-vote>${ic('up', 'width:14px;height:14px')}有用</button><button data-vote>${ic('dn', 'width:14px;height:14px')}没用</button>${fromRepo ? `<a class="edit" href="#" data-toast="会打开 GitHub 编辑 ${d.src}">${ic('pen', 'width:14px;height:14px')}在 GitHub 上修改这一页</a>` : ''}</div>
    <div class="pager">${prev ? `<a class="prev spot" href="#/${sec}/${prev.slug}">上一页<b>${ic('arrow', 'transform:scaleX(-1)')}${prev.title}</b></a>` : '<span></span>'}${next ? `<a class="next spot" href="#/${sec}/${next.slug}">下一页<b>${next.title}${ic('arrow')}</b></a>` : ''}</div>
    <div class="updated">${d.draft ? '这一页还没写' : `最后更新于 ${d.updated} · 来源 ${d.src}`}</div>`
}
function tocHtml() {
  const heads = $$('.article .prose h2[id], .article .prose .step h3[id]').map((h) => [h.id, h.textContent.replace(/#$/, ''), h.tagName === 'H3'])
  return `<h5>本页内容</h5><div class="toc-track"><span class="toc-ind" id="tocInd"></span>${heads.map(([id, t, l3]) => `<a href="#${id}" data-toc="${id}"${l3 ? ' class="l3"' : ''}>${t}</a>`).join('')}</div>
   <div class="toc-extra"><a href="#" data-open-ask>${ic('chat', 'width:14px;height:14px')}问芝士这一页</a><a href="#" data-copy-page>${ic('copy', 'width:14px;height:14px')}复制为 Markdown</a></div>`
}
function docShell(sec) {
  return `<div class="layout"><aside class="side" id="side">${sideHtml(sec)}</aside><main class="main"><article class="article" id="article"></article></main><nav class="toc" id="toc"></nav></div>`
}
function fillDoc(sec, page) {
  $('#article').innerHTML = articleHtml(sec, page)
  $('#toc').innerHTML = tocHtml()
  $$('.article .code-bar').forEach((b) => b.insertAdjacentHTML('afterbegin', '<span class="lights"><i></i><i></i><i></i></span>'))
  moveSidePill(page)
}

function changelogPage() {
  const counts = { all: 0, feat: 0, imp: 0, fix: 0 }
  RELEASES.forEach((r) => Object.entries(r.hl).forEach(([t, it]) => { counts[t] += it.length; counts.all += it.length }))
  return `<div class="layout no-toc"><aside class="side" id="side"><div class="side-inner"><span class="side-pill" id="sidePill"></span>
   <div class="side-group"><h4>版本</h4>${RELEASES.map((r) => `<a href="#${r.id}" data-slug="${r.id}">${r.ver}<span class="soon">${r.date.slice(0, 10)}</span></a>`).join('')}</div>
   <div class="side-group"><h4>订阅</h4><a href="#" data-toast="会打开 RSS">${ic('rss', 'width:14px;height:14px')}RSS</a><a href="#" data-toast="会打开 GitHub Releases">${ic('git', 'width:14px;height:14px')}GitHub Releases</a></div></div></aside>
  <main class="main"><article class="article" id="article" style="max-width:900px">
   <div class="cl-hero"><div class="crumb"><b>更新日志</b></div><h1 class="chars" data-split>知是每一版变了什么</h1><span class="h1-line"></span>
    <p class="lede" style="margin-bottom:0">按版本列出你用得到的变化，写成人话；每一条都链到对应的 PR。完整的改动清单在每个版本末尾展开。</p>
    <div class="callout note" style="margin:20px 0 0">${ic('info')}<p>平台最近一个正式 release 是 <strong>0.17.0</strong>。「0.18.0」是按 9 月 23 日那次正式环境上线划出的<strong>建议版本号</strong>；以后每次上线正式环境都打一个 release，这里就按 release 自动生成。</p></div></div>
   <div class="filters" id="filters"><span class="f-ind" id="fInd"></span>${Object.entries({ all: '全部', ...TAG }).map(([k, v]) => `<button data-f="${k}"${k === 'all' ? ' class="on"' : ''}>${v}<span class="n">${counts[k]}</span></button>`).join('')}</div>
   <div class="timeline" id="timeline"><div class="tl-line"><div class="tl-fill" id="tlFill"></div></div>
   ${RELEASES.map((r) => `<section class="day rel-${r.id}" id="${r.id}"><div class="day-date"><b>${r.ver}</b><small>${r.date}</small><span class="env">${r.env}</span><span class="nums">${r.list.length} 项改动</span></div><div class="day-list">
     ${Object.entries(r.hl).flatMap(([t, it]) => it.map(([h, pr]) => [t, h, pr])).map(([t, h, pr], k) => `<div class="item" data-t="${t}" data-reveal style="--d:${k}"><h3><span class="badge ${t}">${TAG[t]}</span>${esc(h)}${pr ? `<a class="pr" href="#" data-toast="会打开 PR #${pr}">#${pr}</a>` : ''}</h3></div>`).join('')}
     <details class="all-changes"><summary>全部 ${r.list.length} 项改动</summary><ol>${r.list.map((x) => `<li><span>${esc(x.s)}</span><small>#${x.pr} · ${x.d}</small></li>`).join('')}</ol></details>
   </div></section>`).join('')}
   </div>
  </article></main></div>`
}

const guideCount = flat('guide').length, devCount = flat('dev').length
const written = Object.entries(P).filter(([k, d]) => k.startsWith('guide/') && !d.draft).length
const unreleased = RELEASES[0]
function homePage() {
  const feats = NAV.guide.groups.find(([g]) => g === '使用知是')[1]
  const who = Object.keys(WHO)
  return `<div class="home">
  <section class="stage">
   <div class="scene" aria-hidden="true"></div>
   <div class="hero-copy">
    <a class="badge-row" href="#/changelog"><b>最新</b>${unreleased.list.length} 项改动已在测试环境，等下一版正式发布 ${ic('arrow')}</a>
    <h1 class="hero-h1"><span class="line"><span>把一件事</span></span><span class="line"><span>交给<em>芝士</em></span></span></h1>
    <p class="hero-sub">知是 · Cheese 是你和 AI 队友一起做项目的地方。这里写着怎么开始、每个功能怎么用，以及遇到问题怎么办。</p>
    <div class="hero-in" style="--d:1"><button class="hero-search" data-open-search>${ic('search', 'width:18px;height:18px')}<span class="tw">搜索文档，或者直接问：怎么邀请同学进项目？</span><kbd>⌘K</kbd></button></div>
    <div class="hero-cta hero-in" style="--d:2"><a class="pill lg magnetic" href="#/guide/quickstart">快速开始 ${ic('arrow')}</a><a class="pill lg alt magnetic" href="#" data-open-ask>${ic('chat')} 问芝士</a></div>
    <div class="coords hero-in" style="--d:3"><span>知是 · CHEESE</span><span>文档 v2 · 2026.09</span><span>${guideCount + devCount} 篇 · 已写 ${written}</span></div>
   </div>
   <div class="moon-stage" id="moonStage">
    ${chartSvg()}
    <div class="cheese-art" id="heroLogo" role="img" aria-label="知是的标志：一轮带孔的芝士，前面站着一只小老鼠" title="点一下重播"></div>
   </div>
  </section>

  <div class="wrap">
   <div class="sec-head" data-reveal><span class="kick">按 身 份 开 始</span><h2>挑一件你现在就想做的事</h2><p>直接跳到对应的教程或说明，不用从头读。</p></div>
   <div class="who-tabs filters" id="whoTabs"><span class="f-ind" id="whoInd"></span>${who.map((k, i) => `<button data-who="${k}"${i ? '' : ' class="on"'}>${k}</button>`).join('')}</div>
   <section class="entry four" id="whoCards"></section>

   <div class="sec-head" data-reveal><span class="kick">一 件 事 怎 么 走 完</span><h2>不管做什么，都是这四步</h2><p>说清目标，它去做；你随时插话，最后验收。</p></div>
   <section class="flow">${STEPS.map(([t, d, mock], k) => `<div class="bx spot" data-reveal style="--d:${k}"><span class="flow-n">0${k + 1}</span><h3>${t}</h3><p>${d}</p><div class="mock">${mock}</div></div>`).join('')}</section>

   <div class="sec-head" data-reveal><span class="kick">按 功 能 查 找</span><h2>已经会用了，只想查一个功能</h2></div>
   <section class="feat-grid" data-reveal>${feats.map(([s, t, draft]) => `<a href="#/guide/${s}"><span>${t}</span>${draft ? '<small>待写</small>' : ic('arrow')}</a>`).join('')}</section>

   <div class="sec-head" data-reveal><span class="kick">常 见 问 题</span><h2>先看看是不是这几个</h2><p>来自<a class="link" href="#/guide/troubleshooting">常见问题与排障</a>。</p></div>
   <section class="faq" data-reveal>${FAQ.map((f) => `<details class="qa"><summary>${esc(f.q)}${ic('down')}</summary><div>${f.a} <a class="link" href="#/guide/troubleshooting#${f.id}">详细说明</a></div></details>`).join('')}</section>
  </div>

  <section class="cta-band">
   <h2 data-reveal>不知道从哪开始？<br>问芝士。</h2>
   <p data-reveal style="--d:1">用你自己的话描述想做的事，芝士会告诉你从哪一页、哪一步开始。</p>
   <div class="row" data-reveal style="--d:2"><a class="pill lg magnetic" href="#" data-open-ask>${ic('chat')} 问芝士</a><a class="pill lg alt magnetic" href="#/guide/quickstart">快速开始 ${ic('arrow')}</a></div>
   <div class="giant-wrap"><span class="giant">知是</span><img src="${LOGO}" alt=""></div>
  </section>
  </div>`
}
function renderWho(k) {
  $('#whoCards').innerHTML = WHO[k].map(([t, d, s], i) => `<a class="tilt spot" href="#/guide/${s}"><span class="num">0${i + 1}</span><div class="who">${esc(P['guide/' + s].title)}</div><b>${t}</b><p>${d}</p><span class="go">去看看 ${ic('arrow')}</span></a>`).join('')
  $$('#whoTabs button').forEach((b) => b.classList.toggle('on', b.dataset.who === k))
  const on = $('#whoTabs button.on'), ind = $('#whoInd')
  if (on && ind) { ind.style.left = on.offsetLeft + 'px'; ind.style.width = on.offsetWidth + 'px' }
  setupSpot()
}

function chartSvg() {
  const R = 290, ticks = []
  for (let a = 0; a < 360; a += 3) {
    const long = a % 30 === 0, r1 = R - (long ? 14 : 6), t = (a - 90) * Math.PI / 180
    ticks.push(`<line x1="${(Math.cos(t) * r1).toFixed(1)}" y1="${(Math.sin(t) * r1).toFixed(1)}" x2="${(Math.cos(t) * R).toFixed(1)}" y2="${(Math.sin(t) * R).toFixed(1)}"${long ? ' class="lg"' : ''}/>`)
    if (long) ticks.push(`<text x="${(Math.cos(t) * (R + 16)).toFixed(1)}" y="${(Math.sin(t) * (R + 16) + 3).toFixed(1)}">${String(a).padStart(3, '0')}</text>`)
  }
  const mark = (deg, label, sub, href) => {
    const t = (deg - 90) * Math.PI / 180, p = (r) => [(Math.cos(t) * r).toFixed(1), (Math.sin(t) * r).toFixed(1)]
    const [x, y] = p(R), [lx, ly] = p(R + 44), right = Math.cos(t) > 0, tx = (+lx + (right ? 8 : -8)).toFixed(1), anchor = right ? 'start' : 'end'
    return `<a href="${href}" class="mark"><circle cx="${x}" cy="${y}" r="4.5"/><circle class="ring-o" cx="${x}" cy="${y}" r="10"/><line class="lead" x1="${x}" y1="${y}" x2="${lx}" y2="${ly}"/><text class="lbl" x="${tx}" y="${(+ly - 2).toFixed(1)}" text-anchor="${anchor}">${label}</text><text class="sub" x="${tx}" y="${(+ly + 14).toFixed(1)}" text-anchor="${anchor}">${sub}</text></a>`
  }
  return `<svg class="chart" viewBox="-400 -400 800 800" aria-hidden="true">
   <circle class="draw" r="${R}" style="--len:${(2 * Math.PI * R).toFixed(0)}"/><circle class="draw dash" r="232" style="--len:1458"/><circle class="draw" r="172" style="--len:1081"/>
   <line class="axis" x1="-330" y1="0" x2="330" y2="0"/><line class="axis" x1="0" y1="-330" x2="0" y2="330"/>
   <g class="ticks">${ticks.join('')}</g>
   ${mark(-45, '使用文档', `${guideCount} 页`, '#/guide/quickstart')}${mark(45, '开发文档', `${devCount} 篇 · 仅管理员`, '#/dev/overview')}${mark(225, '更新日志', `${RELEASES.length} 个版本`, '#/changelog')}
  </svg>`
}
function drawChart() { requestAnimationFrame(() => $('#moonStage')?.classList.add('drawn')) }

/* The cheese logo, direction A「冒孔」: holes bubble up one by one, the mouse
   peeks out, then a quiet loop keeps it alive. It rests on the original logo. */
let heroRun = 0, heroVisible = true, heroIO
function mountHero() {
  const host = $('#heroLogo'); if (!host) return
  const run = ++heroRun
  host.innerHTML = build('hero')
  const svg = $('svg', host), S = stageFor(svg)
  let clock = 0, last = 0
  const tick = (now) => {
    if (run !== heroRun || !svg.isConnected) return
    if (heroVisible) {
      const dt = last ? Math.min(now - last, 64) : 0; clock += dt
      S.reset(); if (!reduced) BUBBLE.frame(S, Math.min(clock, BUBBLE.D), clock >= BUBBLE.D ? clock - BUBBLE.D : null)
    }
    last = heroVisible ? now : 0
    requestAnimationFrame(tick)
  }
  requestAnimationFrame(tick)
  heroIO?.disconnect()
  heroIO = new IntersectionObserver((es) => { heroVisible = es[0].isIntersecting })
  heroIO.observe(host)
}

function footer() {
  return `<footer class="foot"><div class="foot-row">
 <div><a class="brand" href="#/"><span class="brand-mark sm"><img src="${LOGO}" alt=""></span><span class="brand-word">知是<i class="dot">·</i>Cheese</span></a><div class="fine">和 AI 队友一起做项目的地方。<br>© 2026 SageSeekerSociety</div></div>
 <div><h6>文档</h6><a href="#/guide/quickstart">使用文档</a><a href="#/dev/overview">开发文档</a><a href="#/changelog">更新日志</a></div>
 <div><h6>产品</h6><a href="#" data-toast="会打开 okcheese.com">进入知是</a><a href="#" data-toast="会打开桌面端下载">桌面端下载</a><a href="#" data-toast="会打开服务条款">服务条款</a><a href="#" data-toast="会打开隐私政策">隐私政策</a></div>
 <div><h6>给 AI</h6><a href="#" data-toast="会打开 /docs/llms.txt">llms.txt</a><a href="#" data-toast="会打开 GitHub">GitHub</a><a href="#" data-open-ask>问芝士</a></div>
</div></footer>`
}

/* ============================================================
   Router with view transitions
   ============================================================ */
let cur = null
function parse() {
  const raw = location.hash.slice(1) || '/'
  const [path, anchor] = raw.split('#')
  const parts = path.split('/').filter(Boolean)
  let sec = parts[0] || '', page = parts[1] || ''
  if (sec && !NAV[sec]) sec = ''
  if (sec && sec !== 'changelog' && (!page || !P[sec + '/' + page])) page = NAV[sec].first
  return { sec, page, anchor }
}
function render() {
  const { sec, page, anchor } = parse()
  const sameSec = cur && cur.sec === sec && sec && sec !== 'changelog'
  const same = cur && cur.sec === sec && cur.page === page
  moveTabs(sec)
  $('#menuBtn').style.display = sec ? '' : 'none'
  if (!same) {
    if (sameSec) {
      fillDoc(sec, page)
    } else {
      app.innerHTML = (sec === '' ? homePage() : sec === 'changelog' ? changelogPage() : docShell(sec)) + footer()
      if (sec && sec !== 'changelog') fillDoc(sec, page)
      afterMount(sec)
    }
    scrollTo({ top: 0, behavior: 'instant' })
    setupToc(); setupReveal(); setupSpot(); setupMagnetic(); splitChars()
  }
  cur = { sec, page }
  $('#hdr').classList.toggle('solid', sec !== '' || scrollY > 20)
  document.body.classList.toggle('page-home', sec === '')
  const d = P[sec + '/' + page]; $('#ctxPage').textContent = sec === '' ? '首页' : sec === 'changelog' ? '更新日志' : d ? d.title : ''
  $('#side')?.classList.remove('open'); if (!(innerWidth <= 820 && $('#drawer').classList.contains('open'))) $('#scrim').classList.remove('open')
  if (anchor) {
    const el = document.getElementById(anchor)
    if (el) setTimeout(() => { el.scrollIntoView({ behavior: same ? 'smooth' : 'instant' }); el.classList.remove('flash'); void el.offsetWidth; el.classList.add('flash') }, same ? 0 : 60)
  }
}
function navigate() {
  const prev = cur
  render()
  if (reduced || !prev || (prev.sec === cur.sec && prev.page === cur.page)) return
  const el = prev.sec === cur.sec && cur.sec && cur.sec !== 'changelog' ? $('#article') : app
  el.classList.remove('enter'); void el.offsetWidth; el.classList.add('enter')
}
addEventListener('hashchange', navigate)

function afterMount(sec) {
  if (sec === '') { drawChart(); mountHero(); renderWho(Object.keys(WHO)[0]) }
  if (sec === 'changelog') { moveSidePill(RELEASES[0].id); moveFilter() }
}

/* ============================================================
   Motion helpers
   ============================================================ */
let io
function setupReveal() {
  io?.disconnect()
  io = new IntersectionObserver((es) => es.forEach((e) => {
    if (!e.isIntersecting) return
    e.target.classList.add('in')
    $$('[data-count]', e.target).forEach(countUp)
    io.unobserve(e.target)
  }), { threshold: 0.12, rootMargin: '0px 0px -40px 0px' })
  $$('[data-reveal]').forEach((el) => io.observe(el))
}
function countUp(el) {
  const to = +el.dataset.count, t0 = performance.now(), dur = 1400
  const step = (t) => { const k = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(2, -10 * k); el.textContent = Math.round(to * e); if (k < 1) requestAnimationFrame(step); else el.innerHTML = to + (to > 9 ? '<sup>+</sup>' : '') }
  requestAnimationFrame(step)
}
function splitChars() {
  let i = 0
  $$('[data-split]').forEach((el) => {
    if (el.dataset.done) return
    el.dataset.done = 1
    el.innerHTML = [...el.textContent].map((c) => c === ' ' ? ' ' : `<span class="ch" style="--i:${i++}">${esc(c)}</span>`).join('')
  })
}
function setupSpot() {
  $$('.spot').forEach((el) => {
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
  $$('.magnetic').forEach((el) => {
    if (el.dataset.mg) return
    el.dataset.mg = 1
    el.addEventListener('pointermove', (e) => { const r = el.getBoundingClientRect(); el.style.transform = `translate(${(e.clientX - r.left - r.width / 2) * 0.22}px,${(e.clientY - r.top - r.height / 2) * 0.3}px)` })
    el.addEventListener('pointerleave', () => { el.style.transform = '' })
  })
}

function setupToc() {
  const links = $$('[data-toc]'); const ind = $('#tocInd')
  const targets = links.map((l) => document.getElementById(l.dataset.toc)).filter(Boolean)
  const set = (id) => { links.forEach((l) => l.classList.toggle('on', l.dataset.toc === id)); const on = links.find((l) => l.dataset.toc === id); if (on && ind) { ind.style.top = on.offsetTop + 'px'; ind.style.height = on.offsetHeight + 'px' } }
  if (targets[0]) set(targets[0].id)
  setupToc.fn = () => { let best = targets[0]; for (const t of targets) if (t.getBoundingClientRect().top < 170) best = t; if (best) set(best.id) }
}
function onScroll() {
  const h = document.documentElement.scrollHeight - innerHeight
  $('#progress').style.transform = `scaleX(${h > 0 ? scrollY / h : 0})`
  $('#hdr').classList.toggle('solid', (cur && cur.sec !== '') || scrollY > 20)
  setupToc.fn?.()
  const tl = $('#timeline')
  if (tl) {
    const r = tl.getBoundingClientRect(), mid = innerHeight * 0.55
    $('#tlFill').style.setProperty('--p', Math.max(0, Math.min(1, (mid - r.top) / r.height)))
    $$('.item', tl).forEach((it) => it.classList.toggle('lit', it.getBoundingClientRect().top < mid))
  }
}
addEventListener('scroll', onScroll, { passive: true })

function moveFilter() {
  const on = $('#filters button.on'), ind = $('#fInd')
  if (on && ind) { ind.style.left = on.offsetLeft + 'px'; ind.style.width = on.offsetWidth + 'px' }
}

/* answers are assembled from the docs themselves */
const INDEX = []
for (const [sec, v] of Object.entries(NAV)) for (const [g, items] of v.groups) for (const [s, t] of items) {
  const d = P[sec + '/' + s]
  INDEX.push({ sec, label: v.label, title: t, group: g, href: `#/${sec}/${s}`, text: d ? d.lede : '', icon: v.icon, body: d ? d.body.replace(/<[^>]+>/g, ' ') : '' })
}
RELEASES.forEach((r) => Object.values(r.hl).forEach((it) => it.forEach(([h]) => INDEX.push({ sec: 'changelog', label: '更新日志', title: h, group: r.ver, href: '#/changelog#' + r.id, text: `${r.ver} · ${r.date}`, icon: 'log', body: h }))))
const WORDS = { 仓库: ['projects'], github: ['projects'], 采纳: ['accept'], 合并: ['accept'], 验收: ['accept'], 反应: ['agents'], '@': ['agents'], 电脑: ['devices'], 设备: ['devices'], 机器: ['devices'], 额度: ['quota'], 算力: ['quota'], 架构: ['overview'], 部署: ['topology'], 模型: ['llm', 'agents'], 计费: ['billing'], 团队: ['teams'], 邀请: ['teams'], 同学: ['teams'], 房间: ['rooms'], 话题: ['rooms'], 看板: ['tasks'], 任务: ['tasks'], 文件: ['files'], 下载: ['files'], 提交: ['submit'], 课程: ['courses'], 作业: ['courses'], 题目: ['spaces'], 网站: ['sites'] }
function answerFor(q) {
  const lq = q.toLowerCase()
  const hits = new Set()
  for (const [w, pages] of Object.entries(WORDS)) if (lq.includes(w)) pages.forEach((p) => hits.add(p))
  let docs = INDEX.filter((x) => x.sec !== 'changelog' && hits.has(x.href.split('/').pop()))
  docs.sort((a, b) => (P[a.href.slice(2)]?.draft ? 1 : 0) - (P[b.href.slice(2)]?.draft ? 1 : 0))
  if (!docs.length) docs = INDEX.filter((x) => x.sec !== 'changelog' && [...lq].some((c) => c.trim() && (x.title + x.body).includes(c))).slice(0, 2)
  const here = cur && P[cur.sec + '/' + cur.page]
  if (!docs.length && here && $('#ctxUse')?.checked) { const x = INDEX.find((i) => i.href === `#/${cur.sec}/${cur.page}`); if (x) docs = [x] }
  if (!docs.length) return { text: '文档里暂时没有讲到这一点。你可以在知是的房间里直接 @芝士 问，它能看到你的项目本身。', cites: [] }
  const main = docs[0]
  return { text: `这在「${main.title}」里讲得最清楚：${main.text}`, cites: docs.slice(0, 2) }
}
async function streamInto(el, text, speed = 24) {
  const caret = '<span class="stream-caret"></span>'
  for (let k = 1; k <= text.length && el.isConnected; k += 2) { el.innerHTML = esc(text.slice(0, k)) + caret; await sleep(speed) }
  el.textContent = text
}
const citeHtml = (c) => `<a class="cite" href="${c.href}">${ic('doc')}<span>${c.label} · ${c.title}</span><small>${c.href.replace('#/', '/docs/').replace(/^\/docs\/(guide|dev)\//, '/docs/')}</small></a>`

/* ============================================================
   Theme: a circle of night spreading from where you clicked
   ============================================================ */
function applyTheme(d) { document.documentElement.classList.toggle('dark', d); try { localStorage.setItem('docs-dark', d ? '1' : '0') } catch {} }
applyTheme((() => { try { return localStorage.getItem('docs-dark') === '1' } catch { return false } })())
function toggleTheme(x, y) {
  const to = !isDark()
  const restart = () => {}
  if (!document.startViewTransition || reduced) { applyTheme(to); restart(); return }
  document.documentElement.classList.add('vt-theme')
  const t = document.startViewTransition(() => applyTheme(to))
  t.ready.then(() => {
    const r = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y))
    document.documentElement.animate({ clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${r}px at ${x}px ${y}px)`] }, { duration: 900, easing: 'cubic-bezier(.7,0,.2,1)', pseudoElement: '::view-transition-new(root)' })
  })
  t.finished.finally(() => { document.documentElement.classList.remove('vt-theme'); restart() })
}
$('#themeBtn').addEventListener('click', (e) => { const r = e.currentTarget.getBoundingClientRect(); toggleTheme(r.left + r.width / 2, r.top + r.height / 2) })

/* ============================================================
   Search palette + ask drawer
   ============================================================ */
let sel = 0, hits = []
const hl = (s, q) => (q ? esc(s).split(esc(q)).join(`<mark>${esc(q)}</mark>`) : esc(s))
function doSearch() {
  const q = $('#q').value.trim()
  hits = q ? INDEX.filter((x) => (x.title + x.text + x.group).toLowerCase().includes(q.toLowerCase())) : INDEX.filter((x) => ['quickstart', 'working-with-cheese', 'accept', 'overview'].some((s) => x.href.endsWith('/' + s)) && x.sec !== 'changelog')
  sel = 0
  const by = {}; hits.forEach((h) => (by[h.label] ??= []).push(h))
  let n = 0
  $('#res').innerHTML = hits.length ? Object.entries(by).map(([l, hs]) => `<div class="grp">${q ? l : '常看的页面 · ' + l}</div>` + hs.map((h) => `<div class="hit${n === 0 ? ' on' : ''}" data-i="${n}" style="--k:${n++}"><span class="hi">${ic(h.icon)}</span><div style="min-width:0"><b>${hl(h.title, q)}</b><small>${hl(h.text || h.group, q)}</small></div><span class="go">${ic('arrow')}</span></div>`).join('')).join('') : `<div style="padding:30px;text-align:center;color:var(--faint);font-size:14px">文档里没找到「${esc(q)}」——按 ⌘↵ 问问芝士？</div>`
  $('#askTxt').textContent = q ? `问芝士：「${q}」` : '没找到？直接问芝士'
}
function openSearch() { $('#scrim').classList.add('open'); $('#palette').classList.add('open'); $('#q').value = ''; doSearch(); setTimeout(() => $('#q').focus(), 30) }
function closeAll() { ['#scrim', '#palette'].forEach((s) => $(s).classList.remove('open')); $('#menu')?.classList.remove('open'); $('#side')?.classList.remove('open'); if (innerWidth <= 820) closeDock() }
function closeDock() { $('#drawer').classList.remove('open'); document.body.classList.remove('docked'); $('#scrim').classList.remove('open') }
function toggleDock() { $('#drawer').classList.contains('open') ? closeDock() : openAsk() }
function go(i) { const h = hits[i]; if (!h) return; closeAll(); location.hash = h.href.slice(1) }
$('#q').addEventListener('input', doSearch)
$('#q').addEventListener('keydown', (e) => {
  const all = $$('.hit')
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); sel = (sel + (e.key === 'ArrowDown' ? 1 : -1) + all.length) % all.length; all.forEach((h, i) => h.classList.toggle('on', i === sel)); all[sel]?.scrollIntoView({ block: 'nearest' }) }
  if (e.key === 'Enter') { if (e.metaKey || e.ctrlKey) openAsk($('#q').value); else go(sel) }
})
$('#res').addEventListener('click', (e) => { const h = e.target.closest('.hit'); if (h) go(+h.dataset.i) })
$('#res').addEventListener('pointermove', (e) => { const h = e.target.closest('.hit'); if (h && +h.dataset.i !== sel) { sel = +h.dataset.i; $$('.hit').forEach((x, i) => x.classList.toggle('on', i === sel)) } })
$('#askRow').onclick = () => openAsk($('#q').value)

let greeted = false
const SUGGEST = ['怎么邀请同学进项目？', '采纳和合并是一回事吗？', '能用我自己的电脑跑芝士吗？']
function openAsk(q) {
  $('#palette').classList.remove('open'); $('#drawer').classList.add('open')
  if (innerWidth <= 820) $('#scrim').classList.add('open'); else { $('#scrim').classList.remove('open'); document.body.classList.add('docked') }
  if (!greeted) {
    greeted = true
    $('#chat').innerHTML = `<div class="a"><span class="brand-mark sm"><img src="${LOGO}" alt=""></span><div class="body"><p>你好，我是芝士。关于知是怎么用，问我就行——我只根据这份文档回答，并告诉你出自哪一节。</p></div></div>`
    $('#suggest').innerHTML = SUGGEST.map((s) => `<button data-sug>${s}</button>`).join('')
  }
  if (q && q.trim()) ask(q.trim())
  setTimeout(() => $('#askInput').focus(), 300)
}
async function ask(q) {
  const c = $('#chat')
  $('#suggest').innerHTML = ''
  c.insertAdjacentHTML('beforeend', `<div class="q">${esc(q)}</div><div class="a"><span class="brand-mark sm"><img src="${LOGO}" alt=""></span><div class="body"><span class="typing"><i></i><i></i><i></i></span></div></div>`)
  c.scrollTop = c.scrollHeight
  const body = $$('.a .body', c).pop()
  await sleep(800)
  const a = answerFor(q)
  body.innerHTML = '<p></p>'
  await streamInto($('p', body), a.text)
  body.insertAdjacentHTML('beforeend', a.cites.map(citeHtml).join(''))
  c.scrollTop = c.scrollHeight
}
$('#askSend').onclick = () => { const v = $('#askInput').value.trim(); if (v) { ask(v); $('#askInput').value = '' } }
$('#askInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('#askSend').click() })

/* ============================================================
   Clicks and keys
   ============================================================ */
let tt
function toast(m) { const t = $('#toast'); t.textContent = m; t.classList.add('show'); clearTimeout(tt); tt = setTimeout(() => t.classList.remove('show'), 1900) }

document.addEventListener('click', (e) => {
  const a = e.target.closest('a[href^="#"]')
  if (a) {
    const h = a.getAttribute('href')
    if (h === '#') e.preventDefault()
    else if (!h.startsWith('#/')) {
      e.preventDefault()
      const el = document.getElementById(h.slice(1))
      if (el) { history.replaceState(null, '', location.hash.split('#').slice(0, 2).join('#') + h); el.scrollIntoView({ behavior: 'smooth' }); el.classList.remove('flash'); void el.offsetWidth; el.classList.add('flash') }
      return
    } else if (a.closest('.cite') && innerWidth <= 820) closeDock()
  }
  const t = e.target.closest('[data-open-search],[data-open-ask],[data-close-ask],[data-new-chat],[data-toast],[data-menu],[data-copy-page],[data-copy],[data-vote],[data-f],[data-sug],[data-who],#heroLogo,.code-tab')
  if (!t) { if (!e.target.closest('.menu')) $('#menu')?.classList.remove('open'); return }
  if (t.matches('[data-open-search]')) { e.preventDefault(); openSearch() }
  else if (t.matches('[data-open-ask]')) { e.preventDefault(); $('#menu')?.classList.remove('open'); if (t.closest('.hdr')) toggleDock(); else openAsk() }
  else if (t.matches('[data-close-ask]')) closeDock()
  else if (t.matches('[data-new-chat]')) { greeted = false; openAsk() }
  else if (t.matches('[data-sug]')) ask(t.textContent)
  else if (t.matches('[data-menu]')) $('#menu').classList.toggle('open')
  else if (t.matches('[data-copy-page]')) { e.preventDefault(); $('#menu')?.classList.remove('open'); const d = P[cur.sec + '/' + cur.page]; if (d) navigator.clipboard?.writeText(`# ${d.title}\n\n${d.lede}\n`).catch(() => {}); toast('已复制本页 Markdown') }
  else if (t.matches('[data-copy]')) { const pre = t.closest('.code').querySelector('pre'); navigator.clipboard?.writeText(pre.innerText).catch(() => {}); t.classList.add('done'); t.innerHTML = ic('check'); setTimeout(() => { t.classList.remove('done'); t.innerHTML = ic('copy') }, 1400) }
  else if (t.matches('[data-vote]')) { t.parentElement.querySelectorAll('[data-vote]').forEach((b) => b.classList.remove('picked')); t.classList.add('picked'); toast('谢谢，已记下') }
  else if (t.matches('[data-f]')) {
    const f = t.dataset.f
    $$('[data-f]').forEach((b) => b.classList.toggle('on', b === t)); moveFilter()
    $$('.item').forEach((i) => i.classList.toggle('hide', f !== 'all' && i.dataset.t !== f))
    $$('.day').forEach((d) => d.classList.toggle('hide', !d.querySelector('.item:not(.hide)')))
    onScroll()
  }
  else if (t.matches('[data-who]')) renderWho(t.dataset.who)
  else if (t.matches('#heroLogo')) mountHero()
  else if (t.matches('.code-tab')) t.parentElement.querySelectorAll('.code-tab').forEach((x) => x.classList.toggle('on', x === t))
  else if (t.matches('[data-toast]')) { e.preventDefault(); toast(t.dataset.toast + '（预览里不跳转）') }
})
$('#scrim').onclick = () => { closeAll(); closeDock() }
$('#menuBtn').onclick = () => { $('#side')?.classList.add('open'); $('#scrim').classList.add('open') }
document.addEventListener('keydown', (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); openSearch() }
  else if (e.key === '/' && !/INPUT|TEXTAREA/.test(document.activeElement.tagName)) { e.preventDefault(); openSearch() }
  else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'i') { e.preventDefault(); toggleDock() }
  else if (e.key === 'Escape') closeAll()
})
addEventListener('resize', () => { moveTabs(cur?.sec ?? ''); moveFilter(); if (cur?.page) moveSidePill(cur.page); const w = $('#whoTabs button.on'); if (w) renderWho(w.dataset.who) })

$('#dockResize').addEventListener('pointerdown', (e) => {
  e.preventDefault(); document.body.classList.add('dragging')
  const move = (ev) => document.documentElement.style.setProperty('--dock-w', Math.max(320, Math.min(720, innerWidth - ev.clientX)) + 'px')
  const up = () => { document.body.classList.remove('dragging'); removeEventListener('pointermove', move); removeEventListener('pointerup', up) }
  addEventListener('pointermove', move); addEventListener('pointerup', up)
})

buildTabs()
render()
document.fonts?.ready.then(() => moveTabs(cur?.sec ?? ''))

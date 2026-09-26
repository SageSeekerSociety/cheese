// Server-side templates: every page of the site is rendered here at build time,
// so each URL is a complete HTML document. src/app.js only adds behaviour.
import { ic, TAG, STEPS } from './content.js'

export const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c])
export const REPO = 'https://github.com/SageSeekerSociety/cheese'

// ---------- shell ----------
export function shell(ctx, { title, description, section, bodyClass = '', main, pageData }) {
  const { site, assets } = ctx
  const tabs = site.tabs.map((t) => `<a class="tab${t.key === section ? ' on' : ''}" data-sec="${t.key}" href="${t.href}">${ic(t.icon, 'width:15px;height:15px')}${esc(t.label)}${t.lock ? `<span class="lock" title="仅管理员">${ic('lock', 'width:12px;height:12px')}</span>` : ''}</a>`).join('')
  return `<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(title)}</title>
<meta name="description" content="${esc(description || site.description)}">
<link rel="icon" href="${assets.logo}">
<link rel="alternate" type="application/rss+xml" title="知是更新日志" href="/docs/changelog.xml">
<link rel="stylesheet" href="${assets.css}">
<style>@font-face{font-family:"Display Kai";src:url(${assets.display}) format("woff2");font-display:swap}</style>
<script>try{if(localStorage.getItem('docs-dark')==='1'||(localStorage.getItem('docs-dark')===null&&matchMedia('(prefers-color-scheme: dark)').matches))document.documentElement.classList.add('dark')}catch(e){}</script>
</head>
<body class="${bodyClass}" data-sec="${esc(section)}">
<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs>${ctx.grads}</defs></svg>
<a class="skip" href="#main">跳到正文</a>
<div class="progress" id="progress"></div>
<div class="ambient" aria-hidden="true"><i class="glow"></i><i class="grid"></i><i class="stars"></i><i class="grain"></i></div>
<header class="hdr" id="hdr">
  <div class="hdr-row">
    <button class="icon-btn menu-btn" id="menuBtn" aria-label="打开导航">${ic('list')}</button>
    <a class="brand" href="/docs/" aria-label="知是 · Cheese 文档首页">
      <span class="brand-mark"><img src="${assets.logo}" alt=""></span>
      <span class="brand-word">知是<i class="dot">·</i>Cheese</span><span class="sep"></span><span class="sub">文档</span>
    </a>
    <nav class="tabs" id="tabs" aria-label="文档分区"><span class="tab-ind" id="tabInd"></span>${tabs}</nav>
    <div class="hdr-actions">
      <button class="search-btn" data-open-search>${ic('search')}<span>搜索</span><kbd>⌘K</kbd></button>
      <button class="ghost-btn" data-open-ask>${ic('chat')}<span>问芝士</span></button>
      <button class="icon-btn" id="themeBtn" aria-label="切换深浅色"><span class="theme-ico">${ic('sun').replace('class="ico"', 'class="ico sun"')}${ic('moon').replace('class="ico"', 'class="ico moon"')}</span></button>
      <a class="pill hide-sm" href="/"><span>进入知是</span>${ic('arrow', 'width:14px;height:14px')}</a>
    </div>
  </div>
</header>
<div id="main">${main}</div>
${footer(ctx)}
<div class="scrim" id="scrim"></div>
<div class="palette" id="palette" role="dialog" aria-label="搜索文档">
  <div class="pin">${ic('search')}<input id="q" placeholder="搜索文档，比如「采纳」「设备」「额度」" autocomplete="off"><kbd>esc</kbd></div>
  <div class="res" id="res"></div>
  <div class="askrow" id="askRow"><span class="spark-ico">${ic('chat')}</span><span id="askTxt">没找到？直接问芝士</span><kbd>⌘ ↵</kbd></div>
  <div class="foot-k"><span><kbd>↑</kbd><kbd>↓</kbd> 选择</span><span><kbd>↵</kbd> 打开</span><span><kbd>esc</kbd> 关闭</span></div>
</div>
<aside class="drawer" id="drawer" aria-label="问芝士">
  <div class="dock-resize" id="dockResize" title="拖动调整宽度"></div>
  <div class="drawer-h">
    <span class="brand-mark sm"><img src="${assets.logo}" alt=""></span>
    <div style="flex:1;line-height:1.3"><b>问芝士</b><br><small>只根据这份文档回答，每条答案都附出处</small></div>
    <button class="icon-btn" data-new-chat aria-label="新对话" title="新对话">${ic('pen')}</button>
    <button class="icon-btn" data-close-ask aria-label="收起" title="收起（⌘I）">${ic('arrow')}</button>
  </div>
  <div class="ctx" id="ctx">${ic('doc')}<span>正在看</span><b id="ctxPage">${esc(title)}</b><label><input type="checkbox" id="ctxUse" checked>带上这一页</label></div>
  <div class="drawer-b" id="chat" aria-live="polite"></div>
  <div class="suggest" id="suggest"></div>
  <div class="drawer-f"><form class="box" id="askForm"><input id="askInput" maxlength="500" placeholder="问一个关于知是的问题…" autocomplete="off"><button class="send" id="askSend" aria-label="发送">${ic('arrow')}</button></form><small id="askHint">回答由 AI 依据这份文档生成，可能有误，以原文为准。问题会被记录，用于改进文档。</small></div>
</aside>
<div class="toast" id="toast" role="status"></div>
<script type="application/json" id="page-data">${JSON.stringify(pageData || {}).replace(/</g, '\\u003c')}</script>
<script type="module" src="${assets.js}"></script>
</body>
</html>`
}

export function footer(ctx) {
  const { site, assets } = ctx
  return `<footer class="foot"><div class="foot-row">
 <div><a class="brand" href="/docs/"><span class="brand-mark sm"><img src="${assets.logo}" alt=""></span><span class="brand-word">知是<i class="dot">·</i>Cheese</span></a><div class="fine">和 AI 队友一起做项目的地方。<br>© 2026 SageSeekerSociety</div></div>
 <div><h6>文档</h6>${site.userSections.map((s) => `<a href="${s.href}">${esc(s.label)}</a>`).join('')}<a href="/docs/dev/overview">开发文档</a><a href="/docs/changelog">更新日志</a></div>
 <div><h6>产品</h6><a href="/">进入知是</a><a href="/docs/download">桌面端与连接器</a><a href="/legal/terms">服务条款</a><a href="/legal/privacy">隐私政策</a><a href="/feedback">帮助与反馈</a></div>
 <div><h6>给 AI 与开发者</h6><a href="/docs/llms.txt">llms.txt</a><a href="/docs/manual.zip">下载全部文档（Markdown）</a><a href="/docs/changelog.xml">更新日志 RSS</a><a href="${REPO}" rel="noopener">GitHub</a></div>
</div></footer>`
}

// ---------- doc pages ----------
function sidebar(nav, current, base) {
  return `<aside class="side" id="side" aria-label="本栏目录"><div class="side-inner"><span class="side-pill" id="sidePill"></span>${nav.map(([g, items]) => `<div class="side-group"><h4>${esc(g)}</h4>${items.map((p) => `<a href="${base}${p.slug}" data-slug="${p.slug}"${p.slug === current ? ' class="on" aria-current="page"' : ''}>${esc(p.title)}</a>`).join('')}</div>`).join('')}</div></aside>`
}

function toc(page) {
  if (!page.toc.length) return '<nav class="toc" id="toc"></nav>'
  return `<nav class="toc" id="toc" aria-label="本页内容"><h5>本页内容</h5><div class="toc-track"><span class="toc-ind" id="tocInd"></span>${page.toc.map((h) => `<a href="#${h.id}" data-toc="${h.id}"${h.level === 3 ? ' class="l3"' : ''}>${esc(h.text)}</a>`).join('')}</div>
   <div class="toc-extra"><a href="#" data-open-ask>${ic('chat', 'width:14px;height:14px')}问芝士这一页</a><a href="${page.mdUrl}" data-copy-page>${ic('copy', 'width:14px;height:14px')}复制为 Markdown</a></div></nav>`
}

function devMeta(page) {
  if (!page.kind) return ''
  const covers = (page.covers || []).map((c) => `<a href="${REPO}/tree/main/${c}" rel="noopener"><code>${esc(c)}</code></a>`).join('')
  return `<div class="dev-meta"><span class="kind kind-${esc(page.kindKey)}">${esc(page.kind)}</span>${page.generated ? '<span class="gen">由代码自动生成</span>' : ''}${covers ? `<div class="covers"><small>涉及代码</small>${covers}</div>` : ''}</div>`
}

export function docPage(ctx, page, nav, prev, next) {
  const dev = page.section === 'dev'
  const base = dev ? '/docs/dev/' : '/docs/'
  const edit = page.src && !page.generated ? `<a class="edit" href="${REPO}/edit/main/${page.src}" rel="noopener">${ic('pen', 'width:14px;height:14px')}在 GitHub 上修改这一页</a>` : ''
  const main = `<div class="layout">${sidebar(nav, page.slug, base)}<main class="main"><article class="article" id="article">
    ${dev ? `<div class="admin-note">${ic('lock', 'width:14px;height:14px')}<span><b>仅管理员可见</b>这一栏按当前代码撰写，写给改这个仓库的人和 agent。</span></div>` : ''}
    <div class="crumb">${esc(page.sectionLabel)}<span>/</span><b>${esc(page.group)}</b></div>
    <div class="page-head"><h1>${esc(page.title)}<span class="h1-line"></span></h1>
     <div class="copy-wrap"><div class="copy-btn"><button data-copy-page>${ic('copy', 'width:14px;height:14px')}复制本页</button><button data-menu aria-label="更多">${ic('down', 'width:14px;height:14px')}</button></div>
      <div class="menu" id="menu">
       <button data-copy-page>${ic('copy')}<span><b>复制本页</b><small>以 Markdown 格式复制，给大模型用</small></span></button>
       <a href="${page.mdUrl}">${ic('md')}<span><b>查看 Markdown 原文</b><small>${esc(page.mdUrl)}</small></span></a>
       <button data-open-ask>${ic('chat')}<span><b>问芝士这一页</b><small>带着这一页的内容提问</small></span></button>
       <a href="${dev ? '/docs/dev/llms.txt' : '/docs/llms.txt'}">${ic('list')}<span><b>llms.txt</b><small>全部目录，给 AI 读的入口</small></span></a>
      </div></div></div>
    ${devMeta(page)}
    ${page.lede ? `<p class="lede">${page.lede}</p>` : ''}
    <div class="prose">${page.html}</div>
    <div class="helpful">${edit}</div>
    <div class="pager">${prev ? `<a class="prev spot" href="${prev.url}">上一页<b>${ic('arrow', 'transform:scaleX(-1)')}${esc(prev.title)}</b></a>` : '<span></span>'}${next ? `<a class="next spot" href="${next.url}">下一页<b>${esc(next.title)}${ic('arrow')}</b></a>` : ''}</div>
    <div class="updated">${page.updated ? `最后更新于 ${esc(page.updated)}` : ''}${page.src ? ` · 来源 <code>${esc(page.src)}</code>` : ''}</div>
  </article></main>${toc(page)}</div>`
  return shell(ctx, {
    title: `${page.title} · 知是 · Cheese 文档`,
    description: page.summary,
    section: page.section,
    main,
    pageData: { kind: 'doc', section: page.section, slug: page.slug, title: page.title, md: page.mdUrl, dev },
  })
}

// ---------- changelog ----------
export function changelogPage(ctx, releases) {
  const counts = { all: 0, feat: 0, imp: 0, fix: 0 }
  releases.forEach((r) => Object.entries(r.hl).forEach(([t, it]) => { counts[t] += it.length; counts.all += it.length }))
  const main = `<div class="layout no-toc"><aside class="side" id="side"><div class="side-inner"><span class="side-pill" id="sidePill"></span>
   <div class="side-group"><h4>版本</h4>${releases.map((r, i) => `<a href="#${r.id}" data-slug="${r.id}"${i ? '' : ' class="on"'}>${esc(r.ver)}<span class="soon">${esc(r.date.slice(0, 10))}</span></a>`).join('')}</div>
   <div class="side-group"><h4>订阅</h4><a href="/docs/changelog.xml">${ic('rss', 'width:14px;height:14px')}RSS</a><a href="${REPO}/releases" rel="noopener">${ic('git', 'width:14px;height:14px')}GitHub Releases</a></div></div></aside>
  <main class="main"><article class="article" id="article" style="max-width:900px">
   <div class="cl-hero"><div class="crumb"><b>更新日志</b></div><h1 class="chars" data-split>知是每一版变了什么</h1><span class="h1-line"></span>
    <p class="lede" style="margin-bottom:0">按版本列出你用得到的变化，写成人话；每一条都链到对应的 PR。完整的改动清单在每个版本末尾展开。</p></div>
   <div class="filters" id="filters"><span class="f-ind" id="fInd"></span>${Object.entries({ all: '全部', ...TAG }).map(([k, v]) => `<button data-f="${k}"${k === 'all' ? ' class="on"' : ''}>${v}<span class="n">${counts[k]}</span></button>`).join('')}</div>
   <div class="timeline" id="timeline"><div class="tl-line"><div class="tl-fill" id="tlFill"></div></div>
   ${releases.map((r) => `<section class="day rel-${r.id}" id="${r.id}"><div class="day-date"><b>${esc(r.ver)}</b><small>${esc(r.date)}</small><span class="env">${esc(r.env)}</span><span class="nums">${r.list.length} 项改动</span></div><div class="day-list">
     ${Object.entries(r.hl).flatMap(([t, it]) => it.map(([h, pr]) => [t, h, pr])).map(([t, h, pr], k) => `<div class="item" data-t="${t}" data-reveal style="--d:${k}"><h3><span class="badge ${t}">${TAG[t]}</span>${esc(h)}${pr ? `<a class="pr" href="${REPO}/pull/${pr}" rel="noopener">#${pr}</a>` : ''}</h3></div>`).join('')}
     <details class="all-changes"><summary>全部 ${r.list.length} 项改动</summary><ol>${r.list.map((x) => `<li><span>${esc(x.s)}</span><small><a href="${REPO}/pull/${x.pr}" rel="noopener">#${x.pr}</a> · ${x.d}</small></li>`).join('')}</ol></details>
   </div></section>`).join('')}
   </div>
  </article></main></div>`
  return shell(ctx, { title: '更新日志 · 知是 · Cheese 文档', description: '知是每一版变了什么，按版本列出，每条链到 PR。', section: 'changelog', main, pageData: { kind: 'changelog' } })
}

export function changelogFeed(releases) {
  const items = releases.flatMap((r) => Object.values(r.hl).flat().filter(([, pr]) => pr).map(([h, pr]) => ({ h, pr, ver: r.ver, date: r.list.find((x) => x.pr === String(pr))?.d || r.date })))
  const rfc = (d) => new Date(`${d.slice(0, 10)}T00:00:00+08:00`).toUTCString()
  return `<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>知是更新日志</title><link>https://okcheese.com/docs/changelog</link><description>知是 · Cheese 每一版变了什么</description><language>zh-CN</language>
${items.map((x) => `<item><title>${esc(x.h)}</title><link>${REPO}/pull/${x.pr}</link><guid isPermaLink="false">cheese-pr-${x.pr}</guid><category>${esc(x.ver)}</category><pubDate>${rfc(x.date)}</pubDate></item>`).join('\n')}
</channel></rss>
`
}

// ---------- download ----------
export function downloadPage(ctx, desktop) {
  const card = (os, sub, files, note) => `<div class="dl-card"><div class="dl-os">${os}</div><small>${sub}</small><div class="dl-files">${files.map(([label, file]) => `<a class="pill" href="${desktop.base}/${file}" rel="noopener">${ic('down', 'width:14px;height:14px')}${label}</a>`).join('')}</div><p>${note}</p></div>`
  const main = `<div class="layout no-toc"><aside class="side" id="side"><div class="side-inner"><span class="side-pill" id="sidePill"></span><div class="side-group"><h4>下载</h4><a href="#desktop" data-slug="desktop" class="on">桌面端</a><a href="#connector" data-slug="connector">连接器</a></div></div></aside>
  <main class="main"><article class="article" id="article" style="max-width:900px">
   <div class="crumb"><b>下载</b></div><h1>桌面端与连接器<span class="h1-line"></span></h1>
   <p class="lede">桌面端让你在自己的电脑上用知是，登录后这台电脑就能成为芝士干活的设备。只想把一台服务器接进来，装连接器就够了。</p>
   <div class="prose">
    <h2 id="desktop">桌面端<a class="anchor" href="#desktop">#</a></h2>
    <div class="dl-grid">
     ${card('macOS', 'Apple 芯片（M 系列）', [['下载 .dmg', 'Cheese-arm64.dmg']], '打开 .dmg，把知是拖进「应用程序」。')}
     ${card('macOS', 'Intel 芯片', [['下载 .dmg', 'Cheese-x64.dmg']], '打开 .dmg，把知是拖进「应用程序」。')}
     ${card('Windows', 'x64', [['下载安装程序', 'Cheese-Setup-x64.exe']], '运行安装程序，按提示完成。')}
    </div>
    <p>桌面端有新版本时会在后台自动下载，下次打开就是新的。所有版本见 <a class="link" href="${REPO}/releases/tag/desktop-latest" rel="noopener">GitHub 发布页</a>。</p>
    <h2 id="connector">连接器<a class="anchor" href="#connector">#</a></h2>
    <p>连接器是一个命令行程序：装在服务器或没有图形界面的电脑上，登录后平台就能在这台机器上替芝士干活。安装和接入步骤见<a class="link" href="/docs/devices#devices">设备与运行环境</a>。</p>
    <div class="code"><div class="code-bar"><span class="code-tab on">登录并保持连接</span><button class="copy" data-copy aria-label="复制">${ic('copy')}</button></div><pre><span class="k">cheese</span> auth login
<span class="k">cheese</span> link auto-connect
<span class="k">cheese</span> status</pre></div>
   </div>
  </article></main></div>`
  return shell(ctx, { title: '下载 · 知是 · Cheese 文档', description: '下载知是桌面端（macOS、Windows）和连接器。', section: 'download', main, pageData: { kind: 'download' } })
}

// ---------- dev gate ----------
export function devGatePage(ctx) {
  const main = `<div class="gate"><div class="gate-card">
    <span class="brand-mark"><img src="${ctx.assets.logo}" alt=""></span>
    <h1>开发文档仅对平台管理员开放</h1>
    <p id="gateMsg">正在确认你的身份…</p>
    <div class="gate-actions" id="gateActions"></div>
  </div></div>`
  return shell(ctx, { title: '开发文档 · 知是 · Cheese 文档', description: '开发文档仅对平台管理员开放。', section: 'dev', main, pageData: { kind: 'dev-gate' } })
}

export function redirectPage(to) {
  return `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>已移动</title><meta http-equiv="refresh" content="0; url=${to}"><link rel="canonical" href="${to}"><script>location.replace(${JSON.stringify(to)}+location.hash)</script></head><body><a href="${to}">这一页已经移到新地址</a></body></html>`
}

export function notFoundPage(ctx) {
  const main = `<div class="gate"><div class="gate-card"><h1>没有找到这一页</h1><p>它可能已经改名或移动了。试试搜索，或者问芝士。</p><div class="gate-actions"><button class="pill" data-open-search>${ic('search')}搜索文档</button><a class="pill alt" href="/docs/">回到文档首页</a></div></div></div>`
  return shell(ctx, { title: '没有找到这一页 · 知是 · Cheese 文档', section: '', main, pageData: { kind: '404' } })
}

export { ic, STEPS }

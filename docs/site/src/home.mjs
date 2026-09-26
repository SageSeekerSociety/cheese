// The home page, rendered at build time. Interactive parts (logo motion, the
// pinned room story and the 我是…我要… pickers) are wired up by src/app.js from
// the same data, passed in the page's JSON.
import { esc, shell, ic, REPO } from './render.mjs'
import { TAG } from './content.js'

export const WORK = [
  ['学生', '周二 21:40', '作业', '@芝士 帮我看懂《数据结构》第三题到底要什么，一起把代码写完，再按题目要求提交。', ['题目', '话题文件', '云机器'], '讲清了题意和两个边界条件；代码跑过 12 个样例；已按题目格式提交。', ['main.c', '提交记录'], 'student-tutorial'],
  ['老师 / 助教', '周五 16:05', '批改', '@芝士 按评分标准把这周 86 份作业初评一遍，挑出需要我复核的，再列出大家普遍卡住的地方。', ['课程', '提交记录', '评分标准'], '86 份初评完成，11 份标为需复核；最常见的问题是递归没有终止条件（23 份）。', ['初评表.xlsx', '讲评提纲.md'], 'teacher-tutorial'],
  ['办公', '周一 09:12', '周报', '@芝士 把这三份会议纪要整理成一页周报，按项目分节，结尾列待办，下午三点前要。', ['文件', '实况文档'], '一页周报，4 个项目、9 条待办，每条都标了负责人和出处。', ['周报.docx'], 'office-tutorial'],
  ['团队项目', '周三 14:30', '开发', '@芝士 把首页改成新设计，手机上也要好看，开 PR 等我验收。', ['仓库', '预览', '设备'], '改了 6 个文件，手机和桌面各截了图；检查全过，验收卡已递给你。', ['PR #214', '预览链接'], 'accept'],
]
export const TOOLI = { 题目: 'doc', 话题文件: 'folder', 云机器: 'cpu', 课程: 'book', 提交记录: 'list', 评分标准: 'check', 文件: 'folder', 实况文档: 'doc', 仓库: 'git', 预览: 'layers', 设备: 'cpu' }

// The room is the workbench's own components (island/), pinned while the page
// scrolls; each quarter of the section's scroll adds the next thing that happens.
export const STORY = [
  ['交代一件事', '在话题里说清要什么、给谁、什么时候要。@ 芝士，它就接手。'],
  ['拆成几件事同时做', '芝士复述它的理解，把活拆成几条任务并行推进，每条都有进度。'],
  ['交付，当场预览', '做出来的文件直接出现在话题里，右侧就能打开看；你可以随时补一句要求。'],
  ['改好，等你验收', '它按你的补充改完，递上验收：采纳就合进项目，不满意就退回。'],
]
export const ROLE_ICON = { 学生: 'book', '老师 / 助教': 'users', 办公: 'folder', 团队项目: 'git' }

// What one of these people hands over and gets back: a worked example per role.
export function workPanel(who, pages) {
  const [role, time, label, task, tools, result, files, to] = WORK.find(([r]) => r === who)
  const target = pages[to]
  return `<div class="w-task"><div class="w-meta"><span>${time}</span><span class="w-tag">${label}</span><span class="w-model">${ic('spark', 'width:12px;height:12px')}Claude Opus</span></div>
    <p class="w-text">${esc(task).replace('@芝士', '<span class="mention">@芝士</span>')}</p>
    <div class="w-tools">${tools.map((t) => `<span>${ic(TOOLI[t] || 'doc', 'width:13px;height:13px')}${t}</span>`).join('')}</div></div>
   <div class="w-result"><div class="w-av"><img src="${pages.__logo}" alt=""></div><div><small>芝士交回来</small><p>${esc(result)}</p><div class="w-files">${files.map((f) => `<span>${ic(f.startsWith('PR') ? 'git' : 'doc', 'width:13px;height:13px')}${esc(f)}</span>`).join('')}</div>
    <a class="link" href="${target.url}">${esc(role)}怎么做：${esc(target.title)} →</a></div></div>`
}

export function pickHtml(id, label, options, selected, open) {
  return `<button class="x-pick-btn" data-pick="${id}" aria-haspopup="listbox" aria-expanded="${open === id}">${esc(label)}${ic('down')}</button>
   <div class="x-menu${open === id ? ' open' : ''}" role="listbox">${options.map(([v, t, d, icon], i) => `<button role="option" data-pick-opt="${id}" data-v="${esc(v)}" class="${String(v) === String(selected) ? 'on' : ''}" style="--i:${i}"><span class="x-menu-ic">${ic(icon)}</span><span><b>${esc(t)}</b><small>${esc(d)}</small></span></button>`).join('')}</div>`
}

export function sayHtml(who, what, WHO, pages, open = '') {
  const roles = Object.keys(WHO).map((k) => [k, k, WHO[k].map((x) => x[0]).slice(0, 2).join('、') + '…', ROLE_ICON[k] || 'users'])
  const jobs = WHO[who].map(([t, d, s], i) => [i, t, d, s.endsWith('tutorial') ? 'bulb' : 'doc'])
  const [t, d, s] = WHO[who][what]
  const target = pages[s]
  return {
    work: workPanel(who, pages),
    who: pickHtml('who', who, roles, who, open),
    what: pickHtml('what', WHO[who][what][0], jobs, what, open),
    out: `<div class="x-say-card"><small>${esc(target.sectionLabel)} · ${esc(target.title)}</small><b>${esc(t)}</b><p>${esc(d)}</p><a class="pill" href="${target.url}">看看怎么做 ${ic('arrow')}</a></div>`,
  }
}

// The docs' own pages, as a reader first sees them (shots/site.mjs).
const site = (slug, phone) => `/docs/images/site/${phone ? 'm-' : ''}${slug.replace('/', '-')}.jpg`

// One line per door: what that part of the docs is for, and the real screen
// that shows it (docs/manual/public/images, taken by shots/shots.mjs).
const DOORS = {
  start: ['十分钟上手：建项目、开话题，把第一件事交给芝士，再验收它交回来的东西。', site('quickstart')],
  tutorials: ['按身份把一件事从头走到尾：学生交作业、老师开课、办公协作。', site('student-tutorial')],
  features: ['每个功能是什么、在哪、怎么用、有什么限制，按用途分组。', site('rooms')],
  faq: ['芝士没回复、机器没连上、额度用完……遇到问题先看这里，每条都写了怎么处理。', site('troubleshooting')],
}

// The pages people come to the docs for most.
const POPULAR = ['quickstart', 'accept', 'agents', 'devices', 'quota', 'troubleshooting']

// 问芝士 walks a reader through the kinds of docs, one question per screen:
// [kind label, icon, question, answer, page (slug, `dev/…`, or `changelog`)]
const TOUR = [
  ['开始使用', 'rocket', '第一次用，该从哪开始？', '先建一个项目，在话题里把第一件事说清楚交给芝士。它做完会递一张验收卡，你采纳，改动就进了项目。整个过程十分钟能走完一遍，从《快速开始》读起就行。', 'quickstart'],
  ['教程', 'bulb', '我是学生，怎么交作业？', '加入老师的空间，找到题目并领取，从题目建一个项目和芝士一起做，最后按题目要求提交。《学生：从题目到提交》把这五步从头走到尾。', 'student-tutorial'],
  ['功能说明', 'layers', '验收和采纳是什么？', '芝士交活时会递一张验收卡：写着改了什么、推荐谁审。审过点「采纳」就合并进项目主线，不满意就「退回」并写明原因。', 'accept'],
  ['常见问题', 'info', '提示「机器未配置或未连接」怎么办？', '说明这个话题现在没有可用的运行设备。先在「我的设备」看设备是否在线，再核对话题选的运行环境；常见问题里每条都写了怎么处理。', 'troubleshooting'],
  ['更新日志', 'tag', '最近改了什么？', '', 'changelog'],
  ['开发文档', 'code', '一条消息在后台是怎么变成一轮的？', '消息先落库，再看有没有点名 AI 队友；同一话题的轮串行，跑着的时候新消息直接送进正在运行的会话。这部分在开发文档里，只对平台管理员开放。', 'dev/turn'],
]


function tourPage(slug, { pages, dev, latest }) {
  if (slug === 'changelog') return { url: '/docs/changelog', title: latest.ver, label: '更新日志' }
  if (slug.startsWith('dev/')) { const p = dev[slug.slice(4)]; return p && { url: p.url, title: p.title, label: '开发文档', locked: true } }
  const p = pages[slug]; return p && { url: p.url, title: p.title, label: p.sectionLabel }
}

function tourAnswer([, , , answer, slug], latest) {
  if (slug !== 'changelog') return answer
  const n = latest.list.length
  return `${latest.ver}（${latest.env}）一共 ${n} 项改动。更新日志把每一版改了什么写成人话，每条都链到对应的代码改动，也能用 RSS 订阅。`
}

export function homePage(ctx, { releases, faq, WHO, doors, pages, dev }) {
  const latest = releases[0]
  const news = ['feat', 'imp'].flatMap((t) => (latest.hl[t] || []).map(([h, pr]) => [t, h, pr])).slice(0, 5)
  const firstWho = Object.keys(WHO)[0]
  const say = sayHtml(firstWho, 0, WHO, pages)
  const count = doors.reduce((n, d) => n + d.items.length, 0)
  const features = doors.find((d) => d.key === 'features')?.items || []
  const steps = TOUR.map(([kind, icon, q, a, slug]) => ({ kind, icon, q, slug, page: tourPage(slug, { pages, dev, latest }) }))
    .filter((t) => t.page).map((t) => ({ ...t, a: tourAnswer(TOUR.find((x) => x[4] === t.slug), latest) }))
  const main = `<div class="home x">
  <section class="x-hero">
   <div class="scene" aria-hidden="true"></div>
   <div class="x-copy">
   <a class="badge-row" href="/docs/changelog"><b>${esc(latest.ver)}</b>${latest.list.length} 项改动 · 看看变了什么 ${ic('arrow')}</a>
   <span class="x-kick">知是 · Cheese 使用文档</span>
   <h1 class="x-title display"><span class="line"><span>照着做，</span></span><span class="line"><span class="d-word">就做得到。</span></span></h1>
   <p class="x-sub">从建第一个项目，到把芝士做出来的成果合进主线：每一步都写清楚在哪点、会看到什么。卡住了，直接问芝士。</p>
   <div class="x-cta"><a class="pill lg magnetic" href="/docs/quickstart">快速开始 ${ic('arrow')}</a><button class="x-search" data-open-search>${ic('search', 'width:17px;height:17px')}<span>搜索文档，或直接问</span><kbd>⌘K</kbd></button></div>
   <div class="d-popular"><small>常看</small>${POPULAR.filter((s) => pages[s]).map((s) => `<a href="${pages[s].url}">${esc(pages[s].title)}</a>`).join('')}</div>
   <p class="d-count">${count} 篇使用文档 · 每一页都有 Markdown 原文 · 跟着代码一起更新</p>
   </div>
   <div class="x-mark" id="heroLogo" role="img" aria-label="知是的标志：一轮带孔的芝士，前面站着一只小老鼠" title="点一下重播"><img src="${ctx.assets.logo}" alt=""></div>
  </section>

  <section class="tour" id="tour" style="--n:${steps.length}">
   <div class="x-head tour-head" data-reveal><span class="x-kick">问芝士，带你逛一遍文档</span><h2 class="display">不知道该看哪一页？<br>问就行。</h2><p>往下滚，每一屏问一个问题：芝士回答，再把你带到对应的那类文档。</p></div>
   ${steps.map((_, i) => `<i class="tour-snap" style="--i:${i}" aria-hidden="true"></i>`).join('')}
   <i class="tour-anchor" aria-hidden="true"></i>
   <div class="tour-pin">
    <div class="tour-stage">
     <div class="tour-browser">
      <div class="tb-bar"><span class="tb-dots"><i></i><i></i><i></i></span><span class="tb-url" id="tourUrl">okcheese.com/docs/</span><span class="tb-load" id="tourLoad"></span></div>
      <div class="tb-view">
       <div class="tb-page on blank" data-i="-1"><div class="tb-blank"><img src="${ctx.assets.logo}" alt=""><p>问一个问题，芝士带你去对应的那一页</p></div></div>
       ${steps.map((t, i) => `<a class="tb-page${t.page.locked ? ' locked' : ''}" data-i="${i}" href="${t.page.url}" tabindex="-1"><img src="${site(t.slug)}" alt="${esc(t.page.title)}" loading="lazy">${t.page.locked ? `<span class="tb-lock">${ic('lock', 'width:16px;height:16px')}开发文档只对平台管理员开放</span>` : ''}</a>`).join('')}
      </div>
     </div>
     <div class="tour-kinds" id="tourKinds">${steps.map((t, i) => `<button data-tour="${i}">${ic(t.icon, 'width:14px;height:14px')}${esc(t.kind)}</button>`).join('')}</div>
    </div>
    <aside class="tour-chat">
     <div class="tour-chat-h"><span class="brand-mark sm"><img src="${ctx.assets.logo}" alt=""></span><div><b>问芝士</b><small>只根据这份文档回答，每条答案都附出处</small></div></div>
     <div class="tc-log" id="tourLog" aria-live="polite"></div>
     <button class="tc-input" data-open-ask><span id="tourTyping" data-placeholder="问一个关于知是的问题…">问一个关于知是的问题…</span><span class="tc-send">${ic('arrow')}</span></button>
    </aside>
   </div>
   <ol class="tour-list">${steps.map((t) => `<li data-reveal><div class="q">${esc(t.q)}</div><div class="a">${esc(t.a)}</div>
    <a class="tl-page${t.page.locked ? ' locked' : ''}" href="${t.page.url}"><span class="tl-frame"><img src="${site(t.slug, true)}" alt="" loading="lazy"></span><span class="cite">${ic('doc', 'width:13px;height:13px')}${esc(t.page.label)} · ${esc(t.page.title)}</span></a></li>`).join('')}</ol>
   <script type="application/json" id="tourData">${JSON.stringify(steps.map((t) => ({ q: t.q, a: t.a, url: t.page.url, title: t.page.title, label: t.page.label }))).replace(/</g, '\\u003c')}</script>
  </section>

  <section class="x-sec">
   <div class="x-head" data-reveal><h2 class="display">知是能做的，都写在这里</h2><p>功能说明里的每一页：它是什么、在哪、怎么用。</p></div>
   <div class="wall">${features.map((p, k) => {
     const shot = site(p.slug)
     return `<a class="wall-item${shot ? ' has-shot' : ''}${k < 2 ? ' big' : ''}" href="${p.url}" data-reveal style="--d:${k % 6}">
      ${shot ? `<div class="wall-shot"><img src="${shot}" alt="" loading="lazy"></div>` : ''}
      <div class="wall-body"><small>${esc(p.group)}</small><b>${esc(p.title)}</b><p>${esc(p.summary || '')}</p><span class="go">阅读 ${ic('arrow', 'width:13px;height:13px')}</span></div>
     </a>`
   }).join('')}</div>
  </section>

  <section class="x-sec d-doors-sec">
   <div class="x-head" data-reveal><h2 class="display">文档分四块</h2><p>先上手，再按身份走一遍，用到哪个功能查哪个，卡住了看常见问题。</p></div>
   <div class="d-doors">${doors.map((d, k) => {
     const [line, img] = DOORS[d.key] || ['', null]
     return `<div class="d-door${img ? '' : ' no-shot'}" data-reveal style="--d:${k}">
    ${img ? `<a class="d-door-shot" href="${d.items[0].url}" tabindex="-1" aria-hidden="true"><img src="${img}" alt="" loading="lazy"></a>` : `<div class="d-door-art" aria-hidden="true">${ic(d.icon)}</div>`}
    <div class="d-door-body">
     <a class="x-door-head" href="${d.items[0].url}"><span class="x-door-ic">${ic(d.icon)}</span><b>${esc(d.label)}</b><small>${d.items.length} 篇</small></a>
     <p>${esc(line)}</p>
     <div class="x-door-list">${d.items.slice(0, 4).map((p) => `<a href="${p.url}">${esc(p.title)}${ic('arrow', 'width:13px;height:13px')}</a>`).join('')}</div>
    </div></div>`
   }).join('')}</div>
  </section>

  <section class="x-sec x-say" data-reveal>
   <div class="x-head"><h2 class="display">按你的身份找</h2><p>挑一个身份和一件事，看它交出去、交回来是什么样，再去读对应的那一页。</p></div>
   <p class="x-sentence">我是 <span class="x-pick" id="pickWho">${say.who}</span>，<br>我要 <span class="x-pick" id="pickWhat">${say.what}</span>。</p>
   <div class="x-work" id="workPanel">${say.work}</div>
   <div class="x-say-out" id="sayOut">${say.out}</div>
  </section>

  <section class="x-sec" data-reveal>
   <div class="x-head"><h2 class="display">文档还能这样用</h2><p>不想翻，就问；想交给自己的 AI 助手，就给它原文。</p></div>
   <div class="d-ways">
    <button class="d-way" data-open-ask>${ic('chat', 'width:20px;height:20px')}<b>问芝士</b><p>在这里直接问，芝士只根据文档回答，每条答案都附出处。</p><span class="go">打开 ${ic('arrow', 'width:13px;height:13px')}</span></button>
    <a class="d-way" href="${pages.agents ? pages.agents.url + '#summon' : '/docs/'}">${ic('spark', 'width:20px;height:20px')}<b>在话题里问</b><p>在知是的话题里问「这个怎么用」，芝士会先查文档再回答，并给你链接。</p><span class="go">怎么叫芝士 ${ic('arrow', 'width:13px;height:13px')}</span></a>
    <a class="d-way" href="/docs/llms.txt"><code>llms.txt</code><b>给 AI 读</b><p>全站目录，给模型的入口；任何一页加上 .md 就是原文，整本也能打包下载（manual.zip）。</p><span class="go">打开目录 ${ic('arrow', 'width:13px;height:13px')}</span></a>
    <a class="d-way" href="/docs/changelog">${ic('tag', 'width:20px;height:20px')}<b>跟上变化</b><p>每一版改了什么，写成人话，每条链到代码改动；可以用 RSS 订阅。</p><span class="go">更新日志 ${ic('arrow', 'width:13px;height:13px')}</span></a>
   </div>
  </section>

  <section class="x-sec x-news" data-reveal>
   <div class="x-news-head"><span class="x-kick">最近更新</span><h2>${esc(latest.ver)}<small>${esc(latest.env)}</small></h2>
    <div class="x-cta"><a class="pill alt" href="/docs/changelog">完整更新日志 ${ic('arrow')}</a><a class="link" href="/docs/changelog.xml">RSS 订阅</a></div></div>
   <ul class="x-news-list">${news.map(([t, h, pr]) => `<li><span class="badge ${t}">${TAG[t]}</span><span>${esc(h)}</span>${pr ? `<a class="pr" href="${REPO}/pull/${pr}" rel="noopener">#${pr}</a>` : ''}</li>`).join('')}</ul>
  </section>

  <section class="x-sec">
   <div class="x-head" data-reveal><h2>常见问题</h2></div>
   <div class="h-faq" data-reveal>${faq.map((f) => `<details><summary>${esc(f.q)}<span class="h-plus"></span></summary><div>${f.a} <a class="link" href="/docs/troubleshooting#${f.id}">详细说明</a></div></details>`).join('')}</div>
  </section>

  <section class="x-end" data-reveal>
   <h2 class="display">没找到想要的？</h2>
   <div class="x-cta" style="justify-content:center"><button class="pill lg magnetic" data-open-ask>${ic('chat')} 问芝士</button><a class="pill lg alt magnetic" href="/docs/feedback">提反馈</a></div>
   <p class="x-end-sub">第一次用？从<a class="link" href="/docs/quickstart">快速开始</a>读起；想在自己电脑上用，<a class="link" href="/docs/download">下载桌面端</a>。</p>
  </section>
  </div>`
  return shell(ctx, { title: '知是 · Cheese 文档', section: 'home', bodyClass: 'page-home', main, pageData: { kind: 'home' } })
}

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

// One line per door: what that part of the docs is for, and the real screen
// that shows it (docs/manual/public/images, taken by shots/shots.mjs).
const DOORS = {
  start: ['十分钟上手：建项目、开话题，把第一件事交给芝士，再验收它交回来的东西。', '/docs/images/room.jpg'],
  tutorials: ['按身份把一件事从头走到尾：学生交作业、老师开课、办公协作。', '/docs/images/task-card.jpg'],
  features: ['每个功能是什么、在哪、怎么用、有什么限制，按用途分组。', '/docs/images/board.jpg'],
  faq: ['芝士没回复、机器没连上、额度用完……遇到问题先看这里，每条都写了怎么处理。', null],
}

// The pages people come to the docs for most.
const POPULAR = ['quickstart', 'accept', 'agents', 'devices', 'quota', 'troubleshooting']

// 问芝士 walks a reader through the kinds of docs, one question per screen:
// [kind label, icon, question, answer, page (slug, `dev/…`, or `changelog`), screenshot]
const TOUR = [
  ['开始使用', 'rocket', '第一次用，该从哪开始？', '先建一个项目，在话题里把第一件事说清楚交给芝士。它做完会递一张验收卡，你采纳，改动就进了项目。整个过程十分钟能走完一遍。', 'quickstart', '/docs/images/room.jpg'],
  ['教程', 'bulb', '学生怎么交作业？', '加入老师的空间，找到题目并领取，从题目建一个项目和芝士一起做，最后按题目要求提交。教程把这五步从头走到尾。', 'student-tutorial', '/docs/images/m-room.jpg'],
  ['功能说明', 'layers', '验收和采纳是什么？', '芝士交活时会递一张验收卡：写着改了什么、推荐谁审。审过点「采纳」就合并进项目主线，不满意就「退回」并写明原因。', 'accept', '/docs/images/task-card.jpg'],
  ['常见问题', 'info', '提示「机器未配置或未连接」怎么办？', '说明这个话题现在没有可用的运行设备。先在「我的设备」看设备是否在线，再核对话题选的运行环境。', 'troubleshooting', '/docs/images/devices.jpg'],
  ['更新日志', 'tag', '最近改了什么？', '', 'changelog', null],
  ['开发文档', 'code', '一条消息在后台是怎么变成一轮的？', '消息先落库，再看有没有点名 AI 队友；同一话题的轮串行，跑着的时候新消息直接送进正在运行的会话。开发文档只对平台管理员开放。', 'dev/turn', null],
]

// Screens for the features wall, where there is one.
const FEATURE_SHOT = {
  rooms: '/docs/images/room.jpg', tasks: '/docs/images/board.jpg', accept: '/docs/images/task-card.jpg',
  files: '/docs/images/library.jpg', devices: '/docs/images/devices.jpg', teams: '/docs/images/teams.jpg',
  agents: '/docs/images/settings.jpg', projects: '/docs/images/work-home.jpg', feedback: '/docs/images/feedback.jpg',
}

function tourCard([kind, icon, , , slug, img], i, { pages, dev, latest }) {
  const locked = slug.startsWith('dev/')
  const page = slug === 'changelog'
    ? { url: '/docs/changelog', title: '更新日志', summary: `${latest.ver} · ${latest.env}` }
    : locked ? dev[slug.slice(4)] : pages[slug]
  if (!page) return ''
  const news = slug === 'changelog'
    ? `<ul class="t-news">${['feat', 'imp'].flatMap((t) => (latest.hl[t] || []).map(([h]) => [t, h])).slice(0, 5).map(([t, h]) => `<li><span class="badge ${t}">${TAG[t]}</span>${esc(h)}</li>`).join('')}</ul>`
    : ''
  return `<article class="t-card${i ? '' : ' on'}${locked ? ' locked' : ''}" data-i="${i}">
   <div class="t-card-top"><span class="t-kind">${ic(icon, 'width:14px;height:14px')}${esc(kind)}</span>${locked ? `<span class="t-lock">${ic('lock', 'width:12px;height:12px')}仅平台管理员</span>` : ''}</div>
   <h3>${esc(page.title)}</h3>
   <p>${esc(page.summary || '')}</p>
   ${img ? `<div class="t-shot${img.includes('/m-') ? ' phone' : ''}"><img src="${img}" alt="" loading="lazy"></div>` : news || `<div class="t-lines" aria-hidden="true"><i></i><i></i><i></i><i></i></div>`}
   <a class="pill alt" href="${page.url}">打开这一页 ${ic('arrow')}</a>
  </article>`
}

function tourAnswer([, , , answer, slug], latest) {
  if (slug !== 'changelog') return answer
  const n = latest.list.length
  return `${latest.ver}（${latest.env}）一共 ${n} 项改动，挑了几条你用得到的放在右边，每条都链到对应的代码改动。`
}

export function homePage(ctx, { releases, faq, WHO, doors, pages, dev }) {
  const latest = releases[0]
  const news = ['feat', 'imp'].flatMap((t) => (latest.hl[t] || []).map(([h, pr]) => [t, h, pr])).slice(0, 5)
  const firstWho = Object.keys(WHO)[0]
  const say = sayHtml(firstWho, 0, WHO, pages)
  const count = doors.reduce((n, d) => n + d.items.length, 0)
  const features = doors.find((d) => d.key === 'features')?.items || []
  const main = `<div class="home x">
  <section class="d-hero">
   <div class="scene" aria-hidden="true"></div>
   <div class="d-float" aria-hidden="true"><img class="f1" src="/docs/images/room.jpg" alt=""><img class="f2" src="/docs/images/board.jpg" alt=""><img class="f3" src="/docs/images/task-card.jpg" alt=""></div>
   <a class="badge-row" href="/docs/changelog"><b>${esc(latest.ver)}</b>${latest.list.length} 项改动 · 看看变了什么 ${ic('arrow')}</a>
   <h1 class="d-title display"><span class="d-mark"><img src="${ctx.assets.logo}" alt=""></span><span class="d-word">知是文档</span></h1>
   <p class="d-sub">怎么用知是，都在这里：从建第一个项目，到把芝士做出来的成果合进主线。</p>
   <button class="d-search" data-open-search aria-label="搜索文档">${ic('search', 'width:20px;height:20px')}<span>搜索文档，或者直接问芝士</span><kbd>⌘K</kbd></button>
   <div class="d-popular"><small>常看</small>${POPULAR.filter((s) => pages[s]).map((s) => `<a href="${pages[s].url}">${esc(pages[s].title)}</a>`).join('')}</div>
   <p class="d-count">${count} 篇使用文档 · 每一页都有 Markdown 原文 · 跟着代码一起更新</p>
  </section>

  <section class="tour" id="tour" style="--n:${TOUR.length}">
   <div class="tour-pin">
    <div class="tour-left">
     <span class="x-kick">问芝士，带你逛一遍文档</span>
     <h2 class="display">不知道该看哪一页？<br>问就行。</h2>
     <div class="tour-kinds" id="tourKinds">${TOUR.map(([kind, icon], i) => `<button data-tour="${i}"${i ? '' : ' class="on"'}>${ic(icon, 'width:14px;height:14px')}${esc(kind)}</button>`).join('')}</div>
     <div class="tour-chat" aria-live="polite">
      <div class="tour-chat-h"><span class="brand-mark sm"><img src="${ctx.assets.logo}" alt=""></span><div><b>问芝士</b><small>只根据这份文档回答，每条答案都附出处</small></div></div>
      ${TOUR.map((t, i) => `<div class="tour-qa${i ? '' : ' on'}" data-i="${i}"><div class="q">${esc(t[2])}</div><div class="a" data-full="${esc(tourAnswer(t, latest))}">${esc(tourAnswer(t, latest))}</div><div class="cite">${ic('doc', 'width:13px;height:13px')}${esc(t[0])}</div></div>`).join('')}
      <button class="tour-ask" data-open-ask>${ic('chat')}<span>你也问一个</span>${ic('arrow')}</button>
     </div>
    </div>
    <div class="tour-right">${TOUR.map((t, i) => tourCard(t, i, { pages, dev, latest })).join('')}</div>
   </div>
  </section>

  <section class="x-sec">
   <div class="x-head" data-reveal><h2 class="display">知是能做的，都写在这里</h2><p>功能说明里的每一页：它是什么、在哪、怎么用。</p></div>
   <div class="wall">${features.map((p, k) => {
     const shot = FEATURE_SHOT[p.slug]
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

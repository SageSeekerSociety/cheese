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

// One line per door: what that part of the docs is for.
const DOORS = {
  start: '十分钟上手：建项目、开话题，把第一件事交给芝士。',
  tutorials: '按身份把一件事从头走到尾：学生交作业、老师开课、办公协作。',
  features: '每个功能是什么、怎么用、有什么限制，按用途分组。',
  faq: '遇到报错或卡住时先看这里，每一条都写了怎么处理。',
}

export function homePage(ctx, { releases, faq, WHO, doors, pages }) {
  const latest = releases[0]
  const news = ['feat', 'imp'].flatMap((t) => (latest.hl[t] || []).map(([h, pr]) => [t, h, pr])).slice(0, 5)
  const firstWho = Object.keys(WHO)[0]
  const say = sayHtml(firstWho, 0, WHO, pages)
  const main = `<div class="home x">
  <section class="x-hero">
   <div class="scene" aria-hidden="true"></div>
   <div class="x-copy">
   <a class="badge-row" href="/docs/changelog"><b>${esc(latest.ver)}</b>${latest.list.length} 项改动已在测试环境 ${ic('arrow')}</a>
   <h1 class="x-title"><span class="line"><span>交给芝士一件事，</span></span><span class="line"><span>不只是问一句。</span></span></h1>
   <p class="x-sub">知是 · Cheese 是你和 AI 队友一起做项目的地方：交代目标，它去做，你随时插话，最后验收。这里是它的全部说明。</p>
   <div class="x-cta"><a class="pill lg magnetic" href="/docs/quickstart">快速开始 ${ic('arrow')}</a><button class="x-search" data-open-search>${ic('search', 'width:17px;height:17px')}<span>搜索文档，或直接问</span><kbd>⌘K</kbd></button></div>
   </div>
   <div class="x-mark" id="heroLogo" role="img" aria-label="知是的标志：一轮带孔的芝士，前面站着一只小老鼠" title="点一下重播"><img src="${ctx.assets.logo}" alt=""></div>
  </section>

  <section class="x-story" id="story" style="--n:${STORY.length}">
   <div class="x-story-pin">
    <div class="x-story-copy">
     <span class="x-kick">一件事怎么走完</span>
     <h2>往下滚，看芝士把一件事做完</h2>
     <ol class="x-steps" id="storySteps">${STORY.map(([t, d], i) => `<li${i ? '' : ' class="on"'}><span class="n">0${i + 1}</span><div><b>${t}</b><p>${d}</p></div></li>`).join('')}</ol>
     <div class="x-story-bar" id="storyBar"><i></i></div>
    </div>
    <div class="x-stage"><div class="x-blob b1"></div><div class="x-blob b2"></div><div class="room-fit" id="roomFit"><iframe id="roomFrame" title="话题演示：用工作台自己的组件拼成" data-src="${ctx.assets.room}" loading="lazy"></iframe></div></div>
   </div>
  </section>

  <section class="x-sec">
   <div class="x-head" data-reveal><h2>从这里开始</h2><p>文档分四块：先上手，再按身份走一遍，用到哪个功能查哪个，卡住了看常见问题。</p></div>
   <div class="x-doors">${doors.map((d, k) => `<div class="x-door" data-reveal style="--d:${k}">
    <a class="x-door-head" href="${d.items[0].url}"><span class="x-door-ic">${ic(d.icon)}</span><b>${esc(d.label)}</b><small>${d.items.length} 篇</small></a>
    <p>${esc(DOORS[d.key])}</p>
    <div class="x-door-list">${d.items.slice(0, 4).map((p) => `<a href="${p.url}">${esc(p.title)}${ic('arrow', 'width:13px;height:13px')}</a>`).join('')}</div></div>`).join('')}</div>
  </section>

  <section class="x-sec x-say" data-reveal>
   <div class="x-head"><h2>按你的身份</h2><p>挑一个身份和一件事，看它交出去、交回来是什么样，再去读对应的那一页。</p></div>
   <p class="x-sentence">我是 <span class="x-pick" id="pickWho">${say.who}</span>，<br>我要 <span class="x-pick" id="pickWhat">${say.what}</span>。</p>
   <div class="x-work" id="workPanel">${say.work}</div>
   <div class="x-say-out" id="sayOut">${say.out}</div>
  </section>

  <section class="x-sec x-news" data-reveal>
   <div class="x-news-head"><span class="x-kick">最近更新</span><h2>${esc(latest.ver)}<small>${esc(latest.env)}</small></h2>
    <div class="x-cta"><a class="pill alt" href="/docs/changelog">完整更新日志 ${ic('arrow')}</a><a class="link" href="/docs/changelog.xml">RSS 订阅</a></div></div>
   <ul class="x-news-list">${news.map(([t, h, pr]) => `<li><span class="badge ${t}">${TAG[t]}</span><span>${esc(h)}</span>${pr ? `<a class="pr" href="${REPO}/pull/${pr}" rel="noopener">#${pr}</a>` : ''}</li>`).join('')}</ul>
  </section>

  <section class="x-sec x-agents" data-reveal>
   <div class="x-head"><h2>也写给 AI 读</h2><p>看不懂的地方，直接交给你的 AI 助手：每一页都有 Markdown 原文，整本文档也能一次拿走。</p></div>
   <div class="x-agent-grid">
    <a class="x-agent" href="/docs/llms.txt"><code>llms.txt</code><p>全站目录，给模型的入口。</p></a>
    <a class="x-agent" href="/docs/manual.zip">${ic('down', 'width:16px;height:16px')}<b>整本下载</b><p>全部使用文档的 Markdown，打成一个包。</p></a>
    <a class="x-agent" href="/docs/quickstart.md"><code>/docs/&lt;页&gt;.md</code><p>任何一页加上 .md 就是它的原文；页首「复制本页」也是这一份。</p></a>
    <a class="x-agent" href="/docs/dev/overview">${ic('lock', 'width:16px;height:16px')}<b>开发文档</b><p>架构、关键流程和代码索引。仅平台管理员可看。</p></a>
   </div>
  </section>

  <section class="x-sec">
   <div class="x-head" data-reveal><h2>常见问题</h2></div>
   <div class="h-faq" data-reveal>${faq.map((f) => `<details><summary>${esc(f.q)}<span class="h-plus"></span></summary><div>${f.a} <a class="link" href="/docs/troubleshooting#${f.id}">详细说明</a></div></details>`).join('')}</div>
  </section>

  <section class="x-end" data-reveal>
   <h2>准备好了？去跟芝士说第一句话。</h2>
   <div class="x-cta" style="justify-content:center"><a class="pill lg magnetic" href="/docs/quickstart">快速开始 ${ic('arrow')}</a><button class="pill lg alt magnetic" data-open-ask>${ic('chat')} 问芝士</button></div>
   <p class="x-end-sub">想在自己电脑上用？<a class="link" href="/docs/download">下载桌面端</a></p>
  </section>
  </div>`
  return shell(ctx, { title: '知是 · Cheese 文档', section: 'home', bodyClass: 'page-home', main, pageData: { kind: 'home' } })
}

// The home page, rendered at build time. Interactive parts (logo motion, the
// pinned room story, the role tabs and the 我是…我要… pickers) are wired up by
// src/app.js from the same data, passed in the page's JSON.
import { esc, shell, ic } from './render.mjs'

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
export const ROLE_ICON = { 学生: 'book', '老师 / 助教': 'users', 办公: 'folder' }

export function workPanel(i, pages) {
  const [role, time, label, task, tools, result, files, to] = WORK[i]
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
    who: pickHtml('who', who, roles, who, open),
    what: pickHtml('what', WHO[who][what][0], jobs, what, open),
    out: `<div class="x-say-card"><small>${esc(target.sectionLabel)} · ${esc(target.title)}</small><b>${esc(t)}</b><p>${esc(d)}</p><a class="pill" href="${target.url}">看看怎么做 ${ic('arrow')}</a></div>`,
  }
}

export function homePage(ctx, { releases, faq, WHO, library, pages }) {
  const latest = releases[0]
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

  <section class="x-sec x-three">
   ${[['01', '把手上的事交出去', '不是问一句答一句：说清要什么，芝士自己读材料、跑命令、做出东西，做完递给你验收。', 'working-with-cheese'],
      ['02', '把想法做成东西', '文档、表格、网页、代码都能交付；做出来的网页可以直接发布成项目网站。', 'files'],
      ['03', '和别人一起做', '团队、项目、话题：同学和同事在同一个房间里，谁在做什么、轮到谁，一眼看清。', 'teams']].map(([n, t, d, s], k) => `<a class="x-num spot" href="${pages[s].url}" data-reveal style="--d:${k}"><span class="n">${n}</span><b>${t}</b><p>${d}</p><span class="go">了解更多 ${ic('arrow')}</span></a>`).join('')}
  </section>

  <section class="x-sec">
   <div class="x-head" data-reveal><h2>让芝士干活</h2><p>挑一个身份，看一件事是怎么交出去、又怎么交回来的。</p></div>
   <div class="x-tabs" id="workTabs" data-reveal role="tablist"><span class="f-ind" id="workInd"></span>${WORK.map(([r], i) => `<button role="tab" data-work="${i}"${i ? '' : ' class="on" aria-selected="true"'}>${r}</button>`).join('')}</div>
   <div class="x-work" id="workPanel" data-reveal role="tabpanel">${workPanel(0, pages)}</div>
  </section>

  <section class="x-sec">
   <div class="x-head" data-reveal><h2>看看知是能做什么</h2><p>全部文档，按用途分好。</p></div>
   <div class="x-lib">${library.map(([g, dev, items], i) => `<div data-reveal style="--d:${i % 4}"><h3>${dev ? ic('lock', 'width:12px;height:12px') : ''}${esc(g)}</h3>${items.map((p) => `<a href="${p.url}">${esc(p.title)}</a>`).join('')}</div>`).join('')}</div>
  </section>

  <section class="x-sec x-say" data-reveal>
   <p class="x-sentence">我是 <span class="x-pick" id="pickWho">${say.who}</span>，<br>我要 <span class="x-pick" id="pickWhat">${say.what}</span>。</p>
   <div class="x-say-out" id="sayOut">${say.out}</div>
  </section>

  <section class="x-sec">
   <div class="x-head" data-reveal><h2>常见问题</h2></div>
   <div class="h-faq" data-reveal>${faq.map((f) => `<details><summary>${esc(f.q)}<span class="h-plus"></span></summary><div>${f.a} <a class="link" href="/docs/troubleshooting#${f.id}">详细说明</a></div></details>`).join('')}</div>
  </section>

  <section class="x-end" data-reveal>
   <h2>准备好了？去跟芝士说第一句话。</h2>
   <div class="x-cta" style="justify-content:center"><a class="pill lg magnetic" href="/docs/quickstart">快速开始 ${ic('arrow')}</a><button class="pill lg alt magnetic" data-open-ask>${ic('chat')} 问芝士</button></div>
  </section>
  </div>`
  return shell(ctx, { title: '知是 · Cheese 文档', section: 'home', bodyClass: 'page-home', main, pageData: { kind: 'home' } })
}

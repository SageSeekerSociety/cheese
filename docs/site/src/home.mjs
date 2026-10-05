// The home page, rendered at build time: a headline, the search box, one card
// per part of the docs, then the common questions and what changed last.
import { esc, shell, ic, REPO } from './render.mjs'
import { TAG } from './content.js'

// One line per card: what that part of the docs is for.
const DOORS = {
  start: '十分钟上手：建项目、开话题，把第一件事交给芝士，再验收它交回来的东西。',
  tutorials: '按身份把一件事从头走到尾：学生交作业、老师开课、办公协作。',
  features: '每个功能是什么、在哪、怎么用、有什么限制，按用途分组。',
  faq: '芝士没回复、机器没连上、额度用完……遇到问题先看这里。',
}

const card = (href, icon, title, text) => `<a class="home-card" href="${href}"><span class="home-card-ic">${ic(icon)}</span><b>${esc(title)}</b><p>${esc(text)}</p></a>`

export function homePage(ctx, { releases, faq, doors }) {
  const latest = releases[0]
  const news = ['feat', 'imp'].flatMap((t) => (latest.hl[t] || []).map(([h, pr]) => [t, h, pr])).slice(0, 5)
  const main = `<main class="home">
  <section class="home-hero">
   <h1>知是使用文档</h1>
   <p class="lede">从建第一个项目，到把芝士做出来的成果合进主线：每一步在哪点、会看到什么。</p>
   <button class="home-search" data-open-search>${ic('search')}<span>搜索文档，或直接问芝士</span><kbd>⌘K</kbd></button>
  </section>

  <section class="home-sec" aria-label="文档分区">
   <div class="home-cards">
    ${doors.map((d) => card(d.items[0].url, d.icon, d.label, DOORS[d.key] || '')).join('')}
    ${card('/docs/download', 'download', '桌面端与连接器', '在自己的电脑上用知是，或把一台服务器接进来给芝士干活。')}
    ${card('/docs/changelog', 'tag', '更新日志', '每一版改了什么，写成人话，每条都链到对应的改动。')}
   </div>
  </section>

  <section class="home-sec">
   <h2>常见问题</h2>
   <div class="home-faq">${faq.map((f) => `<details><summary>${esc(f.q)}${ic('down')}</summary><div>${f.a} <a class="link" href="/docs/troubleshooting#${f.id}">详细说明</a></div></details>`).join('')}</div>
  </section>

  <section class="home-sec">
   <div class="home-sec-head"><h2>最近更新</h2><a class="link" href="/docs/changelog">完整更新日志</a></div>
   <p class="home-news-ver">${esc(latest.ver)} · ${esc(latest.env)}</p>
   <ul class="home-news">${news.map(([t, h, pr]) => `<li><span class="badge">${TAG[t]}</span><span>${esc(h)}</span>${pr ? `<a class="pr" href="${REPO}/pull/${pr}" rel="noopener">#${pr}</a>` : ''}</li>`).join('')}</ul>
  </section>
  </main>`
  return shell(ctx, { title: '知是 · Cheese 文档', section: 'home', bodyClass: 'page-home', main, pageData: { kind: 'home' } })
}

// 把 preview.mjs 拍出来的图折成一页可以在预览面板里直接看的 HTML。
//
// 图是 base64 内嵌的，因为预览面板拿到的是一个文件，不是一个站点——外链的图它
// 取不到。所以这一页必须自足。
//
// 之前这一页是手拼的：一次改动要在 27 万字符的 base64 里找位置插一段。改成脚本
// 之后，重跑 `node preview.mjs && node gallery.mjs` 就是一版新的，说明文字在下面
// 这张表里改。
import { readFileSync, writeFileSync } from 'node:fs'

const SHOTS = '/tmp/shots'
const OUT = new URL('../docs/topics/成员页与私聊-预览.html', import.meta.url)

const TITLE = '成员页与私聊 · 效果预览'
const LEDE = '名册是「有谁」，右边那两颗按钮是「找他」和「管他」。私聊一个队友一间，邀请要对方接受才算加入。'
const NOTE = '跑在真代码上的截图，只有数据是编的——预览不该碰任何一个真人的名册。'

const SECTIONS = [
  {
    file: '01-members',
    h2: '成员页',
    p: '侧栏「AI 队友」下面一行「成员」。人按角色分三段，AI 队友单独一段。私聊未读挂在「成员」那一行上（总数），进来之后精确到每一颗私聊按钮。',
  },
  {
    file: '06-teammates',
    h2: 'AI 队友：一个队友一间私聊',
    p: '每个队友各一行、各一颗私聊按钮、各一份未读（图里芝士 1 条、评审员 4 条）。点进去答话的就是这一位——角色设定、模型和记忆都是它自己的。换项目默认队友不会把已有的对话搬走。',
  },
  {
    file: '02-invite-by-uid',
    h2: '邀请：填 uid，先查出这个人是谁',
    p: 'uid 就在个人主页地址里，抄得准；handle 打错一个字母的后果是「查无此人」还是「加错了人」全看运气。边打边查，查到了把头像、昵称和 @handle 摆出来给人确认。',
  },
  {
    file: '03-invite-not-found',
    h2: '查无此人',
    p: '查不到、或者这个人已经在项目里，当场说，按钮按不下去。',
  },
  {
    file: '04-pending-invitations',
    h2: '等待接受',
    p: '发出去还没被答复的邀请在这里，可以撤回。它们不在名册上——进了项目就看得见全部话题，所以得由被邀请的人点头。',
  },
  {
    file: '05-accept-page',
    h2: '被邀请的人看到的',
    p: '首页 → 小队 → 待定。通知在被答复之前不会从收件箱消失。',
  },
]

const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
const img = (file) => `data:image/webp;base64,${readFileSync(`${SHOTS}/${file}.webp`).toString('base64')}`

const html = `<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8" />
<title>${esc(TITLE)}</title>
<style>
  :root { color-scheme: light; }
  body { margin: 0; padding: 40px 24px 80px; background: #f7f7f8; color: #1c1d1f;
         font-family: -apple-system, "PingFang SC", "Microsoft YaHei", system-ui, sans-serif; line-height: 1.7; }
  .wrap { max-width: 1080px; margin: 0 auto; }
  h1 { font-size: 26px; margin: 0 0 6px; }
  .lede { color: #6b6d70; margin: 0 0 8px; max-width: 720px; }
  .note { color: #6b6d70; font-size: 13px; margin: 0 0 36px; max-width: 720px;
          border-left: 3px solid #e3e4e6; padding-left: 12px; }
  section { margin: 0 0 44px; }
  h2 { font-size: 17px; margin: 0 0 4px; }
  section p { color: #6b6d70; font-size: 14px; margin: 0 0 12px; max-width: 760px; }
  img { display: block; width: 100%; height: auto; border: 1px solid #e3e4e6; border-radius: 10px; background: #fff; }
</style>
</head>
<body>
<div class="wrap">
  <h1>${esc(TITLE)}</h1>
  <p class="lede">${esc(LEDE)}</p>
  <p class="note">${esc(NOTE)}</p>
${SECTIONS.map(
  (s) => `  <section>
  <h2>${esc(s.h2)}</h2>
  <p>${esc(s.p)}</p>
  <img alt="${esc(s.h2)}" src="${img(s.file)}" />
  </section>`
).join('\n')}
</div>
</body>
</html>
`

writeFileSync(OUT, html)
console.log('wrote', OUT.pathname, Math.round(html.length / 1024) + 'KB')

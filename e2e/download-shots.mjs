// The screenshots on the download page (frontend/src/views/home/Download.vue):
// the real frontend, in both themes, on a room whose data is made up here. Made
// up on purpose, as in preview.mjs: the page must not show anyone's real project.
//
// Run it against a dev server whose backend is not reachable, so every request
// the page makes is answered from here:
//   (cd frontend && BACKEND_URL=http://127.0.0.1:9 pnpm exec vite --port 5299)
//   node download-shots.mjs
// Rerun it when the room looks different, and commit the two images.
import { writeFileSync } from 'node:fs'
import { chromium } from '@playwright/test'

const BASE = process.env.BASE || 'http://127.0.0.1:5299'
const OUT = new URL('../frontend/public/images/download/', import.meta.url)
const PID = '11111111-1111-1111-1111-111111111111'
const ROOM = 't-migrate'
// A weekday morning, so the times in the room read like work.
const NOW = new Date(new Date().toISOString().slice(0, 10) + 'T10:30:00+08:00').getTime()
const at = (minutesAgo) => new Date(NOW - minutesAgo * 60_000).toISOString()

const ok = (data) => ({ code: 200, message: 'ok', data })
const list = (items) => ok({ data: items, total: items.length })

const ME = { id: 1, username: 'alice', nickname: '爱丽丝', avatarId: 0 }

const TOPICS = [
  ['t-migrate', '用户表迁到新库', 3],
  ['t-login', '登录页改版', 40],
  ['t-weekly', '周报整理', 180],
  ['t-charts', '实验数据可视化', 60 * 26],
  ['t-root', '毕业设计', 60 * 24 * 9],
].map(([id, title, ago]) => ({
  id,
  title,
  title_source: 'human',
  kind: id === 't-root' ? 'root' : 'topic',
  status: 'active',
  project_id: PID,
  parent_id: null,
  created_at: at(60 * 24 * 10),
  last_activity_at: at(ago),
  owner_handle: 'alice',
  involved: true,
}))

const block = (id, author_type, author, content, ago) => ({
  id,
  topic_id: ROOM,
  kind: 'message',
  author_type,
  author,
  content,
  created_at: at(ago),
  reactions: [],
  refs: [],
  meta: null,
})

const BLOCKS = [
  block('b1', 'human', 'alice', '把旧的用户表迁到新库，周五前能上线吗？', 26),
  block(
    'b2',
    'agent',
    'cheese',
    '可以。我看了一下旧表：38 万行，外键只有订单表一处引用，迁移脚本已经写好，在测试库上跑通了，用时 4 分钟。',
    20
  ),
  block('b3', 'human', 'zhangheng', '上线那天旧库先只读，别停。', 12),
  block(
    'b4',
    'agent',
    'cheese',
    '好，切换期间旧库只读。还差一件事要定：新库用 PostgreSQL 还是 MySQL？订单系统现在用的是 MySQL，放在一起维护更省事；PostgreSQL 的 JSON 查询更适合后面的画像字段。',
    3
  ),
]

// The room's living document, as 芝士 keeps it.
const DOC = {
  ...block('doc', 'agent', 'cheese', '', 5),
  kind: 'doc',
  doc_version: 3,
  content: [
    '## 目标',
    '',
    '周五前把用户表迁到新库，切换期间旧库只读，不停服。',
    '',
    '## 进度',
    '',
    '- [x] 迁移脚本：测试库跑通，38 万行用时 4 分钟',
    '- [x] 订单表外键改指新库',
    '- [ ] 确定新库用 PostgreSQL 还是 MySQL',
    '- [ ] 周五晚上切换，旧库保留一周',
    '',
    '## 决定',
    '',
    '- 切换期间旧库只读（张衡）',
  ].join('\n'),
}

function handle(pathname) {
  if (pathname === '/api/version') return ok({ version: 'preview' })
  if (pathname === '/api/users/auth/refresh-token') return ok({ accessToken: 'preview-token', user: ME })
  if (pathname === '/api/projects')
    return list([
      { id: PID, name: '毕业设计', owner_handle: 'alice', summary: '', root_topic_id: 't-root', created_at: at(60 * 24 * 10) },
    ])
  if (pathname === `/api/projects/${PID}/members`)
    return list([
      { user_handle: 'alice', name: '爱丽丝', role: 'lead' },
      { user_handle: 'zhangheng', name: '张衡', role: 'member' },
      { user_handle: 'cheese', name: '芝士', role: 'member', agent: true },
    ])
  if (pathname === `/api/projects/${PID}/topic-unread`) return ok({ 't-login': 2 })
  if (pathname === `/api/projects/${PID}/private-unread`) return ok({})
  if (pathname === '/api/topics') return list(TOPICS)
  if (pathname === `/api/topics/${ROOM}`) return ok(TOPICS[0])
  if (pathname === `/api/topics/${ROOM}/members`)
    return list(
      [
        ['alice', '爱丽丝', 'owner', false],
        ['zhangheng', '张衡', 'member', false],
        ['cheese', '芝士', 'member', true],
      ].map(([member_handle, name, role, agent]) => ({
        id: `m-${member_handle}`,
        topic_id: ROOM,
        member_handle,
        name,
        role,
        agent,
        avatar_id: null,
        created_at: at(60 * 24),
      }))
    )
  if (pathname === `/api/topics/${ROOM}/blocks`) return list(BLOCKS)
  if (pathname === `/api/topics/${ROOM}/doc`) return ok(DOC)
  return null
}

const browser = await chromium.launch({ executablePath: process.env.CHROME || undefined })
const unmatched = new Set()

for (const theme of ['light', 'dark']) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2, colorScheme: theme, timezoneId: 'Asia/Shanghai', locale: 'zh-CN' })
  await ctx.addInitScript(
    ([me, theme]) => {
      localStorage.setItem('accessToken', 'preview-token')
      localStorage.setItem('user', JSON.stringify(me))
      localStorage.setItem('cheesex.theme', theme)
      localStorage.setItem('cheese:locale', 'zh-CN')
    },
    [ME, theme]
  )
  await ctx.route(
    (url) => url.pathname.startsWith('/api/') || url.pathname.startsWith('/users/') || url.pathname.startsWith('/connector/'),
    async (route) => {
      const u = new URL(route.request().url())
      const body = handle(u.pathname)
      if (body === null) unmatched.add(route.request().method() + ' ' + u.pathname)
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body ?? ok(null)) })
    }
  )
  // The room's live connection opens and stays quiet, answering the heartbeat.
  await ctx.routeWebSocket(/\/api\//, (ws) => {
    ws.onMessage((message) => {
      if (String(message).includes('ping')) ws.send(JSON.stringify({ type: 'pong' }))
    })
  })
  // Nothing leaves this machine: a font or picture from elsewhere would only hold up the shot.
  await ctx.route((url) => !['127.0.0.1', 'localhost'].includes(url.hostname), (route) => route.abort())
  const page = await ctx.newPage()
  await page.goto(`${BASE}/projects/${PID}/topics/${ROOM}`, { waitUntil: 'domcontentloaded', timeout: 60000 })
  await page.getByText('适合后面的画像字段').first().waitFor({ timeout: 30000 })
  await page.waitForTimeout(1500)
  const png = await page.screenshot()
  const webp = await page.evaluate(async (b64) => {
    const img = new Image()
    img.src = 'data:image/png;base64,' + b64
    await img.decode()
    const c = document.createElement('canvas')
    c.width = img.naturalWidth
    c.height = img.naturalHeight
    c.getContext('2d').drawImage(img, 0, 0)
    return c.toDataURL('image/webp', 0.86).split(',')[1]
  }, png.toString('base64'))
  writeFileSync(new URL(`app-${theme}.webp`, OUT), Buffer.from(webp, 'base64'))
  console.log(`app-${theme}.webp`, Math.round(Buffer.from(webp, 'base64').length / 1024) + 'KB')
  await ctx.close()
}

console.log('unmatched:', [...unmatched].join(', ') || '(none)')
await browser.close()

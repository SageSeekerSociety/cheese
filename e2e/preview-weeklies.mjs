// 周报集的效果图：真组件、真样式、假数据（照 preview.mjs 那套）。
//
// 两张图回答两个问题：一批周报并排是什么样、空态到底说了什么。第一张里还带着那个
// 洞的现场——侧栏里那间叫「周报怎么发」的房间在场，周报集里却没有它。
import { chromium } from '@playwright/test'

const BASE = process.env.BASE || 'http://localhost:5399'
const PID = '11111111-1111-1111-1111-111111111111'
const OUT = process.env.OUT || '/tmp/shots-weeklies'

const ok = (data) => ({ code: 200, message: 'ok', data })
const list = (items) => ok({ data: items, total: items.length })

const topic = (id, title, kind = 'topic') => ({
  id,
  title,
  kind,
  status: 'active',
  project_id: PID,
  parent_id: null,
  created_at: '2026-09-01T02:00:00Z',
  last_activity_at: '2026-09-14T02:00:00Z',
  owner_handle: 'alice',
})

const TOPICS = [
  topic('t1', '办公能力落地', 'root'),
  topic('t-office', '产物页预览'),
  topic('t-weekly', '周报怎么发'),
]

const week = (id, topic_id, since, until, created_at, content) => ({
  id,
  topic_id,
  kind: 'weekly',
  author: 'cheese',
  author_type: 'participant',
  content,
  created_at,
  refs: [topic_id],
  meta: { since, until },
})

const WEEKLIES = [
  week(
    'w3',
    't-office',
    '2026-09-07T00:00:00+00:00',
    '2026-09-13T23:59:59+00:00',
    '2026-09-14T02:10:00Z',
    '这一版交付的是**「选中哪一版，就在页面上看得见那一版」**——预览、版本栏、下载三样各自归位。\n\n- 旧 `.xls` 现在拿到一张真表格（先转 `.xlsx`，不再压成 PDF）\n- 转换服务没接上时说清是「这个部署没接」，不是「这份文件坏了」\n\n**还欠着的**：房间文档面板上的 `.xls` 仍走老路，下一批改。'
  ),
  week(
    'w2',
    't-weekly',
    '2026-08-31T00:00:00+00:00',
    '2026-09-06T23:59:59+00:00',
    '2026-09-07T01:30:00Z',
    '这一周没有值得记的事：调研在跑，代码没动。'
  ),
  week(
    'w1',
    't-office',
    '2026-08-24T00:00:00+00:00',
    '2026-08-30T23:59:59+00:00',
    '2026-08-31T01:05:00Z',
    '开工的一周。把「办公类产品要不要在项目层分办公 / 代码」这件事调研完了，结论是**不分**：机制大部分共用，不同的只有挂载源、技能和产物形态。\n\n下一步是把结论落成一版能看的东西。'
  ),
]

const state = { weeklies: WEEKLIES }

function handle(pathname) {
  if (pathname === '/api/version') return ok({ version: 'preview' })
  if (pathname === '/api/projects')
    return list([
      {
        id: PID,
        name: '办公能力落地',
        owner_handle: 'alice',
        summary: '',
        root_topic_id: 't1',
        created_at: '2026-09-01T02:00:00Z',
      },
    ])
  if (pathname === `/api/projects/${PID}`)
    return ok({
      id: PID,
      name: '办公能力落地',
      owner_handle: 'alice',
      summary: '',
      root_topic_id: 't1',
      created_at: '2026-09-01T02:00:00Z',
    })
  if (pathname === `/api/projects/${PID}/weeklies`) return list(state.weeklies)
  if (pathname === `/api/projects/${PID}/tasks`) return list([])
  if (pathname === `/api/topics`) return list(TOPICS)
  if (pathname === `/api/projects/${PID}/agents`)
    return list([{ id: 'ag1', handle: 'cheese', display_name: '芝士', is_default: true, is_active: true }])
  if (pathname === `/api/projects/${PID}/topic-unread`) return ok({})
  if (pathname === `/api/projects/${PID}/private-unread`) return ok({})
  return null
}

const b = await chromium.launch()
const ctx = await b.newContext({ viewport: { width: 1440, height: 980 }, deviceScaleFactor: 2 })
await ctx.addInitScript(() => {
  localStorage.setItem('accessToken', 'preview-token')
  localStorage.setItem('user', JSON.stringify({ id: 1, username: 'alice', nickname: '爱丽丝', avatarId: 0 }))
  localStorage.setItem(
    'cheesex.me',
    JSON.stringify({ id: '1', handle: 'alice', name: '爱丽丝', token: 'preview-token' })
  )
})
const unmatched = new Set()
await ctx.route(
  (url) => url.pathname.startsWith('/api/'),
  async (route) => {
    const u = new URL(route.request().url())
    const body = handle(u.pathname)
    if (body === null) unmatched.add(route.request().method() + ' ' + u.pathname)
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(body ?? ok(null)),
    })
  }
)

const p = await ctx.newPage()
const shot = async (name) => {
  await p.waitForTimeout(700)
  const png = await p.screenshot()
  const webp = await p.evaluate(async (b64) => {
    const img = new Image()
    img.src = 'data:image/png;base64,' + b64
    await img.decode()
    const c = document.createElement('canvas')
    c.width = img.naturalWidth
    c.height = img.naturalHeight
    c.getContext('2d').drawImage(img, 0, 0)
    return c.toDataURL('image/webp', 0.85).split(',')[1]
  }, png.toString('base64'))
  const { writeFileSync } = await import('node:fs')
  writeFileSync(`${OUT}/${name}.webp`, Buffer.from(webp, 'base64'))
  console.log('shot', name, Math.round(Buffer.from(webp, 'base64').length / 1024) + 'KB')
}

await p.goto(`${BASE}/projects/${PID}/docs/weeklies`, { waitUntil: 'domcontentloaded', timeout: 60000 })
await p.getByText('周报集').first().waitFor({ timeout: 30000 })
await p.getByText('8月24日 – 8月30日').waitFor({ timeout: 30000 })
await shot('01-weeklies')

// 空态：没有周报时它到底说了什么。
state.weeklies = []
await p.goto(`${BASE}/projects/${PID}/docs/decisions`, { waitUntil: 'domcontentloaded', timeout: 60000 })
await p.goto(`${BASE}/projects/${PID}/docs/weeklies`, { waitUntil: 'domcontentloaded', timeout: 60000 })
await p.getByText('暂无周报').waitFor({ timeout: 30000 })
await shot('02-empty')

console.log('final url:', p.url())
console.log('unmatched:', [...unmatched].join(', ') || '(none)')
await b.close()

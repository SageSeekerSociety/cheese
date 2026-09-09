// 成员页 / 邀请 / 私聊 的效果图：真组件、真样式、假数据。
// 假数据是刻意的——预览不该碰任何一个真人的名册，而一份编好的名册正好能把三种
// 角色、AI 队友、未读、待答复的邀请同时摆进一张图里。
import { chromium } from '@playwright/test'

const BASE = 'http://localhost:5299'
const PID = '11111111-1111-1111-1111-111111111111'
const OUT = process.env.OUT || '/tmp/shots'

const MEMBERS = [
  { user_handle: 'alice', name: '爱丽丝', role: 'lead' },
  { user_handle: 'zhangheng', name: '张衡', role: 'lead' },
  { user_handle: 'chenlaoshi', name: '陈老师', role: 'mentor' },
  { user_handle: 'ligan', name: '李干', role: 'member' },
  { user_handle: 'maxiaoyu', name: '马霄宇', role: 'member' },
  { user_handle: 'chiruotong', name: '池若彤', role: 'member' },
  { user_handle: 'pengwenbo', name: '彭文博', role: 'member' },
  { user_handle: 'caisongyang', name: '蔡松洋', role: 'member' },
  { user_handle: 'cheese-01', name: '芝士', role: 'member', agent: true },
]

const TOPICS = ['推荐算法原型', '召回层选型', '离线评测流水线', '冷启动怎么办', '周会纪要'].map(
  (title, i) => ({
    id: `t${i + 1}`,
    title,
    kind: i === 0 ? 'root' : 'topic',
    status: 'active',
    project_id: PID,
    parent_id: null,
    created_at: '2026-09-01T02:00:00Z',
    last_activity_at: '2026-09-08T02:00:00Z',
    owner_handle: 'alice',
  })
)

const ok = (data) => ({ code: 200, message: 'ok', data })
const PAGE = { pageStart: 0, pageSize: 20, hasMore: false, total: 0 }
const list = (items) => ok({ data: items, total: items.length })

const INVITATIONS = [
  {
    id: 'inv-1',
    project_id: PID,
    invitee_handle: 'zhangwei',
    inviter_handle: 'alice',
    role: 'member',
    status: 'pending',
    created_at: '2026-09-08T02:00:00Z',
    responded_at: null,
    project_name: '推荐算法原型',
  },
]

function handle(pathname) {
  if (pathname === '/api/version') return ok({ version: 'preview' })
  if (pathname === '/api/projects')
    return list([
      {
        id: PID,
        name: '推荐算法原型',
        owner_handle: 'alice',
        summary: '',
        root_topic_id: 't1',
        created_at: '2026-09-01T02:00:00Z',
      },
    ])
  if (pathname === `/api/projects/${PID}/members`) return list(MEMBERS)
  if (pathname === `/api/projects/${PID}/invitations`) return list(INVITATIONS)
  if (pathname === `/api/projects/${PID}/agents`)
    return list([
      { id: 'ag1', handle: 'cheese', display_name: '芝士', is_default: true, is_active: true },
      { id: 'ag2', handle: 'reviewer', display_name: '评审员', is_default: false, is_active: true },
      { id: 'ag3', handle: 'writer', display_name: '文档', is_default: false, is_active: true },
    ])
  if (pathname === `/api/projects/${PID}/topic-unread`) return ok({ t4: 3 })
  // 一个队友一间私聊，所以未读也一个队友一份 —— 键是 `agent:<handle>`。
  if (pathname === `/api/projects/${PID}/private-unread`)
    return ok({ ligan: 2, 'agent:cheese': 1, 'agent:reviewer': 4 })
  if (pathname === '/api/topics') return list(TOPICS)
  // 「待定」那一页的小队申请/邀请，也在 1.0 那层。
  if (pathname === '/users/me/team-requests')
    return { code: 200, message: 'OK', data: { requests: [], page: PAGE } }
  if (pathname === '/users/me/team-invitations')
    return { code: 200, message: 'OK', data: { invitations: [], page: PAGE } }
  // 等我答复的项目邀请。
  if (pathname === '/api/me/invitations')
    return list([
      {
        id: 'inv-9',
        project_id: PID,
        invitee_handle: 'alice',
        inviter_handle: 'zhangheng',
        role: 'member',
        status: 'pending',
        created_at: '2026-09-08T02:00:00Z',
        responded_at: null,
        project_name: '推荐算法原型',
      },
    ])
  // 按 uid 查人走的是 1.0 那层，不带 /api 前缀。
  if (pathname.startsWith('/users/'))
    return ok({ user: { id: 1024, username: 'zhangheng', nickname: '张衡', avatarId: 0 } })
  return null
}

const b = await chromium.launch()
const ctx = await b.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 })
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
  (url) => url.pathname.startsWith('/api/') || url.pathname.startsWith('/users/'),
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
  await p.waitForTimeout(600)
  const png = await p.screenshot()
  const webp = await p.evaluate(async (b64) => {
    const img = new Image()
    img.src = 'data:image/png;base64,' + b64
    await img.decode()
    const c = document.createElement('canvas')
    c.width = img.naturalWidth
    c.height = img.naturalHeight
    c.getContext('2d').drawImage(img, 0, 0)
    return c.toDataURL('image/webp', 0.8).split(',')[1]
  }, png.toString('base64'))
  const { writeFileSync } = await import('node:fs')
  writeFileSync(`${OUT}/${name}.webp`, Buffer.from(webp, 'base64'))
  console.log('shot', name, Math.round(Buffer.from(webp, 'base64').length / 1024) + 'KB')
}

await p.goto(`${BASE}/projects/${PID}/members`, { waitUntil: 'domcontentloaded', timeout: 60000 })
await p.getByText('@ligan').waitFor({ timeout: 30000 })
await shot('01-members')

// 邀请：填 uid → 先查出这个人是谁
await p.getByRole('button', { name: '邀请成员' }).click()
await p.getByLabel('uid').fill('1024')
await p.getByText('张衡', { exact: true }).first().waitFor({ timeout: 15000 })
await shot('02-invite-by-uid')
await p.getByRole('button', { name: '取消' }).click()

// 查无此人
await p.getByRole('button', { name: '邀请成员' }).click()
await p.unroute(() => true).catch(() => {})
await p.route(
  (url) => url.pathname.startsWith('/users/'),
  (route) => route.fulfill({ status: 404, contentType: 'application/json', body: '{}' })
)
await p.getByLabel('uid').fill('999999')
await p.getByText(/找不到 uid/).waitFor({ timeout: 15000 })
await shot('03-invite-not-found')
await p.getByRole('button', { name: '取消' }).click()

// 等待接受
await p.locator('.member-row').first().scrollIntoViewIfNeeded()
await p.getByText('等待接受 · 1').scrollIntoViewIfNeeded()
await shot('04-pending-invitations')

// 被邀请的那一边：首页 → 小队 → 待定
await p.goto(`${BASE}/teams/pending`, { waitUntil: 'domcontentloaded', timeout: 60000 })
await p.getByText('收到的项目邀请').waitFor({ timeout: 30000 })
await p.waitForTimeout(7000) // 让假后端引出的那条一次性提示自己散掉（小队接口不在这套假数据里）
await shot('05-accept-page')

// AI 队友：一个队友一行、一颗自己的私聊按钮、一份自己的未读。
await p.goto(`${BASE}/projects/${PID}/members`, { waitUntil: 'domcontentloaded', timeout: 60000 })
await p.getByText('AI 队友 · 3').waitFor({ timeout: 30000 })
await p.getByText('AI 队友 · 3').scrollIntoViewIfNeeded()
await shot('06-teammates')

console.log('unmatched:', [...unmatched].join(', ') || '(none)')
await b.close()

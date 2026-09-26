// The docs' screenshots, taken in the example project fixture.py builds.
//
//   node shots.mjs                       # every shot
//   node shots.mjs room board            # some of them
//
// Needs a local stack (frontend on APP, default http://localhost:3000) and a
// Playwright browser: a local Chromium, or a browser server at BROWSER_WS
// (e.g. `mcr.microsoft.com/playwright` running `playwright run-server`).
// Writes JPEGs into docs/manual/public/images/, the directory the docs build
// copies to /docs/images/. The local origin in any text on screen (install
// commands, links) is shown as the public one, https://okcheese.com.
import { chromium } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const APP = process.env.APP || 'http://localhost:3000'
const OUT = process.env.SHOT_OUT || join(dirname(fileURLToPath(import.meta.url)), '../../manual/public/images')
const PUBLIC = 'https://okcheese.com'
const PROJECT_NAME = '校园活动报名小程序'
mkdirSync(OUT, { recursive: true })

const desktop = { viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 }
const phone = { viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, isMobile: true, hasTouch: true }

async function signIn(page) {
  const r = await page.request.post(`${APP}/api/users/auth/login`, { data: { username: 'alice', password: 'demo12345' } })
  const { accessToken, user } = (await r.json()).data
  const headers = { Authorization: `Bearer ${accessToken}` }
  const pending = (await (await page.request.get(`${APP}/api/users/me/consents`, { headers })).json()).data.pending
  if (pending.length)
    await page.request.post(`${APP}/api/users/me/consents`, { headers, data: { documents: Object.fromEntries(pending.map((p) => [p.document, p.version])) } })
  await page.goto(`${APP}/favicon.ico`)
  await page.evaluate(({ a, u }) => { localStorage.setItem('accessToken', a); localStorage.setItem('user', JSON.stringify(u)) }, { a: accessToken, u: user })
  const projects = (await (await page.request.get(`${APP}/api/projects`, { headers })).json()).data
  const project = (projects.data ?? projects).find((p) => p.name === PROJECT_NAME)
  if (!project) throw new Error(`run fixture.py first: no project named ${PROJECT_NAME}`)
  const topics = (await (await page.request.get(`${APP}/api/topics?project_id=${project.id}`, { headers })).json()).data
  const rooms = Object.fromEntries((topics.data ?? topics).map((t) => [t.title, t.id]))
  return { pid: project.id, rooms }
}

async function settle(page, ms = 1200) {
  await page.waitForLoadState('networkidle').catch(() => {})
  await page.waitForTimeout(ms)
}

// name → [device, take(page, ctx) returning screenshot options]
const SHOTS = {
  'work-home': [desktop, async (page) => { await page.goto(`${APP}/`); await settle(page); return { clip: { x: 64, y: 30, width: 1376, height: 320 } } }],
  room: [desktop, async (page, { pid, rooms }) => { await page.goto(`${APP}/projects/${pid}/topics/${rooms['报名表单改版']}`); await settle(page, 2000) }],
  'room-menu': [desktop, async (page, { pid, rooms }) => {
    await page.goto(`${APP}/projects/${pid}/topics/${rooms['报名表单改版']}`)
    await settle(page)
    const row = page.locator('.topic-row.is-active')
    await row.hover()
    await row.locator('.row-actions__btn').click()
    await page.waitForTimeout(500)
    return { clip: { x: 0, y: 0, width: 720, height: 520 } }
  }],
  board: [desktop, async (page, { pid }) => { await page.goto(`${APP}/projects/${pid}/running`); await settle(page); return { clip: { x: 340, y: 30, width: 1100, height: 260 } } }],
  'task-card': [desktop, async (page, { pid, rooms }) => {
    await page.goto(`${APP}/projects/${pid}/topics/${rooms['报名表单改版']}`)
    await settle(page)
    await page.getByRole('button', { name: '查看任务' }).first().click()
    await settle(page, 1500)
  }],
  library: [desktop, async (page, { pid }) => { await page.goto(`${APP}/projects/${pid}/library`); await settle(page); return { clip: { x: 340, y: 30, width: 1100, height: 300 } } }],
  members: [desktop, async (page, { pid }) => { await page.goto(`${APP}/projects/${pid}/members`); await settle(page); return { clip: { x: 340, y: 30, width: 1100, height: 750 } } }],
  settings: [desktop, async (page, { pid }) => { await page.goto(`${APP}/projects/${pid}/settings`); await settle(page, 2000); return { clip: { x: 345, y: 34, width: 1095, height: 616 } } }],
  devices: [desktop, async (page) => { await page.goto(`${APP}/my/devices`); await settle(page); return { clip: { x: 300, y: 40, width: 900, height: 490 } } }],
  teams: [desktop, async (page) => { await page.goto(`${APP}/teams`); await settle(page); return { clip: { x: 64, y: 30, width: 1376, height: 320 } } }],
  feedback: [desktop, async (page) => { await page.goto(`${APP}/feedback`); await settle(page); return { clip: { x: 260, y: 40, width: 980, height: 560 } } }],
  'feedback-new': [desktop, async (page) => { await page.goto(`${APP}/feedback/new`); await settle(page); return { clip: { x: 280, y: 40, width: 880, height: 820 } } }],
  'm-work-home': [phone, async (page) => { await page.goto(`${APP}/`); await settle(page) }],
  'm-room': [phone, async (page, { pid, rooms }) => { await page.goto(`${APP}/projects/${pid}/topics/${rooms['报名表单改版']}`); await settle(page, 2000) }],
}

const wanted = process.argv.slice(2)
const browser = process.env.BROWSER_WS ? await chromium.connect(process.env.BROWSER_WS) : await chromium.launch()
for (const [name, [device, take]] of Object.entries(SHOTS)) {
  if (wanted.length && !wanted.includes(name)) continue
  const context = await browser.newContext({ ...device, locale: 'zh-CN', colorScheme: 'light' })
  const page = await context.newPage()
  const ctx = await signIn(page)
  const options = (await take(page, ctx)) || {}
  await page.evaluate(([from, to]) => {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)
    for (let n = walker.nextNode(); n; n = walker.nextNode()) if (n.nodeValue.includes(from)) n.nodeValue = n.nodeValue.replaceAll(from, to)
  }, [new URL(APP).origin, PUBLIC])
  await page.screenshot({ path: join(OUT, `${name}.jpg`), type: 'jpeg', quality: 88, ...options })
  console.log(`${name}.jpg`)
  await context.close()
}
await browser.close()

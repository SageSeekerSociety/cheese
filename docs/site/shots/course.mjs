// Screenshots for the teaching pages, taken in the course course_fixture.py
// builds. Pass that script's output:
//
//   .venv/bin/python ../docs/site/shots/course_fixture.py > /tmp/course.json
//   COURSE=/tmp/course.json node shots/course.mjs [name …]
//
// Same stack, browser and output directory as shots.mjs. alice is the teacher;
// bobby and carol are approved students (carol's work is reviewed, bobby's is
// waiting); david's enrolment is still pending; evelyn has not joined yet.
import { chromium } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const APP = process.env.APP || 'http://localhost:3000'
const OUT = process.env.SHOT_OUT || join(dirname(fileURLToPath(import.meta.url)), '../../manual/public/images')
const PUBLIC = 'https://okcheese.com'
const C = JSON.parse(readFileSync(process.env.COURSE || '/tmp/course.json', 'utf8'))
const S = C.space, [T1, T2, T3] = C.tasks
const desktop = { viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 }

async function signIn(page, username) {
  const r = await page.request.post(`${APP}/api/users/auth/login`, { data: { username, password: 'demo12345' } })
  const { accessToken, user } = (await r.json()).data
  const headers = { Authorization: `Bearer ${accessToken}` }
  const pending = (await (await page.request.get(`${APP}/api/users/me/consents`, { headers })).json()).data.pending
  if (pending.length)
    await page.request.post(`${APP}/api/users/me/consents`, { headers, data: { documents: Object.fromEntries(pending.map((p) => [p.document, p.version])) } })
  await page.goto(`${APP}/favicon.ico`)
  await page.evaluate(({ a, u }) => { localStorage.setItem('accessToken', a); localStorage.setItem('user', JSON.stringify(u)) }, { a: accessToken, u: user })
}

async function settle(page, ms = 5000) {
  await page.waitForLoadState('networkidle').catch(() => {})
  await page.waitForTimeout(ms)
}
const open = (path, ms) => async (page) => { await page.goto(`${APP}${path}`); await settle(page, ms) }

// name → [who, take(page) returning screenshot options]
const SHOTS = {
  'join-course': ['evelyn', async (page) => {
    await page.goto(`${APP}/spaces/join/${C.code}`)
    await page.getByText('要不要找同学组队').first().waitFor({ timeout: 20000 }).catch(() => {})
    await settle(page, 800)
  }],
  'course-student-home': ['bobby', open(`/spaces/${S}/course`)],
  'task-overview': ['bobby', open(`/spaces/${S}/tasks/${T2}`)],
  'task-join-dialog': ['bobby', async (page) => {
    await open(`/spaces/${S}/tasks/${T2}`)(page)
    await page.getByRole('button', { name: '领取题目' }).first().click()
    await settle(page, 800)
  }],
  'task-submit': ['bobby', open(`/spaces/${S}/tasks/${T1}/submit`)],
  'task-submissions': ['carol', open(`/spaces/${S}/tasks/${T1}/submissions`)],
  'space-create': ['alice', async (page) => {
    await open('/spaces')(page)
    await page.getByRole('button', { name: '新建空间' }).first().click()
    await settle(page, 600)
    await page.getByLabel('空间名称').first().fill('数据结构（2026 春）').catch(() => {})
    await page.waitForTimeout(300)
  }],
  'course-teacher-home': ['alice', open(`/spaces/${S}/course`)],
  'task-publish': ['alice', open(`/spaces/${S}/tasks/publish`)],
  // Opened from the course's own sidebar: loaded cold, this page asks for its
  // list before it knows which space it is in and shows nothing.
  'task-audit': ['alice', async (page) => {
    await open(`/spaces/${S}/course`)(page)
    await page.getByText('审核题目', { exact: true }).first().click()
    await settle(page, 3000)
    await page.getByText('第 3 周作业').first().click().catch(() => {})
    await page.waitForTimeout(800)
  }],
  'course-units': ['alice', open(`/spaces/${S}/course/units`)],
  'invite-codes': ['alice', open(`/spaces/${S}/manage/invite-codes`)],
  'task-participants': ['alice', open(`/spaces/${S}/tasks/${T1}/participants`)],
  'course-assignments': ['alice', open(`/spaces/${S}/course/assignments`)],
  'course-settings': ['alice', open(`/spaces/${S}/course/settings`)],
  'course-quiz': ['alice', open(`/spaces/${S}/course/quiz?unit=${C.unit1}`)],
  'course-team': ['bobby', open(`/spaces/${S}/course/team`)],
}

const wanted = process.argv.slice(2)
const browser = process.env.BROWSER_WS ? await chromium.connect(process.env.BROWSER_WS) : await chromium.launch()
for (const [name, [who, take]] of Object.entries(SHOTS)) {
  if (wanted.length && !wanted.includes(name)) continue
  const context = await browser.newContext({ ...desktop, locale: 'zh-CN', colorScheme: 'light' })
  const page = await context.newPage()
  await signIn(page, who)
  const options = (await take(page)) || {}
  await page.evaluate(([from, to]) => {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)
    for (let n = walker.nextNode(); n; n = walker.nextNode()) if (n.nodeValue.includes(from)) n.nodeValue = n.nodeValue.replaceAll(from, to)
    for (const el of document.querySelectorAll('input, textarea')) if (el.value.includes(from)) el.value = el.value.replaceAll(from, to)
  }, [new URL(APP).origin, PUBLIC])
  await page.screenshot({ path: join(OUT, `${name}.jpg`), type: 'jpeg', quality: 85, ...options })
  console.log(`${name}.jpg`)
  await context.close()
}
await browser.close()

// 取证：写路径（SpacesApi.review）有没有真的出网。
//
// 做法是点页面上第一条的「通过」，然后比点击前后的 DOM，并看两份记账。
// 判定要求两件事同时成立：零出网（doors 与 net 都空）**并且**页面跟着变
// （「已通过」计数上升、待审核条数下降）—— 只有前者说明请求根本没发，
// 只有后者可能是假服务在别处被绕过之后由缓存凑出来的。
//
// 跑法同 get-spaces.mjs，输出 JSON 到 stdout。
import { createRequire } from 'node:module'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const require = createRequire(resolve(here, '../../../e2e/package.json'))
const { chromium } = require('@playwright/test')

const BASE = process.env.PREVIEW_BASE || 'http://127.0.0.1:5210/feedback-proto.html'
const isApi = (u) => {
  try {
    return /\/(admin|api)(\/|$)/.test(new URL(u, 'http://127.0.0.1:5210').pathname)
  } catch {
    return false
  }
}

const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM || '/usr/bin/chromium',
  args: ['--no-sandbox', '--disable-dev-shm-usage', '--font-render-hinting=none'],
})
const ctx = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  colorScheme: 'light',
  locale: 'zh-CN',
})
const page = await ctx.newPage()

await page.addInitScript(() => {
  window.__doors = []
  const log = (door, url) => window.__doors.push({ door, url: String(url) })
  const of = window.fetch.bind(window)
  window.fetch = function (input, init) {
    const u = typeof input === 'string' ? input : (input && input.url) || String(input)
    if (/\/(admin|api)(\/|$)/.test(new URL(u, location.origin).pathname)) log('fetch', u)
    return of(input, init)
  }
  const open = XMLHttpRequest.prototype.open
  XMLHttpRequest.prototype.open = function (m, u, ...rest) {
    if (/\/(admin|api)(\/|$)/.test(new URL(String(u), location.origin).pathname)) log('xhr', String(u) + ' [' + m + ']')
    return open.call(this, m, u, ...rest)
  }
})

const net = []
page.on('request', (r) => {
  if (isApi(r.url())) net.push({ kind: 'request', method: r.method(), url: r.url() })
})
page.on('response', async (r) => {
  if (!isApi(r.request().url())) return
  let body = null
  try {
    const t = await r.text()
    body = t.length > 200 ? t.slice(0, 200) + `…(${t.length}B)` : t
  } catch (e) {
    body = `<${e.message}>`
  }
  net.push({ kind: 'response', status: r.status(), url: r.request().url(), body })
})
page.on('requestfailed', (r) => {
  if (isApi(r.url())) net.push({ kind: 'requestfailed', url: r.url(), error: r.failure()?.errorText })
})
page.on('pageerror', (e) => net.push({ kind: 'pageerror', message: e.message.slice(0, 200) }))

await page.goto(BASE + '#/admin/spaces', { waitUntil: 'domcontentloaded' })
await page.waitForTimeout(3500)

const snapshot = () =>
  page.evaluate(() => {
    const t = document.body.innerText
    return {
      longTitle: t.includes('名字很长的样例'),
      applicants: (t.match(/申请人：/g) || []).length,
      approvedMarks: (t.match(/已通过/g) || []).length,
    }
  })

const before = await snapshot()
const btns = await page.locator('button:has-text("通过")').all()
const clicked = btns.length
if (btns.length) await btns[0].click()
await page.waitForTimeout(2500)
const after = await snapshot()
const doors = await page.evaluate(() => window.__doors || [])

console.log(JSON.stringify({ before, clicked, after, doors, net }, null, 2))
await browser.close()

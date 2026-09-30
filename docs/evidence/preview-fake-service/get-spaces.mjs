// 取证：空间申请页的 GET 有没有真的出网。
//
// 准绳是 `page.on('request'/'response')` —— 它打在网络栈上，不看 JS 包装顺序，
// 所以「window.fetch 被 mock 了」证明不了任何事，这里不看它。
// 另外在 init 里记两道 JS 出口（`window.fetch` 与 `XMLHttpRequest.prototype.open`），
// 用来分辨走的哪道门。两份记账都为空，才是「零出网」。
//
// 跑法：先把预览构建成 dist-feedback-proto 并挂在 PREVIEW_BASE 上，再
//   node docs/evidence/preview-fake-service/get-spaces.mjs
// 输出是 JSON，stdout。
import { createRequire } from 'node:module'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
// playwright 装在 e2e/ 里（那套用 pnpm），从那儿解析，不写死某台机器上的绝对路径。
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
    if (/\/(admin|api)(\/|$)/.test(new URL(String(u), location.origin).pathname)) log('xhr', String(u))
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
    body = t.length > 240 ? t.slice(0, 240) + `…(${t.length}B)` : t
  } catch (e) {
    body = `<${e.message}>`
  }
  net.push({ kind: 'response', status: r.status(), url: r.request().url(), body })
})
page.on('requestfailed', (r) => {
  if (isApi(r.url())) net.push({ kind: 'requestfailed', url: r.url(), error: r.failure()?.errorText })
})
page.on('pageerror', (e) => net.push({ kind: 'pageerror', message: e.message.slice(0, 200) }))

await page.goto(BASE + (process.argv[2] || '#/admin/spaces'), { waitUntil: 'domcontentloaded' })
await page.waitForTimeout(4000)

const dom = await page.evaluate(() => {
  const t = document.body.innerText
  return {
    hasLongSampleTitle: t.includes('名字很长的样例'),
    hasLongSampleIntro: t.includes('这段简介故意写得很长'),
    hasErrorText: /加载失败/.test(t),
    hasRetryButton: [...document.querySelectorAll('button')].some((b) => (b.textContent || '').trim() === '重试'),
    applicantCount: (t.match(/申请人：/g) || []).length,
    bodyChars: t.length,
  }
})
const doors = await page.evaluate(() => window.__doors || [])

console.log(JSON.stringify({ route: process.argv[2] || '#/admin/spaces', doors, net, dom }, null, 2))
await browser.close()

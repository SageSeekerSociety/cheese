// 取证：`XMLHttpRequest` 那道后手接不接得住「补丁之前就建好的 axios 实例」。
//
// 为什么要单测它：`axios.create()` 会把当时 `Axios.prototype.request` 的引用抄进
// 实例（`node_modules/axios/lib/axios.js` 的 `bind`），所以改 prototype 只对**之后**
// 建的实例生效。补丁之前建的实例走 axios 的 XHR 适配器，发出来的就是这里发的这种
// 裸 XHR —— 所以只要裸 XHR 由假服务应答、网络层零请求，那种实例就被兜住了。
//
// 判定：两个请求都 status 200、响应体是假数据的 JSON、`net` 为空。
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

const net = []
page.on('request', (r) => {
  if (isApi(r.url())) net.push({ kind: 'request', method: r.method(), url: r.url() })
})
page.on('response', (r) => {
  if (isApi(r.request().url())) net.push({ kind: 'response', status: r.status(), url: r.request().url() })
})
page.on('requestfailed', (r) => {
  if (isApi(r.url())) net.push({ kind: 'requestfailed', url: r.url() })
})
page.on('pageerror', (e) => net.push({ kind: 'pageerror', message: e.message.slice(0, 200) }))

await page.goto(BASE + '#/admin/spaces', { waitUntil: 'domcontentloaded' })
await page.waitForTimeout(3000)

const rawXhr = await page.evaluate(async () => {
  const send = (method, url, body) =>
    new Promise((resolve) => {
      const x = new XMLHttpRequest()
      x.open(method, url)
      x.onloadend = () =>
        resolve({
          status: x.status,
          readyState: x.readyState,
          body: (x.responseText || '').slice(0, 160),
          len: (x.responseText || '').length,
        })
      x.onerror = () => resolve({ status: -1, error: true })
      x.send(body ?? null)
    })
  const get = await send('GET', '/admin/spaces?status=PENDING&offset=0&limit=51')
  const post = await send('POST', '/admin/spaces/42/review', JSON.stringify({ approved: true, reason: '后手验收' }))
  return { get, post }
})

await page.waitForTimeout(1500)
console.log(JSON.stringify({ rawXhr, netCount: net.length, net }, null, 2))
await browser.close()

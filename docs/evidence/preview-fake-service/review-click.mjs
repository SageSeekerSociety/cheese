// 取证：写路径（SpacesApi.review，POST /admin/spaces/:id/review）有没有真的生效、
// 有没有真的出网。
//
// 踩过的坑：用 `button:has-text("通过")` 定位是**子串**匹配，页头 AdminTabs 的
// 「已通过」页签（原生 button、role=tab）会一起被捞进来，取第一个就点到了页签——
// 那是切 APPROVED 筛选，不是审核。所以这里用 `getByRole('button', { name: '通过',
// exact: true })`：role 只认 button（页签是 tab，进不来），名字要全等（「已通过」
// 不等于「通过」）。报告里把候选数和实点次数分开写。
//
// 判定要求四件事同时成立：
//   1. 点到的确实是行内按钮（role 是 button、名字全等「通过」）；
//   2. 假服务收到了这条 POST（URL、id、请求体、响应体都记下来）；
//   3. **那条记录自身**的 reviewStatus 从 PENDING 变 APPROVED（重新 GET 它自己，
//      不看列表条数——条数会随筛选变，不能当写生效的证据）；
//   4. 网络层零请求。
//
// 怎么记 POST：在页面加载完成后往 `window.fetch` 上再包一层，装在预览拦截那一层
// **之上**，被接住的请求也就能看到进出。这样不必往 `proto-feedback-fixtures.ts`
// 里加日志 —— 那个文件是只能缩不能长的超限文件。
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
    return /\/(admin|api|feedback)(\/|$)/.test(new URL(u, 'http://127.0.0.1:5210').pathname)
  } catch {
    return false
  }
}

// 认构建时间：预览是静态包，改了源码不重建就还是旧包。包的 `Last-Modified` 就是构建
// 落盘的时刻，记进输出里，读者才分得清这份 JSON 是哪个包上抓的。
const bundleLastModified = await fetch(new URL('./proto.js', BASE), { method: 'HEAD' })
  .then((r) => r.headers.get('last-modified'))
  .catch(() => null)

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
await page.waitForTimeout(3500)

// 装记录器：包在预览拦截那一层之上，被接住的请求也看得见进出。
await page.evaluate(() => {
  window.__apiLog = []
  const of = window.fetch.bind(window)
  window.fetch = async (input, init) => {
    const raw = typeof input === 'string' ? input : (input && input.url) || String(input)
    const url = new URL(raw, location.origin)
    const method = String((init && init.method) || (input && input.method) || 'GET').toUpperCase()
    // 请求体两处都可能：`init.body`（`src/api.ts` 那条出口），或者整个请求是一个
    // `Request` 对象（axios 的 fetch 适配器是这么发的）—— 只看 `init.body` 会漏掉
    // 写接口的请求体，那正是要记的东西。
    let rawBody = init && typeof init.body === 'string' ? init.body : null
    if (!rawBody && typeof Request !== 'undefined' && input instanceof Request) {
      try {
        rawBody = await input.clone().text()
      } catch {
        rawBody = null
      }
    }
    let body = null
    if (rawBody) {
      try {
        body = JSON.parse(rawBody)
      } catch {
        body = rawBody
      }
    }
    const res = await of(input, init)
    let text = null
    try {
      text = await res.clone().text()
    } catch {
      text = null
    }
    window.__apiLog.push({
      via: 'fetch',
      method,
      url: url.pathname + url.search,
      body,
      status: res.status,
      response: text === null ? null : text.slice(0, 400),
    })
    return res
  }
})

const list = (status) =>
  page.evaluate(async (s) => {
    const res = await fetch(`/admin/spaces?status=${s}&offset=0&limit=51`)
    const j = await res.json()
    const items = (j && j.data && j.data.items) || []
    return {
      status: res.status,
      items: items.map((i) => ({
        id: i.id,
        name: i.name,
        reviewStatus: i.reviewStatus,
        reviewReason: i.reviewReason ?? null,
        reviewedBy: i.reviewedBy ?? null,
      })),
    }
  }, status)

const beforePending = await list('PENDING')

// 定位行内「通过」：role 只认 button，名字全等。同时数一下 role=tab 的同名候选，
// 证明页签确实被排除在外。
const rowButtons = await page.getByRole('button', { name: '通过', exact: true }).all()
const fuzzy = await page.locator('button:has-text("通过")').all()
// 把模糊候选逐个摊开：能看见第 0 个就是页头页签，也就是之前那次误判点到的东西。
const fuzzyWho = []
for (let i = 0; i < fuzzy.length; i += 1) {
  fuzzyWho.push({
    index: i,
    text: ((await fuzzy[i].textContent()) || '').trim(),
    tag: await fuzzy[i].evaluate((el) => el.tagName.toLowerCase()),
    roleAttr: await fuzzy[i].getAttribute('role'),
    // 祖先的 class：分清「页头那排页签」和「列表里行内的按钮」。
    ancestors: await fuzzy[i].evaluate((el) => {
      const out = []
      let n = el.parentElement
      while (n && out.length < 4) {
        if (n.className && typeof n.className === 'string') out.push(n.className.split(' ').slice(0, 2).join('.'))
        n = n.parentElement
      }
      return out
    }),
  })
}
const target = rowButtons[0]
const targetInfo = target
  ? {
      accessibleName: ((await target.textContent()) || '').trim(),
      // 原生 <button> 不写 role 属性，隐式角色就是 button；所以 `roleAttr` 为 null
      // 反而说明它不是页签（页签写的是 role="tab"）。
      roleAttr: await target.getAttribute('role'),
      tag: await target.evaluate((el) => el.tagName.toLowerCase()),
    }
  : null
if (target) await target.click()
await page.waitForTimeout(2500)

const apiLog = await page.evaluate(() => window.__apiLog || [])
const afterPending = await list('PENDING')
const afterApproved = await list('APPROVED')

// 那条记录自身：拿 POST 里带的 id 去 APPROVED 里找它。
const posted = apiLog.filter((e) => e.method === 'POST' && /\/review$/.test(e.url))
const postedId = posted.length ? Number(String(posted[0].url).split('/')[3]) : null
const recordAfter = afterApproved.items.find((i) => i.id === postedId) || null

console.log(
  JSON.stringify(
    {
      capturedAt: new Date().toISOString(),
      previewBase: BASE,
      bundleLastModified,
      targeting: {
        rowButtonCandidates: rowButtons.length,
        fuzzyCandidates: fuzzy.length,
        fuzzyWho,
        clickedTimes: target ? 1 : 0,
        target: targetInfo,
      },
      beforePending: beforePending.items,
      posted,
      recordAfter,
      afterPendingCount: afterPending.items.length,
      afterApprovedIds: afterApproved.items.map((i) => i.id),
      doorsNote: '记录器装在预览拦截之上，被接住的请求也在这里；网络层为空才算零出网',
      net,
    },
    null,
    2
  )
)
await browser.close()

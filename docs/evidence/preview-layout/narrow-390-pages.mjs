// 取证：后台每一格在 390 窄屏（浅色 + 深色）下的横轴有没有把内容裁掉。
//
// 要抓的缺陷是「整段从右侧裁出，而且看不到省略号」。它和横向滚动不是一回事：裁切
// 发生在 `overflow: hidden` 的盒子里，页面根本不出滚动条。而且**不能看盒子自己排不排
// 得下**——这次那个缺陷里，标题盒子是宽的、字在自己盒子里排得下，是外层 `.asp__list`
// 的 `overflow: hidden` 把整个盒子切了，省略号被画到祖先外面，用户看不到。
//
// 所以判据只有一条，加两个不算缺陷的旁注：
//   **静默裁切**（缺陷）：有字的盒子伸出了某个 `overflow-x: hidden` 祖先的内容区。
//   有滚动抓手（不缺陷）：最近祖先 `overflow-x: auto/scroll`，能横滑。
//   页面级横向滚动（不缺陷但记下）：`documentElement.scrollWidth <= clientWidth`。
// 屏幕阅读器专用的 `.visually-hidden` / `.sr-only` 本来就裁，跳过。
//
// 有界逐页：每张在**截图前**把「路由 + 状态 + 宽度 + 主题」写进 manifest，截图后从
// DOM 读回实际渲染的标题与状态。判定只认这份 manifest，不认像素像不像。
//
// 跑法：先把预览构建成 dist-feedback-proto 并挂在 PREVIEW_BASE 上，再
//   TMPDIR=/var/tmp node docs/evidence/preview-layout/narrow-390-pages.mjs
// PNG 落在 OUT_DIR（默认 /var/tmp/shots-390），JSON 走 stdout。
import { createRequire } from 'node:module'
import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const require = createRequire(resolve(here, '../../../e2e/package.json'))
const { chromium } = require('@playwright/test')

const BASE = process.env.PREVIEW_BASE || 'http://127.0.0.1:5210/feedback-proto.html'
const OUT = process.env.OUT_DIR || '/var/tmp/shots-390'
mkdirSync(OUT, { recursive: true })

const bundleLastModified = await fetch(new URL('./proto.js', BASE), { method: 'HEAD' })
  .then((r) => r.headers.get('last-modified'))
  .catch(() => null)

const ROUTES = {
  queue: '#/admin/queue',
  dashboard: '#/admin/dashboard',
  'feature-stats': '#/admin/feature-stats',
  models: '#/admin/models',
  spaces: '#/admin/spaces',
  members: '#/admin/members',
  integrations: '#/admin/integrations',
}

const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM || '/usr/bin/chromium',
  args: ['--no-sandbox', '--disable-dev-shm-usage', '--font-render-hinting=none'],
})

const manifest = []
for (const [page, route] of Object.entries(ROUTES)) {
  for (const theme of ['light', 'dark']) {
    const name = `narrow-390-${theme}-${page}.png`
    // 截图前先记四项，再动手。
    const record = {
      name,
      page,
      route,
      state: 'normal',
      width: 390,
      height: 844,
      theme,
      url: BASE + route,
      phase: 'before-capture',
    }
    manifest.push(record)

    const ctx = await browser.newContext({
      viewport: { width: 390, height: 844 },
      deviceScaleFactor: 1,
      colorScheme: theme === 'dark' ? 'dark' : 'light',
      locale: 'zh-CN',
    })
    const p = await ctx.newPage()
    await p.addInitScript((t) => {
      try {
        localStorage.setItem('cheesex.theme', t)
      } catch {}
    }, theme)
    const net = []
    p.on('request', (r) => {
      if (/\/(admin|api|feedback)(\/|$)/.test(new URL(r.url(), 'http://127.0.0.1').pathname)) net.push(r.url())
    })
    await p.goto(record.url, { waitUntil: 'domcontentloaded', timeout: 30000 }).catch((e) => {
      record.gotoError = e.message
    })
    await p.waitForTimeout(3500)

    // 截图后从 DOM 读回。
    //
    // 判据的准星是**祖先裁切**，不是盒子自己排不下：这次那个缺陷里，标题自己的盒子是
    // 宽的、文字在自己盒子里排得下（`scrollWidth == clientWidth`），是外层
    // `.asp__list` 的 `overflow: hidden` 把整个盒子切掉了。所以找的是「有字的盒子
    // 伸出了某个 `overflow-x: hidden` 祖先的内容区」——那才是静默裁切，且省略号会被
    // 画到祖先外面去，用户看不到。祖先 `overflow-x: auto/scroll` 的算有滚动条这个
    // 抓手，不算缺陷。
    Object.assign(
      record,
      await p.evaluate(() => {
        const vw = document.documentElement.clientWidth
        const clipped = []
        const scrollable = []
        const SKIPPED = /(^|\s)(visually-hidden|sr-only)(\s|$)/
        const boxName = (el) =>
          el.tagName.toLowerCase() + (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.') : '')

        for (const el of document.querySelectorAll('body *')) {
          const txt = (el.textContent || '').replace(/\s+/g, ' ').trim()
          if (!txt || el.children.length > 0) continue // 只看真正含字的叶子盒子
          if (SKIPPED.test(el.className || '')) continue // 屏幕阅读器专用，本来就裁
          const r = el.getBoundingClientRect()
          if (r.width === 0 || r.height === 0) continue
          if (r.bottom < 0 || r.top > window.innerHeight) continue // 视口外的行，还没滚到

          for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) {
            const cs = getComputedStyle(a)
            const ox = cs.overflowX
            if (ox === 'visible') continue
            const ar = a.getBoundingClientRect()
            const overRight = r.right - ar.right
            const overLeft = ar.left - r.left
            if (overRight <= 1 && overLeft <= 1) continue // 没伸出这个祖先
            const rec = {
              text: txt.slice(0, 30),
              leaf: boxName(el),
              leafRight: +r.right.toFixed(1),
              clipper: boxName(a),
              clipperRight: +ar.right.toFixed(1),
              overRight: +overRight.toFixed(1),
              overLeft: +overLeft.toFixed(1),
              clipperOverflowX: ox,
              leafTextOverflow: getComputedStyle(el).textOverflow,
            }
            if (ox === 'hidden') clipped.push(rec)
            else scrollable.push(rec)
            break // 只认最近的那个祖先
          }
        }
        return {
          renderedTitle: (document.querySelector('.aph h1, .aph__title, h1, h2')?.textContent || '').trim().slice(0, 24),
          bodyChars: document.body.innerText.replace(/\s+/g, ' ').trim().length,
          pageScrollWidth: document.documentElement.scrollWidth,
          pageClientWidth: vw,
          noHorizontalScroll: document.documentElement.scrollWidth <= vw + 1,
          // 静默裁切：`overflow-x: hidden` 的祖先切掉了有字的盒子，没有可见省略号。
          silentClipped: clipped.slice(0, 12),
          silentClippedCount: clipped.length,
          // 有滚动条抓手的：祖先 `overflow-x: auto/scroll`，能横滑，不算缺陷。
          inScrollableRegion: scrollable.slice(0, 12),
          inScrollableCount: scrollable.length,
        }
      })
    )
    record.net = net
    record.phase = 'after-capture'
    await p.screenshot({ path: `${OUT}/${name}`, fullPage: false })
    await ctx.close()
    // 每拍一张就落盘，中断也能看到做到哪一步。
    writeFileSync(`${OUT}/manifest.json`, JSON.stringify(manifest, null, 2))
  }
}

const summary = manifest.map((m) => ({
  name: m.name,
  title: m.renderedTitle,
  noHorizontalScroll: m.noHorizontalScroll,
  silentClippedCount: m.silentClippedCount,
  inScrollableCount: m.inScrollableCount,
  net: m.net,
  verdict: m.noHorizontalScroll && m.silentClippedCount === 0 ? 'PASS' : 'FAIL',
}))
console.log(
  JSON.stringify(
    {
      capturedAt: new Date().toISOString(),
      previewBase: BASE,
      bundleLastModified,
      outDir: OUT,
      width: 390,
      pages: Object.keys(ROUTES),
      themes: ['light', 'dark'],
      summary,
      manifest,
    },
    null,
    2
  )
)
await browser.close()

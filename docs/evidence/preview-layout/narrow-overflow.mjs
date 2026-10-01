// 取证：窄屏（390）下空间申请长卡的标题与说明有没有被右侧裁掉。
//
// 判据不是「看着差不多」，是三个可读回的数：
//   1. 行和列表的 scrollWidth <= clientWidth —— 没有横向溢出，就不会被 `overflow: hidden` 裁；
//   2. 标题/说明的盒子右缘 <= 行的内容右缘 —— 没有探出行外；
//   3. 标题/说明的 scrollWidth <= clientWidth —— 文字在自己的盒子里排得下（换行生效）。
// 三个都成立才算「读得全」。省略号不算：这里要的是标题说明读得全，不是读个开头。
//
// 文本用**长中文**和**无空格文本**各压一遍：无空格那一串没有断行机会，正是把
// fit-content 顶宽、造成右侧裁切的那一类内容。
//
// 跑法：先把预览构建成 dist-feedback-proto 并挂在 PREVIEW_BASE 上，再
//   TMPDIR=/var/tmp node docs/evidence/preview-layout/narrow-overflow.mjs
// 输出是 JSON，stdout。
import { createRequire } from 'node:module'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
// playwright 装在 e2e/ 里（那套用 pnpm），从那儿解析，不写死某台机器上的绝对路径。
const require = createRequire(resolve(here, '../../../e2e/package.json'))
const { chromium } = require('@playwright/test')

const BASE = process.env.PREVIEW_BASE || 'http://127.0.0.1:5210/feedback-proto.html'

// 认构建时间：预览是静态包，改了源码不重建就还是旧包。这只是构建落盘时刻的证据，
// 不是代码哈希——源码归属仍以固定 head、构建记录和路由证据为准。
const bundleLastModified = await fetch(new URL('./proto.js', BASE), { method: 'HEAD' })
  .then((r) => r.headers.get('last-modified'))
  .catch(() => null)

const LONG_ZH = '名字很长的样例'.repeat(6) + '这段简介故意写得很长用来试窄屏换行' + '，再补一句让标题说明都超过一行。'
const LONG_NOSPACE = 'Averylongsinglewordwithoutanyspacesorbreakopportunities'.repeat(4)

const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM || '/usr/bin/chromium',
  args: ['--no-sandbox', '--disable-dev-shm-usage', '--font-render-hinting=none'],
})

const results = []
for (const scenario of [
  { width: 390, colorScheme: 'light', label: '390 浅色' },
  { width: 390, colorScheme: 'dark', label: '390 深色' },
  { width: 700, colorScheme: 'light', label: '700 浅色' },
  { width: 1440, colorScheme: 'light', label: '1440 浅色' },
]) {
  const ctx = await browser.newContext({
    viewport: { width: scenario.width, height: 900 },
    colorScheme: scenario.colorScheme,
    locale: 'zh-CN',
  })
  const page = await ctx.newPage()
  const net = []
  page.on('request', (r) => {
    if (/\/(admin|api|feedback)(\/|$)/.test(new URL(r.url(), 'http://127.0.0.1').pathname)) net.push(r.url())
  })

  await page.goto(BASE + '#/admin/spaces', { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(3500)

  // 量一次：换成给定的标题/说明文本再量一次。null 表示用页面上的真实内容。
  const measure = async (title, desc) =>
    page.evaluate(
      ({ title, desc }) => {
        const rows = [...document.querySelectorAll('.asp__row')].filter((r) => r.querySelector('.asp__main'))
        // 长卡优先：标题最长的那一行。没有就取第一行。
        const row =
          rows.slice().sort((a, b) => (b.querySelector('.asp__name')?.textContent || '').length - (a.querySelector('.asp__name')?.textContent || '').length)[0] || rows[0]
        if (!row) return { error: 'no .asp__row with .asp__main on this page' }

        const name = row.querySelector('.asp__name')
        const descs = [...row.querySelectorAll('.asp__desc')]
        if (title && name) name.textContent = title
        if (desc && descs[0]) descs[0].textContent = desc

        const box = (el) => {
          if (!el) return null
          const r = el.getBoundingClientRect()
          return {
            left: +r.left.toFixed(1),
            right: +r.right.toFixed(1),
            width: +r.width.toFixed(1),
            clientWidth: el.clientWidth,
            scrollWidth: el.scrollWidth,
            lines: el.getClientRects().length,
            text: (el.textContent || '').slice(0, 60),
            textLength: (el.textContent || '').length,
          }
        }

        const rowRect = row.getBoundingClientRect()
        const list = row.closest('.asp__list')
        const main = row.querySelector('.asp__main')
        const cs = getComputedStyle(row)
        const nameB = box(name)
        const descB = box(descs[0])
        const overflows = (b) => (b ? b.right > rowRect.right + 0.5 : null)
        // ≤700px 是卡片布局，标题说明要**读得全**：既不能探出行外被裁，也要在自己盒子里排得下。
        // >700px 是带动作列的行式布局，标题 `nowrap` + 省略号是**设计如此**，「排不下」不是缺陷，
        // 所以那档只判「有没有被裁出行外」，不判 fitsOwnBox——否则会把正常的省略号报成失败。
        const narrow = rowRect.width <= 700.5

        return {
          row: {
            flexDirection: cs.flexDirection,
            alignItems: cs.alignItems,
            width: +rowRect.width.toFixed(1),
            clientWidth: row.clientWidth,
            scrollWidth: row.scrollWidth,
          },
          main: box(main),
          list: list ? { clientWidth: list.clientWidth, scrollWidth: list.scrollWidth } : null,
          name: nameB,
          desc: descB,
          checks: {
            rowNoHorizontalOverflow: row.scrollWidth <= row.clientWidth + 0.5,
            listNoHorizontalOverflow: list ? list.scrollWidth <= list.clientWidth + 0.5 : null,
            nameInsideRow: nameB ? !overflows(nameB) : null,
            descInsideRow: descB ? !overflows(descB) : null,
            nameFitsOwnBox: nameB ? nameB.scrollWidth <= nameB.clientWidth + 0.5 : null,
            descFitsOwnBox: descB ? descB.scrollWidth <= descB.clientWidth + 0.5 : null,
          },
          verdict: {
            narrowLayout: narrow,
            nameReadable: nameB ? !overflows(nameB) && (narrow ? nameB.scrollWidth <= nameB.clientWidth + 0.5 : true) : null,
            descReadable: descB ? !overflows(descB) && (narrow ? descB.scrollWidth <= descB.clientWidth + 0.5 : true) : null,
            nameEllipsizedByDesign: narrow ? null : nameB ? nameB.scrollWidth > nameB.clientWidth + 0.5 : null,
          },
        }
      },
      { title, desc }
    )

  results.push({
    label: scenario.label,
    viewport: { width: scenario.width, colorScheme: scenario.colorScheme },
    asIs: await measure(null, null),
    longChinese: await measure(LONG_ZH, LONG_ZH),
    noSpaceText: await measure(LONG_NOSPACE, LONG_NOSPACE),
    net,
  })

  // 自证这套判据不是空过：把修掉的那两条规则按原样注回去（`align-items: flex-start`
  // 复位 + 标题 `nowrap`），同一个包、同一个页面再量一次。它红了，才说明上面那三个
  // 检查抓得住当初那个右侧裁切，而不是不管内容是什么都报「可读」。
  // 先重新加载——上面几次 measure 改过 DOM 文本，不重载会拿改过的文本当「页面原样」。
  if (scenario.width === 390) {
    await page.reload({ waitUntil: 'domcontentloaded' })
    await page.waitForTimeout(3500)
    await page.addStyleTag({
      content:
        '@media (max-width: 700px){ .asp__row{ align-items: flex-start !important; } .asp__name{ white-space: nowrap !important; } }',
    })
    await page.waitForTimeout(200)
    results.push({
      label: scenario.label + '（注回修掉的规则，自证判据有效）',
      viewport: { width: scenario.width, colorScheme: scenario.colorScheme },
      regressionInjected: await measure(null, null),
      regressionInjectedLongChinese: await measure(LONG_ZH, LONG_ZH),
      regressionInjectedNoSpaceText: await measure(LONG_NOSPACE, LONG_NOSPACE),
    })
  }
  await ctx.close()
}

console.log(
  JSON.stringify(
    {
      capturedAt: new Date().toISOString(),
      previewBase: BASE,
      bundleLastModified,
      route: '#/admin/spaces',
      samples: { longChinese: LONG_ZH.slice(0, 40) + `…(${LONG_ZH.length}字)`, noSpaceText: LONG_NOSPACE.slice(0, 40) + `…(${LONG_NOSPACE.length}字符)` },
      results,
    },
    null,
    2
  )
)
await browser.close()

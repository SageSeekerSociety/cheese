// 取证：出错态文案的「现状 / 建议」对照页，以及七个后台页读失败的**真实调用点**渲染。
//
// 为什么要单独取证：出错态文案的差别是**结构性**的（原话当标题 / 原话只在悬停 / 原话被丢了），
// 纸上写「建议改成某某」看不出来。对照页用真实组件把现状 props 和建议 props 并排渲染出来；
// 七页那一步走的是**产品自己的路由和调用点**（`#/admin/...`，不是对照页），从 DOM 读回
// 标题 / 说明行 / 按钮，证明文案真的接进了产品，而不是只在预览页里画着好看。
//
// 判据只认 DOM 读回，不认像素：
//   1. 对照页每一行都有两个 `.cp__stage`，各自的 `.bes__title` / `.bes__desc` / `.bes__btn`
//      文本读回来，等于脚本里写的期望值；
//   2. 七页实机上，`.bes` 块的 title 是中性句式、desc 是服务端原话、按钮是「重试」——
//      三条都要成立，缺一条都算没接上（只改 i18n 不传 `desc` 会在这里现形）；
//   3. 「原话是否可见」看 `document.body.innerText` 里有没有那句原话；
//   4. 队列那层壳的 `title` 属性读回来必须是空的：产品不再把原话挂在那里（对照页那两行
//      还在演示旧结构，所以壳的 `raw` prop 留着，但产品调用点已经不传了）。
//
// 跑法：先把预览构建成 dist-feedback-proto 并挂在 PREVIEW_BASE 上，再
//   TMPDIR=/var/tmp node docs/evidence/preview-copy/copy-and-error-states.mjs
// 输出是 JSON 文件（同目录 manifest.json），stdout 是人看的摘要。
import { createRequire } from 'node:module'
import fs from 'node:fs'
import path from 'node:path'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
// playwright 装在 e2e/ 里（那套用 pnpm），从那儿解析，不写死某台机器上的绝对路径。
const require = createRequire(resolve(here, '../../../e2e/package.json'))
const { chromium } = require('@playwright/test')

const BASE = process.env.PREVIEW_BASE || 'http://127.0.0.1:5210/feedback-proto.html'
const OUT = process.env.SHOT_DIR || '/var/tmp/shots-copy'
fs.mkdirSync(OUT, { recursive: true })

/** 服务端那句原话。预览假服务在 `?fail=1` 下给的就是这一句。 */
const RAW = '服务暂时不可用（样例错误）'

/** 认构建落盘时刻，不当代码哈希用。 */
const bundleLastModified = await fetch(new URL('./proto.js', BASE), { method: 'HEAD' })
  .then((r) => r.headers.get('last-modified'))
  .catch(() => null)

const ROUTES = [
  // `titles` 是这一路上**合法的中性标题**（一页可能有两档，比如队列的列表/表格）。
  // 断言它，才抓得住「只改了 i18n 的值、组件那边还拿原话当标题」这种半接上。
  { key: 'queue', label: '反馈队列', hash: '#/admin/queue', titles: ['队列加载失败', '表格加载失败'] },
  { key: 'dashboard', label: '看板', hash: '#/admin/dashboard', titles: ['看板加载失败'] },
  {
    key: 'feature-stats',
    label: '功能数据',
    hash: '#/admin/feature-stats',
    titles: ['功能数据加载失败'],
  },
  {
    key: 'models',
    label: '模型管理',
    hash: '#/admin/models',
    titles: ['模型加载失败', '额度加载失败', '最近操作加载失败'],
  },
  { key: 'spaces', label: '空间申请', hash: '#/admin/spaces', titles: ['空间申请加载失败'] },
  { key: 'members', label: '成员管理', hash: '#/admin/members', titles: ['成员加载失败'] },
  {
    key: 'integrations',
    label: '飞书应用',
    hash: '#/admin/integrations',
    titles: ['飞书应用加载失败'],
  },
]

const browser = await chromium.launch({ executablePath: '/usr/bin/chromium', args: ['--no-sandbox'] })
const results = []

async function openPage() {
  const ctx = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    colorScheme: 'light',
    locale: 'zh-CN',
    deviceScaleFactor: 1,
  })
  const page = await ctx.newPage()
  const net = []
  page.on('request', (r) => {
    const u = r.url()
    if (!u.startsWith('http://127.0.0.1:5210') && !u.startsWith('data:')) net.push(u)
  })
  return { ctx, page, net }
}

// ── 1) 对照页：真实组件 × 现状 props / 建议 props ───────────────────────
{
  const { ctx, page, net } = await openPage()
  const url = `${BASE}#/copy-preview`
  await page.goto(url, { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(3500)

  const dom = await page.evaluate(() => ({
    heading: document.querySelector('.cp__h1')?.textContent?.trim() ?? '',
    facts: [...document.querySelectorAll('.cp__facts li')].map((li) => li.textContent.trim()),
    rows: [...document.querySelectorAll('.cp__row')].map((row) => ({
      where: row.querySelector('.cp__where')?.textContent?.trim() ?? '',
      id: row.querySelector('.cp__where')?.textContent?.trim().split(/\s+/)[0] ?? '',
      badge: row.querySelector('.cp__badge')?.textContent?.trim() ?? '',
      stages: [...row.querySelectorAll('.cp__stage')].map((st) => ({
        title: st.querySelector('.bes__title')?.textContent?.trim() ?? '',
        desc: st.querySelector('.bes__desc')?.textContent?.trim() ?? '',
        action: st.querySelector('.bes__btn')?.textContent?.trim() ?? '',
      })),
    })),
    writeRows: [...document.querySelectorAll('.cp__table tbody tr')].map((tr) =>
      [...tr.querySelectorAll('td')].map((td) => td.textContent.trim()),
    ),
    hScroll: document.documentElement.scrollWidth > document.documentElement.clientWidth,
  }))

  const shot = path.join(OUT, 'copy-preview-full.png')
  // 这套外壳的滚动发生在内层容器上，`fullPage` 只能截到视口那么高；按元素截又会在
  // 元素高于视口时把下半截留白。所以先把视口撑到内容实际高度再整页截，十行对照才都在。
  const need = await page.evaluate(() => {
    const cp = document.querySelector('.cp')
    const inner = cp?.closest('*') 
    const h = Math.max(document.documentElement.scrollHeight, cp?.scrollHeight ?? 0, cp?.getBoundingClientRect().height ?? 0)
    return Math.min(Math.ceil(h) + 80, 12000)
  })
  await page.setViewportSize({ width: 1440, height: need })
  await page.waitForTimeout(600)
  await page.screenshot({ path: shot, fullPage: true })

  // 判据：每行两个 stage；建议那一栏的说明一律是服务端原话；标题是中性句式。
  const verdict = { rowsWithTwoStages: 0, proposedDescIsRaw: 0, proposedActionIsRetry: 0, total: dom.rows.length }
  for (const r of dom.rows) {
    if (r.stages.length === 2) verdict.rowsWithTwoStages++
    const proposed = r.stages[1]
    if (proposed?.desc === RAW) verdict.proposedDescIsRaw++
    if (proposed?.action === '重试') verdict.proposedActionIsRetry++
  }
  verdict.pass =
    verdict.rowsWithTwoStages === verdict.total &&
    verdict.proposedDescIsRaw === verdict.total &&
    verdict.proposedActionIsRetry === verdict.total &&
    dom.writeRows.length === 5 &&
    dom.hScroll === false

  results.push({
    name: 'copy-preview-full',
    route: '#/copy-preview',
    state: 'comparison',
    width: 1440,
    theme: 'light',
    url,
    bundleLastModified,
    dom,
    verdict,
    net,
    screenshot: shot,
    capturedAt: new Date().toISOString(),
  })
  await ctx.close()
}

// ── 2) 七页读失败实机（现状，真实页面上下文） ───────────────────────────
for (const r of ROUTES) {
  const { ctx, page, net } = await openPage()
  const url = `${BASE}?fail=1${r.hash}`
  await page.goto(url, { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(4000)

  const dom = await page.evaluate((raw) => {
    const blocks = [...document.querySelectorAll('.bes')].map((b) => ({
      title: b.querySelector('.bes__title')?.textContent?.trim() ?? '',
      desc: b.querySelector('.bes__desc')?.textContent?.trim() ?? '',
      action: b.querySelector('.bes__btn')?.textContent?.trim() ?? '',
      // 队列那层 `AdminQueueEmpty` 把原话挂在外层 `title` 上（悬停才看得到）。
      shellTitle: b.parentElement?.getAttribute('title') ?? '',
    }))
    return {
      blocks,
      rawVisible: document.body.innerText.includes(raw),
      hasRetryButton: !!document.querySelector('.bes__btn'),
      hScroll: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    }
  }, RAW)

  const shot = path.join(OUT, `err-${r.key}.png`)
  await page.screenshot({ path: shot, fullPage: false })

  // 原话去向，按 DOM 实际读回判定，三类互斥。
  const anyTitleRaw = dom.blocks.some((b) => b.title === RAW)
  const anyDescRaw = dom.blocks.some((b) => b.desc === RAW)
  const anyShellRaw = dom.blocks.some((b) => b.shellTitle === RAW)
  const rawFate = anyTitleRaw
    ? '当标题显示'
    : anyDescRaw
      ? '作说明行，可见'
      : anyShellRaw
        ? '挂外层 title 属性，悬停可见'
        : '丢了'

  // 接上了没有，判三条：标题中性、原话在说明行、按钮还在。外加壳的 title 上不能有原话。
  const errBlocks = dom.blocks.filter((b) => b.title !== '' || b.desc !== '' || b.action !== '')
  const verdict = {
    rawAsDesc: errBlocks.filter((b) => b.desc === RAW).length,
    rawAsTitle: errBlocks.filter((b) => b.title === RAW).length,
    rawOnShell: dom.blocks.filter((b) => b.shellTitle === RAW).length,
    neutralTitles: errBlocks.filter((b) => b.desc === RAW && r.titles.includes(b.title)).length,
    withRetry: errBlocks.filter((b) => b.action === '重试').length,
    blocks: errBlocks.length,
  }
  verdict.pass =
    verdict.rawAsDesc >= 1 &&
    verdict.rawAsTitle === 0 &&
    verdict.rawOnShell === 0 &&
    verdict.neutralTitles === verdict.rawAsDesc &&
    verdict.withRetry === verdict.rawAsDesc

  results.push({
    name: `err-${r.key}`,
    label: r.label,
    route: r.hash,
    state: 'error',
    width: 1440,
    theme: 'light',
    url,
    bundleLastModified,
    dom,
    rawFate,
    rawVisible: dom.rawVisible,
    verdict,
    expectedTitles: r.titles,
    net,
    screenshot: shot,
    capturedAt: new Date().toISOString(),
  })
  await ctx.close()
}

await browser.close()
fs.writeFileSync(path.join(OUT, 'manifest.json'), JSON.stringify(results, null, 2))

const cmp = results.find((r) => r.name === 'copy-preview-full')
console.log(`bundleLastModified: ${cmp.bundleLastModified}`)
console.log(`capturedAt: ${cmp.capturedAt}`)
console.log(`\n== 对照页 ==  ${JSON.stringify(cmp.verdict)}`)
console.log(`net: ${JSON.stringify(cmp.net)}  hScroll: ${cmp.dom.hScroll}`)
for (const row of cmp.dom.rows) {
  const [now, next] = row.stages
  console.log(` - ${row.where}`)
  console.log(`     现状  ${JSON.stringify(now)}`)
  console.log(`     建议  ${JSON.stringify(next)}`)
}
console.log(`写失败横幅 ${cmp.dom.writeRows.length} 行: ${cmp.dom.writeRows.map((r) => r[0]).join(' ')}`)
console.log(`\n== 七页实机读失败（产品调用点） ==`)
let allPass = cmp.verdict.pass
for (const r of results.filter((x) => x.name.startsWith('err-'))) {
  if (!r.verdict.pass) allPass = false
  console.log(
    `${r.name}\t${r.verdict.pass ? 'PASS' : 'FAIL'}\t原话去向=${r.rawFate}\tbody可见=${r.rawVisible}\t` +
      `原话作说明行=${r.verdict.rawAsDesc} 原话当标题=${r.verdict.rawAsTitle} 原话在壳title=${r.verdict.rawOnShell} ` +
      `中性标题=${r.verdict.neutralTitles} 带重试=${r.verdict.withRetry} 共${r.verdict.blocks}块\t` +
      `net=${JSON.stringify(r.net)}\thScroll=${r.dom.hScroll}`,
  )
}
console.log(`\n全部判据: ${allPass ? 'PASS' : 'FAIL'}`)
process.exit(allPass ? 0 : 1)

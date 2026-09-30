/**
 * 给「产品现状」那两格截图：走组件预览站里的 **产品真组件**（`RoomMessage.vue`），
 * 不是这次要做的 AskFlow。所以它截出来的就是用户现在真的会点到的那个界面。
 *
 * 和 `shoot.mjs` 同一条 CDP 路子，差别只有两处：
 *   - 格子选择器是 `.catalog-case`（预览站给每一格的外壳，标题和说明都在里面），
 *     不是 AskFlow 自己的 `.flow`；
 *   - 不做整页四套 —— 现状对照只要「还没答」和「已经有人答了」两格。
 *
 * 用法：node shot-current.mjs <baseUrl> <outDir> <prefix> <格数|1,6> [宽] [深色] [选择器] [就绪判据]
 */
import { spawn } from 'node:child_process'
import { mkdir, writeFile } from 'node:fs/promises'
import { join } from 'node:path'

const baseUrl = process.argv[2] ?? 'http://localhost:5199/demo/catalog/room-message'
const outDir = process.argv[3] ?? 'shots'
const prefix = process.argv[4] ?? 'now'
const wanted = Number(process.argv[5] ?? 2)
/** 逗号分隔就是「只截这几格」（从 1 数起）；只有一个数就是「截前 N 格」。 */
const picks = String(process.argv[5] ?? '2')
  .split(',')
  .map((n) => Number(n.trim()))
  .filter((n) => Number.isFinite(n) && n > 0)
const only = String(process.argv[5] ?? '').includes(',')
const indices = only ? picks.map((n) => n - 1) : Array.from({ length: wanted }, (_, i) => i)
const viewW = Number(process.argv[6] ?? 1120)
const dark = process.argv[7] === 'dark'
const SEL = process.argv[8] ?? '.catalog-case'
const SELQ = JSON.stringify(SEL)
/** 判「真的画出来了」看哪句字。默认拿现状那格的选项文案当记号。 */
const marker = process.argv[9] ?? '课程平台收文件'

const origin = new URL(baseUrl).origin + '/'
const PORT = 9344
const chrome = spawn(
  'chromium',
  [
    '--headless',
    '--disable-gpu',
    '--no-sandbox',
    '--hide-scrollbars',
    `--remote-debugging-port=${PORT}`,
    '--remote-allow-origins=*',
    '--user-data-dir=/var/tmp/ask-shot-current-profile',
    'about:blank',
  ],
  { stdio: ['ignore', 'ignore', 'ignore'] },
)

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function endpoint() {
  for (let i = 0; i < 60; i += 1) {
    try {
      const res = await fetch(`http://127.0.0.1:${PORT}/json/version`)
      return (await res.json()).webSocketDebuggerUrl
    } catch {
      await sleep(250)
    }
  }
  throw new Error('chromium 没把调试口露出来')
}

class Cdp {
  constructor(url) {
    this.ws = new WebSocket(url)
    this.ws.binaryType = 'arraybuffer'
    this.id = 0
    this.pending = new Map()
    this.ws.addEventListener('message', (event) => {
      const raw = event.data
      const text =
        typeof raw === 'string'
          ? raw
          : raw instanceof ArrayBuffer
            ? new TextDecoder().decode(raw)
            : String(raw)
      const msg = JSON.parse(text)
      if (msg.id === undefined) return
      const handler = this.pending.get(msg.id)
      if (!handler) return
      this.pending.delete(msg.id)
      if (msg.error) handler.reject(new Error(msg.error.message))
      else handler.resolve(msg.result)
    })
  }
  open() {
    return new Promise((resolve, reject) => {
      this.ws.addEventListener('open', resolve, { once: true })
      this.ws.addEventListener('error', reject, { once: true })
    })
  }
  send(method, params = {}, sessionId = undefined) {
    const id = ++this.id
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject })
      this.ws.send(JSON.stringify(sessionId ? { id, method, params, sessionId } : { id, method, params }))
    })
  }
}

const wsUrl = await endpoint()
const cdp = new Cdp(wsUrl)
await cdp.open()

const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' })
const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true })
const call = (method, params = {}) => cdp.send(method, params, sessionId)

await mkdir(outDir, { recursive: true })

await call('Page.enable')
// 主题偏好落在源上再导航（`shoot.mjs` 同款）：产品读的是 localStorage 里那个键。
await call('Page.navigate', { url: origin })
await sleep(700)
await call('Runtime.evaluate', {
  expression: `localStorage.setItem('cheesex.theme', ${JSON.stringify(dark ? 'dark' : 'light')})`,
})
await call('Emulation.setDeviceMetricsOverride', {
  width: viewW,
  height: 1200,
  deviceScaleFactor: 2,
  mobile: false,
})
await call('Page.navigate', { url: baseUrl })

// 等组件真画出来再截：vite 第一次导航还在按需编译，固定睡会截到白页。
let ready = 'not-ready'
for (let i = 0; i < 40; i += 1) {
  await sleep(500)
  ready = (
    await call('Runtime.evaluate', {
      expression: `document.querySelectorAll(${SELQ}).length + '格 | ' + (document.body.textContent.includes(${JSON.stringify(marker)}) ? 'ok' : 'MISSING')`,
      returnByValue: true,
    })
  ).result.value
  if (ready.endsWith('ok')) break
}
if (!ready.endsWith('ok')) {
  cdp.ws.close()
  chrome.kill()
  throw new Error(`要截的那几格没画出来：${ready}`)
}
console.log(`就绪：${ready}`)

const count = (
  await call('Runtime.evaluate', {
    expression: `document.querySelectorAll(${SELQ}).length`,
    returnByValue: true,
  })
).result.value
console.log(`页面上 ${count} 格，截第 ${indices.map((n) => n + 1).join('、')} 格`)

const manifest = []
for (const i of indices) {
  if (i >= count) throw new Error(`第 ${i + 1} 格不在了（一共 ${count} 格）`)
  const info = await call('Runtime.evaluate', {
    expression: `(() => {
      const el = document.querySelectorAll(${SELQ})[${i}]
      if (!el) return JSON.stringify({ miss: true })
      const label = (el.querySelector('.catalog-case-name')?.textContent || '格${i + 1}').trim()
      el.scrollIntoView({ block: 'start' })
      return JSON.stringify({ label })
    })()`,
    returnByValue: true,
  })
  const meta = JSON.parse(info.result.value)
  if (meta.miss) throw new Error(`第 ${i + 1} 格不在了`)
  await sleep(300)
  // 每张图之前重新量一次：字体落位会让后面的格子往下挪，按一开始量的坐标 clip 会越截越空。
  const measure = async () =>
    JSON.parse(
      (
        await call('Runtime.evaluate', {
          expression: `(() => {
            const r = document.querySelectorAll(${SELQ})[${i}].getBoundingClientRect()
            return JSON.stringify({ x: r.x, y: r.y, w: r.width, h: r.height })
          })()`,
          returnByValue: true,
        })
      ).result.value,
    )
  let b = await measure()
  const need = Math.ceil(b.h) + 40
  const innerH = (
    await call('Runtime.evaluate', { expression: 'window.innerHeight', returnByValue: true })
  ).result.value
  if (need > innerH) {
    await call('Emulation.setDeviceMetricsOverride', {
      width: viewW,
      height: need,
      deviceScaleFactor: 2,
      mobile: false,
    })
    await sleep(300)
    await call('Runtime.evaluate', {
      expression: `document.querySelectorAll(${SELQ})[${i}].scrollIntoView({ block: 'start' })`,
    })
    await sleep(250)
    b = await measure()
  }
  const { data } = await call('Page.captureScreenshot', {
    format: 'png',
    captureBeyondViewport: false,
    clip: { x: b.x, y: b.y, width: b.w, height: b.h, scale: 1 },
  })
  const name = `${prefix}-${String(i + 1).padStart(2, '0')}`
  await writeFile(join(outDir, `${name}.png`), Buffer.from(data, 'base64'))
  manifest.push({ file: `${name}.png`, label: meta.label, width: Math.round(b.w), height: Math.round(b.h) })
  console.log(`  ${name}.png  ${Math.round(b.w)}x${Math.round(b.h)}  ${meta.label}`)
}

await writeFile(join(outDir, `${prefix}-manifest.json`), `${JSON.stringify(manifest, null, 2)}\n`)
cdp.ws.close()
chrome.kill()
console.log(`\n清单写在 ${join(outDir, `${prefix}-manifest.json`)}`)
process.exit(0)

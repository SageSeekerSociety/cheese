/**
 * 给预览站的组件逐格截图：每一格单独一张，另加宽屏/窄屏 × 浅色/深色四套整页。
 *
 * 走 Chrome DevTools Protocol 而不是 `--screenshot`，两个理由：
 *   - 深色：`Emulation.setEmulatedMedia` 的 prefers-color-scheme 在这台 chromium 上
 *     压不下去（截出来还是浅色），而产品的主题是 `index.html` 启动脚本读
 *     `localStorage['cheesex.theme']` 决定的 —— 直接写那个键更贴近「用户真选了深色」。
 *   - 窄屏：`Emulation.setDeviceMetricsOverride` 会真的改布局断点，改窗口大小有时不会。
 *
 * **逐格截图**用元素的边界框做 clip：整页截图再缩到一张 A4 上，字会小到读不了，
 * 而 PDF 里每张图都要能看清文案。格子按 DOM 顺序编号，文件名带上它自己的标题拼音
 * 不如直接带上标题原文 —— 这里按序号命名，对应关系写在报告里。
 *
 * 用法：node shoot.mjs <baseUrl> <outDir>
 */
import { spawn } from 'node:child_process'
import { mkdir, writeFile } from 'node:fs/promises'
import { join } from 'node:path'

const baseUrl = process.argv[2] ?? 'http://localhost:5199/demo/catalog/ask-flow'
const outDir = process.argv[3] ?? 'shots'
/** localStorage 按源隔离：写偏好要先落到同一个源上，再导航到目标页。 */
const origin = new URL(baseUrl).origin + '/'

const FULL_SHOTS = [
  { name: 'flow-desktop-light', width: 1120, height: 1500, dark: false },
  { name: 'flow-desktop-dark', width: 1120, height: 1500, dark: true },
  { name: 'flow-mobile-light', width: 390, height: 1900, dark: false },
  { name: 'flow-mobile-dark', width: 390, height: 1900, dark: true },
]

const PORT = 9333
const chrome = spawn(
  'chromium',
  [
    '--headless',
    '--disable-gpu',
    '--no-sandbox',
    '--hide-scrollbars',
    `--remote-debugging-port=${PORT}`,
    '--remote-allow-origins=*',
    '--user-data-dir=/var/tmp/ask-shot-profile',
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
  throw new Error('chromium never exposed the debug endpoint')
}

class Cdp {
  constructor(url) {
    this.ws = new WebSocket(url)
    // Node 的 WebSocket 默认把帧交给 Blob/ArrayBuffer，不是字符串。这里一律
    // 取文本再 parse，免得 JSON.parse 撞上 Blob 报一个看不懂的错。
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
      // flatten 会话里 `sessionId` 走信封，不进 params。
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

/** 落一次主题偏好，再导航到目标页 —— 见文件头。 */
async function setTheme(dark) {
  await call('Page.navigate', { url: origin })
  await sleep(700)
  await call('Runtime.evaluate', {
    expression: `localStorage.setItem('cheesex.theme', ${JSON.stringify(dark ? 'dark' : 'light')})`,
  })
}

/** 等组件真画出来再截。vite 第一次导航还在按需编译，固定睡会截到白页。 */
async function waitReady(selector, marker) {
  let page = 'not-ready'
  for (let i = 0; i < 40; i += 1) {
    await sleep(500)
    const probe = await call('Runtime.evaluate', {
      expression: `document.documentElement.dataset.theme + ' | ' + (${marker})`,
      returnByValue: true,
    })
    page = probe.result.value
    if (page.endsWith('ok')) return page
  }
  throw new Error(`组件没画出来（${selector}）：${page}`)
}

// ── 逐格：每一格单独一张，字看得清 ─────────────────────────
await setTheme(false)
await call('Page.enable')
await call('Emulation.setDeviceMetricsOverride', {
  width: 1120,
  height: 1200,
  deviceScaleFactor: 2,
  mobile: false,
})
await call('Page.navigate', { url: baseUrl })
await waitReady('.flow', "document.querySelectorAll('.flow').length ? 'ok' : 'MISSING'")

// 逐格：每一格单独一张，字看得清。
//
// **每张图之前重新量一次**，别一次量完再连着截：字体和图片落位会让后面的格子往下挪，
// 按一开始量的坐标去 clip 就会越截越空 —— 上面几格看着好好的，后面全是一片白。
// 再把格子滚到视口顶部，clip 用视口坐标，不猜 `captureBeyondViewport` 的坐标系。
const count = (
  await call('Runtime.evaluate', {
    expression: "document.querySelectorAll('.flow').length",
    returnByValue: true,
  })
).result.value
console.log(`逐格：${count} 张`)
for (let i = 0; i < count; i += 1) {
  const info = await call('Runtime.evaluate', {
    expression: `(() => {
      const el = document.querySelectorAll('.flow')[${i}]
      if (!el) return JSON.stringify({ miss: true })
      const label = (el.parentElement?.previousElementSibling?.textContent || '格${i + 1}').trim().slice(0, 22)
      el.scrollIntoView({ block: 'start' })
      return JSON.stringify({ label })
    })()`,
    returnByValue: true,
  })
  const meta = JSON.parse(info.result.value)
  if (meta.miss) throw new Error(`第 ${i + 1} 格不在了`)
  await sleep(300)
  // 格子比视口高就先把视口撑高，免得切掉一截。
  const measure = async () =>
    JSON.parse(
      (
        await call('Runtime.evaluate', {
          expression: `(() => {
            const r = document.querySelectorAll('.flow')[${i}].getBoundingClientRect()
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
      width: 1120,
      height: need,
      deviceScaleFactor: 2,
      mobile: false,
    })
    await sleep(300)
    await call('Runtime.evaluate', {
      expression: `document.querySelectorAll('.flow')[${i}].scrollIntoView({ block: 'start' })`,
    })
    await sleep(250)
    b = await measure()
  }
  const { data } = await call('Page.captureScreenshot', {
    format: 'png',
    captureBeyondViewport: false,
    clip: { x: b.x, y: b.y, width: b.w, height: b.h, scale: 1 },
  })
  const name = `flow-${String(i + 1).padStart(2, '0')}`
  await writeFile(join(outDir, `${name}.png`), Buffer.from(data, 'base64'))
  console.log(`  ${name}.png  ${Math.round(b.w)}x${Math.round(b.h)}  ${meta.label}`)
}

// ── 整页四套：宽屏/窄屏 × 浅色/深色 ───────────────────────
for (const shot of FULL_SHOTS) {
  await call('Emulation.setDeviceMetricsOverride', {
    width: shot.width,
    height: shot.height,
    deviceScaleFactor: 2,
    mobile: shot.width < 500,
  })
  await setTheme(shot.dark)
  await call('Page.navigate', { url: baseUrl })
  const page = await waitReady('.flow', "document.querySelector('.flow') ? 'ok' : 'MISSING'")
  const { data } = await call('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true })
  await writeFile(join(outDir, `${shot.name}.png`), Buffer.from(data, 'base64'))
  console.log(
    `${shot.name}.png  ${shot.width}x${shot.height}  wanted=${shot.dark ? 'dark' : 'light'}  page=${page}`,
  )
}

cdp.ws.close()
chrome.kill()
process.exit(0)

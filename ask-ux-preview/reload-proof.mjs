/**
 * **浏览器真刷新**的证据：localStorage 恢复草稿，并且恢复出来的草稿**不标已答**。
 *
 * 为什么不能用组件测试代替：`unmount()` 再 `render()` 只是重挂同一个 JS 上下文，
 * 弱于浏览器刷新。刷新会把整个 JS 上下文砸掉重建 —— 这里每次 reload 都核对
 * `executionContextId` 真的换了，才敢说「这是刷新」。
 *
 * 恢复要能看出是**从 localStorage 回来的**，不是 `initial*` prop 又给了一遍：所以
 * 先往备注框里打一个随机标记（这个标记不在任何 initial prop 里），刷新后看它还在。
 *
 * **这是预览站的证据，不是产品链路。** 产品还没有可作答的提问。
 *
 * 用法：node reload-proof.mjs <baseUrl> <outJson>
 *   baseUrl 默认 http://localhost:5199/demo/catalog/ask-flow
 */
import { spawn } from 'node:child_process'
import { writeFile } from 'node:fs/promises'

const baseUrl = process.argv[2] ?? 'http://localhost:5199/demo/catalog/ask-flow'
const outJson = process.argv[3] ?? 'reload-proof.json'
const PORT = 9334

const chrome = spawn(
  'chromium',
  [
    '--headless',
    '--disable-gpu',
    '--no-sandbox',
    '--hide-scrollbars',
    `--remote-debugging-port=${PORT}`,
    '--remote-allow-origins=*',
    '--user-data-dir=/var/tmp/ask-reload-profile',
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
    this.ws.binaryType = 'arraybuffer'
    this.id = 0
    this.pending = new Map()
    this.contexts = []
    this.ws.addEventListener('message', (event) => {
      const raw = event.data
      const text =
        typeof raw === 'string'
          ? raw
          : raw instanceof ArrayBuffer
            ? new TextDecoder().decode(raw)
            : String(raw)
      const msg = JSON.parse(text)
      if (msg.method === 'Runtime.executionContextCreated') {
        this.contexts.push(msg.params.context.id)
        return
      }
      if (msg.id === undefined) return
      const handler = this.pending.get(msg.id)
      if (!handler) return
      this.pending.delete(msg.id)
      handler(msg)
    })
  }
  ready() {
    return new Promise((resolve) => {
      if (this.ws.readyState === 1) return resolve()
      this.ws.addEventListener('open', resolve, { once: true })
    })
  }
  send(method, params = {}, sessionId = undefined) {
    this.id += 1
    const id = this.id
    return new Promise((resolve, reject) => {
      this.pending.set(id, (msg) => {
        if (msg.error) reject(new Error(`${method}: ${msg.error.message}`))
        else resolve(msg.result)
      })
      // flatten 会话里 `sessionId` 走信封，不进 params。
      this.ws.send(JSON.stringify(sessionId ? { id, method, params, sessionId } : { id, method, params }))
    })
  }
  /** 在当前 JS 上下文里跑一段表达式，返回它的 JSON 值。 */
  async eval(expression) {
    const res = await this.send(
      'Runtime.evaluate',
      {
        expression,
        awaitPromise: true,
        returnByValue: true,
      },
      this.sessionId,
    )
    if (res.exceptionDetails) {
      throw new Error(`eval failed: ${res.exceptionDetails.text ?? 'unknown'}`)
    }
    return res.result.value
  }
  async navigate(url) {
    await this.send('Page.navigate', { url }, this.sessionId)
    for (let i = 0; i < 80; i += 1) {
      const ready = await this.eval('document.readyState')
      if (ready === 'complete') break
      await sleep(100)
    }
    // 路由是前端自己画的，等它把那一格摆出来。
    for (let i = 0; i < 80; i += 1) {
      const has = await this.eval(`Boolean(document.querySelector('.catalog-case'))`)
      if (has) break
      await sleep(100)
    }
    await sleep(400)
  }
}

const results = []
function check(name, ok, detail) {
  results.push({ name, ok: Boolean(ok), detail: String(detail ?? '') })
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  → ${detail}` : ''}`)
}

let cdp
try {
  cdp = new Cdp(await endpoint())
  await cdp.ready()
  // 浏览器级端点上没有 Page.* —— 要先挂到一个页面 target，`sessionId` 走信封。
  const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' })
  const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true })
  cdp.sessionId = sessionId
  await cdp.send('Page.enable', {}, sessionId)
  await cdp.send('Runtime.enable', {}, sessionId)

  // 先落到同一个源上再导航：localStorage 按源隔离。
  await cdp.navigate(new URL(baseUrl).origin + '/')
  await cdp.navigate(baseUrl)

  const ctxBefore = cdp.contexts.at(-1)
  check('刷新前拿得到 JS 上下文', ctxBefore !== undefined, `contextId=${ctxBefore}`)

  // 找到「⑧ 刷新回来草稿还在」那一格。
  const CASE = '⑧ 刷新回来草稿还在'
  const found = await cdp.eval(`
    (() => {
      const cases = [...document.querySelectorAll('.catalog-case')]
      const hit = cases.find((c) => c.querySelector('.catalog-case-name')?.textContent?.includes('刷新回来草稿还在'))
      if (!hit) return null
      window.__reloadCase = hit
      const note = hit.querySelector('textarea, input[type="text"]')
      if (!note) return { found: true, note: false }
      return { found: true, note: true, tag: note.tagName, ph: note.placeholder ?? '' }
    })()
  `)
  check(`找到那一格「${CASE}」`, found?.found, JSON.stringify(found))

  // 打一个不在任何 initial prop 里的标记 —— 只有 localStorage 才带得回来。
  const marker = `reload-proof-${Date.now()}`
  await cdp.eval(`
    (() => {
      const hit = window.__reloadCase
      const note = hit.querySelector('textarea, input[type="text"]')
      note.focus()
      note.value = ${JSON.stringify(marker)}
      note.dispatchEvent(new Event('input', { bubbles: true }))
      note.dispatchEvent(new Event('change', { bubbles: true }))
      return note.value
    })()
  `)
  const typedOk = await cdp.eval(
    `window.__reloadCase.querySelector('textarea, input[type="text"]').value`,
  )
  check('刷新前把标记打进了备注框', typedOk === marker, `got=${JSON.stringify(typedOk)}`)

  // 存一份，证明确实写进了 localStorage（而不是只在内存里）。
  const rawBefore = await cdp.eval(
    `JSON.stringify(Object.keys(localStorage).filter((k) => k.startsWith('cheesex.askflow.')))`,
  )
  check('草稿写进了 localStorage', String(rawBefore).length > 2, `keys=${rawBefore}`)

  // —— 真刷新 ——
  const before = cdp.contexts.length
  await cdp.send('Page.reload', { ignoreCache: true }, cdp.sessionId)
  for (let i = 0; i < 80; i += 1) {
    const ready = await cdp.eval('document.readyState')
    if (ready === 'complete') break
    await sleep(100)
  }
  for (let i = 0; i < 80; i += 1) {
    const has = await cdp.eval(`Boolean(document.querySelector('.catalog-case'))`)
    if (has) break
    await sleep(100)
  }
  await sleep(500)

  const ctxAfter = cdp.contexts.at(-1)
  check('刷新后 JS 上下文换了（真的是刷新，不是重挂）', ctxAfter !== ctxBefore, `before=${ctxBefore} after=${ctxAfter} 新建上下文数=${cdp.contexts.length - before}`)

  // 刷新后重新找那一格 —— 旧的那个 DOM 节点已经跟着上下文一起没了。
  const after = await cdp.eval(`
    (() => {
      const cases = [...document.querySelectorAll('.catalog-case')]
      const hit = cases.find((c) => c.querySelector('.catalog-case-name')?.textContent?.includes('刷新回来草稿还在'))
      if (!hit) return null
      const note = hit.querySelector('textarea, input[type="text"]')
      const text = hit.textContent ?? ''
      return {
        noteValue: note ? note.value : null,
        draftFlag: text.includes('草稿没交'),
        progress02: text.includes('0/2 已答'),
        receipt: text.includes('选了「'),
        committedMark: text.includes('已答'),
      }
    })()
  `)
  check('刷新后备注框里还是那个标记', after?.noteValue === marker, `got=${JSON.stringify(after?.noteValue)}`)
  check('恢复的是草稿：写着「草稿没交」', after?.draftFlag, `draftFlag=${after?.draftFlag}`)
  check('恢复的草稿**没被标成已答**（进度仍是 0/2）', after?.progress02, `progress02=${after?.progress02}`)
  check('恢复的草稿不出回执（不显示「选了「」）', !after?.receipt, `receipt=${after?.receipt}`)
} catch (err) {
  check('脚本本身跑完', false, err instanceof Error ? err.message : String(err))
} finally {
  try {
    cdp?.ws.close()
  } catch {
    /* ignore */
  }
  chrome.kill('SIGTERM')
}

const passed = results.filter((r) => r.ok).length
const summary = { total: results.length, passed, failed: results.length - passed, results }
await writeFile(outJson, JSON.stringify(summary, null, 2))
console.log(`\n${passed}/${results.length} 通过 → ${outJson}`)
process.exit(summary.failed > 0 ? 1 : 0)

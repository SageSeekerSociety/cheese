// The shell's side of the frame: the document's look, the platform's elements,
// theme and mode, height reporting, errors, and how the code arrives — from the
// parent frame, or written into the page for the platform's check.
//
// It says back `ready` once it can take code, `height` whenever the app's own
// height changes (the box the app draws into, never the page, which is at least
// as tall as the frame and so cannot report getting shorter), and `error` with
// what went wrong, which is also written to the console, where the platform's
// browser reads it.
import 'virtual:doc-tokens.css'
import 'katex/dist/katex.min.css'
import './page.css'

import { registerComponents } from './components'
import { themeChanged } from './theme'

const CHANNEL = 'cheese-app'
// Read by 芝士 when the check refuses a version, and shown as the error's text.
const EMPTY = '运行后页面没有内容：组件没有向页面输出任何元素。'
const RUNAWAY = '小程序的高度持续增长，已停止调整外框高度。常见原因是内嵌时使用了 vh 或 100% 高度。'
const root = document.getElementById('cheese-app') as HTMLElement
const html = document.documentElement

function post(message: object) {
  if (window.parent !== window) window.parent.postMessage({ ch: CHANNEL, ...message }, '*')
}
/** 「…后」, with the space Chinese puts between a number or a Latin letter
 *  and a character. */
export const after = (what: string) => `${what}${/[\w)]$/.test(what) ? ' ' : ''}后`

/** What the platform's check is doing to the app as a reader would
 *  (exercise.ts), so an error it brings out says what brought it out. */
let doing: string | null = null
export function acting(what: string | null) {
  doing = what
}
/** How many times the app has failed, for whoever needs to know whether an
 *  action of theirs made it fail. */
export let failures = 0
export function fail(message: string) {
  failures++
  const said = doing ? `读者${after(doing)}出错：${message}` : message
  console.error(said)
  post({ type: 'error', message: said })
}
addEventListener('error', (e) => fail(e.message || String(e.error)))
// An app checks the numbers its document states with console.assert. One that
// fails is an error like any other, so the platform's check refuses the version;
// the browser itself only logs it, at a level the check does not read.
console.assert = (ok?: unknown, ...what: unknown[]) => {
  if (!ok) fail(`自查未通过：${what.map(String).join(' ') || 'console.assert 的条件不成立'}`)
}
addEventListener('unhandledrejection', (e) => fail(String(e.reason?.message ?? e.reason)))

function setTheme(theme: string) {
  html.dataset.theme = theme === 'dark' ? 'dark' : 'light'
  themeChanged()
}
function setMode(mode: string) {
  html.dataset.mode = mode
}

let last = 0
let grew: number[] = []
const watch = new ResizeObserver(() => {
  const h = Math.ceil(root.getBoundingClientRect().height)
  if (h === last) return
  const now = performance.now()
  grew = h > last ? grew.filter((t) => now - t < 3000).concat(now) : []
  last = h
  if (html.dataset.mode !== 'full' && grew.length > 30) {
    watch.disconnect()
    fail(RUNAWAY)
    return
  }
  post({ type: 'height', h })
})

export function start(mount: (code: string, root: HTMLElement) => void) {
  registerComponents()
  let ran = false
  const run = (code: string) => {
    if (ran) return
    ran = true
    watch.observe(root)
    try {
      mount(code, root)
    } catch (e) {
      fail(String((e as Error)?.message ?? e))
      return
    }
    if (!root.children.length && !root.textContent?.trim()) fail(EMPTY)
  }
  addEventListener('message', (event: MessageEvent) => {
    const d = event.data
    if (event.source !== window.parent || !d || d.ch !== CHANNEL) return
    if (d.type === 'run') {
      if (self.origin !== 'null') return fail('This page runs a mini-app only inside a sandboxed frame.')
      setTheme(d.theme)
      setMode(d.mode === 'full' ? 'full' : 'inline')
      run(String(d.code ?? ''))
    } else if (d.type === 'theme') setTheme(d.theme)
    else if (d.type === 'mode') setMode(d.mode === 'full' ? 'full' : 'inline')
  })
  const boot = (window as { __CHEESE_APP__?: { code: string; theme: string } }).__CHEESE_APP__
  if (boot) {
    setTheme(boot.theme)
    setMode('shot')
    run(boot.code)
  } else post({ type: 'ready' })
}

/** Turn `import … from 'x'` into require calls the shell answers. */
export function requireFrom(modules: Record<string, unknown>) {
  return (name: string) => {
    if (name in modules) return modules[name]
    throw new Error(`无法导入 ${name}：小程序只能导入 ${Object.keys(modules).join('、')}。`)
  }
}

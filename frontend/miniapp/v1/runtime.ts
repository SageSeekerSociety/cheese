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
const EMPTY = '运行后什么都没画出来：组件没有往页面里放任何内容。'
const RUNAWAY = '小程序的高度一直在变大，停止跟随。常见原因：内嵌时用了 vh 或 100% 高度。'
const root = document.getElementById('cheese-app') as HTMLElement
const html = document.documentElement

function post(message: object) {
  if (window.parent !== window) window.parent.postMessage({ ch: CHANNEL, ...message }, '*')
}
export function fail(message: string) {
  console.error(message)
  post({ type: 'error', message })
}
addEventListener('error', (e) => fail(e.message || String(e.error)))
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
    throw new Error(`没有这个库：${name}。小程序只能用外壳自带的库。`)
  }
}

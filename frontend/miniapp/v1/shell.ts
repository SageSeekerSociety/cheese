// Shell 1: the page a document's mini-app runs in.
//
// The document's page frames this with `sandbox="allow-scripts"` on the
// content domain (backend/app/api/app_shell_host.py), then hands it the app's
// code, the theme and whether it is inline or filling the screen. The page holds
// nothing else: the code arrives only from the frame's parent, which read it
// with its reader's permission. The platform's browser, checking a new version,
// has no parent: the code is written into the page as `__CHEESE_APP__`.
//
// What it says back: `ready` once it can take code, `height` whenever the app's
// own height changes (the box the app draws into, never the page, which is at
// least as tall as the frame and so cannot report getting shorter), and
// `error` with what went wrong. An error is also written to the console, which
// is where the platform's browser reads it.
import 'virtual:doc-tokens.css'
import 'katex/dist/katex.min.css'
import './kit.css'

import * as echarts from 'echarts'
import katex from 'katex'

import { createKit } from './kit'

type Mode = 'inline' | 'full' | 'shot'
type Listener = (value: string) => void

const CHANNEL = 'cheese-app'
// i18n-data: read by 芝士 (the check's refusal) and shown as the error's text.
const RUNAWAY = '小程序的高度一直在变大，停止跟随。常见原因：内嵌时用了 vh 或 100% 高度。'
const NOT_SANDBOXED = 'This page runs a mini-app only inside a sandboxed frame.'

const root = document.getElementById('cheese-app') as HTMLElement
const html = document.documentElement
const state = { mode: 'inline' as Mode, theme: 'light' }
const listeners = { mode: new Set<Listener>(), theme: new Set<Listener>() }

const kit = createKit({
  echarts: echarts as never,
  katex: katex as never,
  mode: () => state.mode,
  on: (event, fn) => listeners[event].add(fn),
})
Object.assign(window, { kit, echarts, katex })

function post(message: object): void {
  if (window.parent !== window) window.parent.postMessage({ ch: CHANNEL, ...message }, '*')
}

function fail(message: string): void {
  console.error(message)
  post({ type: 'error', message })
}

addEventListener('error', (event) => fail(event.message || String(event.error)))
addEventListener('unhandledrejection', (event) => fail(String(event.reason?.message ?? event.reason)))

function setTheme(theme: string): void {
  state.theme = theme === 'dark' ? 'dark' : 'light'
  html.dataset.theme = state.theme
  for (const fn of listeners.theme) fn(state.theme)
}

function setMode(mode: Mode, safeBottom = 0): void {
  state.mode = mode
  html.dataset.mode = mode
  html.style.setProperty('--safe-bottom', `${Math.max(0, Math.min(safeBottom, 200))}px`)
  for (const fn of listeners.mode) fn(mode)
}

let last = 0
let grew: number[] = []
const watch = new ResizeObserver(() => {
  const height = Math.ceil(root.getBoundingClientRect().height)
  if (height === last) return
  const now = performance.now()
  grew = height > last ? grew.filter((at) => now - at < 3000).concat(now) : []
  last = height
  if (state.mode !== 'full' && grew.length > 30) {
    watch.disconnect()
    fail(RUNAWAY)
    return
  }
  post({ type: 'height', h: height })
})

let ran = false
/** Put the app's markup in, then run its scripts in order. */
function run(code: string): void {
  if (ran) return
  ran = true
  const box = document.createElement('template')
  box.innerHTML = code
  const scripts = [...box.content.querySelectorAll('script')]
  for (const script of scripts) script.remove()
  root.append(box.content)
  watch.observe(root)
  for (const old of scripts) {
    const script = document.createElement('script')
    script.textContent = old.textContent
    document.body.append(script)
  }
}

addEventListener('message', (event: MessageEvent) => {
  const data = event.data
  if (event.source !== window.parent || !data || data.ch !== CHANNEL) return
  if (data.type === 'run') {
    // Code handed in by a parent runs only where it cannot reach anyone's
    // session: in a sandboxed frame, whose origin is opaque.
    if (self.origin !== 'null') return fail(NOT_SANDBOXED)
    setTheme(data.theme)
    setMode(data.mode === 'full' ? 'full' : 'inline', Number(data.safeBottom) || 0)
    run(String(data.code ?? ''))
  } else if (data.type === 'theme') {
    setTheme(data.theme)
  } else if (data.type === 'mode') {
    setMode(data.mode === 'full' ? 'full' : 'inline', Number(data.safeBottom) || 0)
  }
})

const boot = (window as { __CHEESE_APP__?: { code: string; theme: string } }).__CHEESE_APP__
if (boot) {
  setTheme(boot.theme)
  setMode('shot')
  run(boot.code)
} else {
  post({ type: 'ready' })
}

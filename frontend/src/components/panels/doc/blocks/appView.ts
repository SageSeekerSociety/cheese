// A mini-app block (`:::app <key>@<version>`): a head with its title and
// version, the sentence that says what it shows, and the app itself, run in a
// sandboxed frame on the content domain. Shared by the editor's node view
// (./blockViews.ts) and the reader (./reader.ts).
//
// The frame runs the platform's shell; the code is read here, with the
// reader's permission (lib/docApps.ts), and handed in. A phone first shows the
// picture taken when the version was kept and runs the app when tapped; a wide
// screen runs it once it is near. Full screen enlarges this same frame — moving
// a frame reloads it, and the reader would lose whatever they had done — and
// the system's back gesture, Esc or the back button puts it back.
import type { NodeViewRendererProps } from '@tiptap/core'
import type { Node as PMNode } from '@tiptap/pm/model'
import type { NodeView } from '@tiptap/pm/view'
import type { DocAppVersion } from '../../../../lib/docApps'
import type { AgentHook } from './mermaidView'
import type { PopoverItem } from './popover'

import { docApps, problemOf } from '../../../../lib/docApps'

import { controlButton, menuAt } from './popover'
import { onThemeChange, posOf } from './viewKit'

import { t } from '@/i18n'

const CHANNEL = 'cheese-app'
const PHONE = '(max-width: 600px)'
/** The widths the platform took pictures at (backend `web_snippet/check.py`). */
const SHOT_WIDTHS = [400, 720]

export interface AppAttrs {
  app: string
  version: number
  title: string
}

export interface AppBlockOptions {
  /** Show the version menu: switching it changes the document's `@<version>`. */
  setVersion?: (version: number) => void
  /** Hand the app to the AI teammate (the editor's), when there is one. */
  agent?: { name: string; run: () => void } | null
}

export interface AppBlock {
  dom: HTMLElement
  /** Where the block's sentence goes: the editor's content, or the reader's text. */
  text: HTMLElement
  /** Load and show the app; returns what takes it down. */
  start: () => () => void
  update: (attrs: AppAttrs) => void
}

const PROBLEM: Record<ReturnType<typeof problemOf>, string> = {
  forbidden: 'forbidden',
  missing: 'missing',
  failed: 'loadFailed',
}

const label = (key: string, params?: Record<string, unknown>) => t(`work.room.doc.blocks.app.${key}`, params ?? {})

function theme(): 'light' | 'dark' {
  return document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light'
}

/** The bottom inset of a phone's screen (its home bar), in pixels. */
function safeBottom(): number {
  const probe = document.createElement('div')
  probe.style.cssText = 'position:fixed;bottom:0;height:0;padding-bottom:env(safe-area-inset-bottom)'
  document.body.append(probe)
  const inset = probe.getBoundingClientRect().height
  probe.remove()
  return Math.round(inset)
}

function scroller(el: HTMLElement): HTMLElement | null {
  for (let at = el.parentElement; at; at = at.parentElement) {
    if (/(auto|scroll)/.test(getComputedStyle(at).overflowY)) return at
  }
  return null
}

export function appBlock(initial: AppAttrs, opts: AppBlockOptions = {}): AppBlock {
  let attrs = initial
  const dom = document.createElement('div')
  dom.dataset.block = 'app'
  dom.className = 'doc-app'
  // The part that fills the screen; `dom` keeps its place in the document.
  const box = document.createElement('div')
  box.className = 'doc-app__box'
  const head = document.createElement('div')
  head.className = 'doc-app__head'
  head.contentEditable = 'false'
  const back = controlButton('doc-app__back', label('back'), '←')
  const title = document.createElement('span')
  title.className = 'doc-app__title'
  const version = document.createElement('span')
  version.className = 'doc-app__version'
  const sourceButton = controlButton('doc-app__tool doc-app__inline', label('source'), label('source'))
  const fullButton = controlButton('doc-app__tool doc-app__inline', label('full'), label('full'))
  const moreButton = controlButton('doc-app__tool doc-app__more', label('more'), '⋯')
  moreButton.setAttribute('aria-haspopup', 'menu')
  head.append(back, title, version, sourceButton, fullButton, moreButton)
  const text = document.createElement('div')
  text.className = 'doc-app__text'
  const stage = document.createElement('div')
  stage.className = 'doc-app__stage'
  stage.contentEditable = 'false'
  const source = document.createElement('div')
  source.className = 'doc-app__source'
  source.contentEditable = 'false'
  source.hidden = true
  box.append(head, text, stage, source)
  dom.append(box)

  let loaded: DocAppVersion | null = null
  let frame: HTMLIFrameElement | null = null
  let natural = 0
  let pushed = false
  const teardown: (() => void)[] = []

  function paintHead() {
    title.textContent = attrs.title || label('untitled')
    version.textContent = label('version', { n: attrs.version })
    moreButton.hidden = !opts.setVersion && !opts.agent
  }
  paintHead()

  function say(message: string, actions: HTMLElement[] = []) {
    frame = null
    stage.classList.remove('is-capped')
    const note = document.createElement('div')
    note.className = 'doc-app__note'
    if (message) {
      const words = document.createElement('div')
      words.textContent = message
      note.append(words)
    }
    if (actions.length) {
      const row = document.createElement('div')
      row.className = 'doc-app__actions'
      row.append(...actions)
      note.append(row)
    }
    stage.replaceChildren(note)
  }

  function fail(message: string) {
    const actions: HTMLElement[] = []
    if (opts.agent) {
      const fix = controlButton(
        'doc-app__action',
        label('fix', { name: opts.agent.name }),
        label('fix', { name: opts.agent.name })
      )
      fix.addEventListener('click', () => opts.agent?.run())
      actions.push(fix)
    }
    say(label('failed', { message }), actions)
    stage.classList.add('is-failed')
  }

  /** The room the app takes before it reports its own: its height when it was kept. */
  function reserve(v: DocAppVersion): number {
    const width = stage.clientWidth || SHOT_WIDTHS[1]
    const nearest = SHOT_WIDTHS.reduce((a, b) => (Math.abs(b - width) < Math.abs(a - width) ? b : a))
    return v.heights[String(nearest)] ?? 0
  }

  function cap(): number {
    return Math.round(window.innerHeight * 1.5)
  }

  function size(height: number) {
    natural = height
    if (!frame || dom.classList.contains('is-full')) return
    const before = frame.getBoundingClientRect()
    const target = Math.max(48, Math.min(height, cap()))
    frame.style.height = `${target}px`
    stage.classList.toggle('is-capped', height > cap())
    // Taller above where the reader is: keep what they are reading where it was.
    const delta = target - before.height
    const area = scroller(dom)
    const top = area ? area.getBoundingClientRect().top : 0
    if (delta && before.bottom < top) {
      if (area) area.scrollTop += delta
      else window.scrollBy(0, delta)
    }
  }

  function post(message: object) {
    frame?.contentWindow?.postMessage({ ch: CHANNEL, ...message }, '*')
  }

  function mode() {
    const full = dom.classList.contains('is-full')
    post({ type: 'mode', mode: full ? 'full' : 'inline', safeBottom: full ? safeBottom() : 0 })
  }

  function run(v: DocAppVersion) {
    if (!v.shell_url) return say(label('unavailable'))
    const el = document.createElement('iframe')
    el.className = 'doc-app__frame'
    el.setAttribute('sandbox', 'allow-scripts')
    el.title = attrs.title || label('untitled')
    const reserved = reserve(v)
    if (reserved) el.style.height = `${Math.min(reserved, cap())}px`
    let loads = 0
    // A second load is the app sending its own frame elsewhere: stop it there.
    el.addEventListener('load', () => {
      if (++loads > 1) fail(label('navigated'))
    })
    const listen = (event: MessageEvent) => {
      const data = event.data
      if (event.source !== el.contentWindow || !data || data.ch !== CHANNEL) return
      if (data.type === 'ready') {
        const full = dom.classList.contains('is-full')
        post({
          type: 'run',
          code: v.code,
          theme: theme(),
          mode: full ? 'full' : 'inline',
          safeBottom: full ? safeBottom() : 0,
        })
      } else if (data.type === 'height' && typeof data.h === 'number' && Number.isFinite(data.h)) {
        size(Math.max(0, Math.min(data.h, 100_000)))
      } else if (data.type === 'error') {
        fail(String(data.message ?? '').slice(0, 500))
      }
    }
    window.addEventListener('message', listen)
    teardown.push(() => window.removeEventListener('message', listen))
    el.src = v.shell_url
    frame = el
    const more = controlButton('doc-app__all', label('showAll'), label('showAll'))
    more.addEventListener('click', enterFull)
    stage.classList.remove('is-failed')
    stage.replaceChildren(el, more)
  }

  /** On a phone, the picture first; the app runs when tapped. */
  async function picture(v: DocAppVersion) {
    const all = docApps()
    const name = `400-${theme()}`
    const start = controlButton('doc-app__run', label('run'), label('run'))
    start.addEventListener('click', () => run(v))
    if (!all || !v.shots.includes(name)) return say('', [start])
    const img = document.createElement('img')
    img.className = 'doc-app__shot'
    img.alt = attrs.title
    try {
      img.src = await all.shot(attrs.app, attrs.version, 400, theme())
    } catch {
      return say('', [start])
    }
    teardown.push(() => URL.revokeObjectURL(img.src))
    const wrap = document.createElement('div')
    wrap.className = 'doc-app__picture'
    wrap.append(img, start)
    stage.replaceChildren(wrap)
  }

  async function load() {
    const all = docApps()
    if (!all) return say(label('unavailable'))
    stage.replaceChildren()
    try {
      loaded = await all.version(attrs.app, attrs.version)
    } catch (error) {
      loaded = null
      return say(label(PROBLEM[problemOf(error)], { n: attrs.version }))
    }
    source.hidden = true
    if (window.matchMedia(PHONE).matches && !dom.classList.contains('is-full')) await picture(loaded)
    else run(loaded)
  }

  function toggleSource() {
    if (!source.hidden) {
      source.hidden = true
      return
    }
    if (!loaded) return
    const pre = document.createElement('pre')
    pre.textContent = loaded.code
    const copy = controlButton('doc-app__action', label('copy'), label('copy'))
    copy.addEventListener('click', () => void navigator.clipboard?.writeText(loaded?.code ?? ''))
    source.replaceChildren(copy, pre)
    source.hidden = false
  }

  function onKey(event: KeyboardEvent) {
    if (event.key === 'Escape') exitFull()
  }
  function onPop() {
    pushed = false
    exitFull()
  }

  function enterFull() {
    if (dom.classList.contains('is-full')) return
    dom.style.height = `${dom.offsetHeight}px`
    dom.classList.add('is-full')
    if (!frame && loaded) run(loaded)
    if (frame) frame.style.height = ''
    try {
      history.pushState({ ...history.state }, '')
      pushed = true
    } catch {
      pushed = false
    }
    window.addEventListener('popstate', onPop)
    document.addEventListener('keydown', onKey)
    mode()
    back.focus()
  }

  function exitFull() {
    if (!dom.classList.contains('is-full')) return
    dom.classList.remove('is-full')
    dom.style.height = ''
    window.removeEventListener('popstate', onPop)
    document.removeEventListener('keydown', onKey)
    if (pushed) {
      pushed = false
      history.back()
    }
    mode()
    if (natural) size(natural)
  }

  function openMenu() {
    const items: PopoverItem[] = []
    if (opts.agent) {
      const agent = opts.agent
      items.push({ label: label('ask', { name: agent.name }), run: () => agent.run() })
    }
    const switchTo = opts.setVersion
    const all = docApps()
    if (!switchTo || !all) return menuAt(moreButton, items)
    void all.app(attrs.app).then(
      (app) => {
        for (const v of [...app.versions].reverse()) {
          items.push({
            label: label('version', { n: v.version }),
            hint: v.note || undefined,
            pressed: v.version === attrs.version,
            run: () => switchTo(v.version),
          })
        }
        menuAt(moreButton, items)
      },
      () => menuAt(moreButton, items)
    )
  }

  back.addEventListener('click', exitFull)
  sourceButton.addEventListener('click', toggleSource)
  fullButton.addEventListener('click', enterFull)
  moreButton.addEventListener('click', openMenu)

  let started = false
  return {
    dom,
    text,
    start() {
      started = true
      void load()
      const stopTheme = onThemeChange(() => post({ type: 'theme', theme: theme() }))
      return () => {
        stopTheme()
        exitFull()
        for (const stop of teardown.splice(0)) stop()
        stage.replaceChildren()
        frame = null
        started = false
      }
    },
    update(next) {
      const changed = next.app !== attrs.app || next.version !== attrs.version
      attrs = next
      paintHead()
      if (changed && started) {
        for (const stop of teardown.splice(0)) stop()
        void load()
      }
    },
  }
}

// ---- In the editor

function attrsOf(node: PMNode): AppAttrs {
  return { app: node.attrs.app as string, version: node.attrs.version as number, title: node.attrs.title as string }
}

/** The editor's view of a mini-app block: its sentence is edited in place, the
 *  rest is controls. Switching the version changes the document's `@<version>`
 *  — going back to an older version is an edit like any other. */
export function appNodeView(agent: AgentHook) {
  return ({ node, editor, getPos }: NodeViewRendererProps): NodeView => {
    let current = node
    const hook = agent()
    const block = appBlock(attrsOf(node), {
      setVersion: editor.isEditable
        ? (version) => {
            const pos = posOf(getPos)
            if (pos === null || !editor.isEditable) return
            editor.view.dispatch(editor.state.tr.setNodeMarkup(pos, undefined, { ...current.attrs, version }))
          }
        : undefined,
      agent: hook
        ? {
            name: hook.name,
            run: () => {
              const pos = posOf(getPos)
              if (pos !== null) hook.run(editor, pos, pos + current.nodeSize)
            },
          }
        : null,
    })
    let stop: (() => void) | null = null
    const near =
      typeof IntersectionObserver === 'undefined'
        ? null
        : new IntersectionObserver(
            (entries) => {
              if (!entries.some((entry) => entry.isIntersecting) || stop) return
              near?.disconnect()
              stop = block.start()
            },
            { rootMargin: '400px 0px' }
          )
    if (near) near.observe(block.dom)
    else stop = block.start()
    return {
      dom: block.dom,
      contentDOM: block.text,
      update(next) {
        if (next.type !== current.type) return false
        current = next
        block.update(attrsOf(next))
        return true
      },
      // Everything but the sentence is the view's own: its frame, its menus,
      // its size while it fills the screen.
      ignoreMutation: (mutation) => !block.text.contains(mutation.target),
      stopEvent: (event) => !block.text.contains(event.target as Node),
      destroy() {
        near?.disconnect()
        stop?.()
      },
    }
  }
}

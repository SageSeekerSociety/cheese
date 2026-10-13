// A mini-app block (`:::app <key>@<version>`), drawn as the prototype of
// #2423 settled it: a head with the title and the sentence under it, a full
// screen button and a ⋯ menu (its versions, the source, ask the AI teammate,
// copy a link that opens it full screen); under the head, the app. Shared by
// the editor's node view and the reader (./reader.ts).
//
// The frame runs the platform's shell; the code is read here, with the
// reader's permission (lib/docApps.ts), and handed in. A phone first shows
// the picture taken when the version was kept and runs the app on a tap; a
// wide screen runs it once it is near. Full screen pins this same block over
// the page — moving a frame reloads it, and the reader would lose whatever
// they had done — and the back button, the system's back gesture or Esc put
// it back where the reader was.
import type { NodeViewRendererProps } from '@tiptap/core'
import type { Node as PMNode } from '@tiptap/pm/model'
import type { NodeView } from '@tiptap/pm/view'
import type { DocApp, DocAppVersion } from '../../../../lib/docApps'
import type { AgentHook } from './mermaidView'
import type { PopoverItem } from './popover'

import { docApps, problemOf } from '../../../../lib/docApps'

import { controlButton, menuAt } from './popover'
import { onThemeChange, posOf } from './viewKit'

import { t } from '@/i18n'
import { relTime } from '@/lib/relTime'

const CHANNEL = 'cheese-app'
const PHONE = '(max-width: 600px)'
/** The widths the platform took pictures at (backend `web_snippet/check.py`). */
const SHOT_WIDTHS = [400, 720]
/** The address parameter that opens an app full screen: what 复制链接 copies. */
const LINK_PARAM = 'app'

const ICONS = {
  back: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M12.5 4.5 7 10l5.5 5.5"/></svg>',
  full: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4 8V4h4M16 8V4h-4M4 12v4h4M16 12v4h-4"/></svg>',
  more: '<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="4.5" cy="10" r="1.4"/><circle cx="10" cy="10" r="1.4"/><circle cx="15.5" cy="10" r="1.4"/></svg>',
}

export interface AppAttrs {
  app: string
  version: number
  title: string
}

export interface AppBlockOptions {
  /** Change the version the document shows (恢复成这一版, 退回): an edit. */
  setVersion?: (version: number) => void
  /** Hand the app to the AI teammate, when there is one; `request` is said for
   *  the reader, as if typed into its box. */
  agent?: { name: string; run: (request?: string) => void } | null
}

export interface AppBlock {
  dom: HTMLElement
  /** Where the block's sentence goes: the editor's content, or the reader's text. */
  text: HTMLElement
  /** Load and show the app; returns what takes it down. */
  start: () => () => void
  update: (attrs: AppAttrs) => void
}

const label = (key: string, params?: Record<string, unknown>) => t(`work.room.doc.blocks.app.${key}`, params ?? {})

const PROBLEM: Record<ReturnType<typeof problemOf>, string> = {
  forbidden: 'forbidden',
  missing: 'missing',
  failed: 'loadFailed',
}

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

function iconButton(className: string, name: string, icon: string): HTMLButtonElement {
  const button = controlButton(`doc-app__icon ${className}`, name)
  button.innerHTML = icon
  return button
}

function textButton(className: string, text: string): HTMLButtonElement {
  return controlButton(className, text, text)
}

/** A short note at the bottom of the screen. */
function toast(message: string): void {
  const note = document.createElement('div')
  note.className = 'doc-app-toast'
  note.setAttribute('role', 'status')
  note.textContent = message
  document.body.append(note)
  window.setTimeout(() => note.remove(), 2400)
}

/** Ancestors that would hold a fixed box inside themselves (a transform, a
 *  containment, an off-screen skip) let go while the app fills the screen. */
function release(el: HTMLElement): () => void {
  const undo: (() => void)[] = []
  for (let at = el.parentElement; at && at !== document.body; at = at.parentElement) {
    const style = getComputedStyle(at)
    const holds =
      style.transform !== 'none' ||
      style.filter !== 'none' ||
      style.contain !== 'none' ||
      style.contentVisibility === 'auto' ||
      style.willChange.includes('transform')
    if (!holds) continue
    const before = at.getAttribute('style')
    at.style.setProperty('transform', 'none', 'important')
    at.style.setProperty('filter', 'none', 'important')
    at.style.setProperty('contain', 'none', 'important')
    at.style.setProperty('content-visibility', 'visible', 'important')
    at.style.setProperty('will-change', 'auto', 'important')
    const node = at
    undo.push(() => (before === null ? node.removeAttribute('style') : node.setAttribute('style', before)))
  }
  return () => {
    for (const step of undo.reverse()) step()
  }
}

/** The source of one version, in a sheet over the page. */
function showSource(title: string, v: DocAppVersion): void {
  const sheet = document.createElement('div')
  sheet.className = 'doc-app-sheet'
  const body = document.createElement('div')
  body.className = 'doc-app-sheet__body'
  body.setAttribute('role', 'dialog')
  body.setAttribute('aria-modal', 'true')
  const name = label('sourceTitle', { title, n: v.version })
  body.setAttribute('aria-label', name)
  const head = document.createElement('div')
  head.className = 'doc-app-sheet__head'
  const words = document.createElement('div')
  words.className = 'doc-app-sheet__title'
  const strong = document.createElement('span')
  strong.textContent = name
  const note = document.createElement('span')
  note.className = 'doc-app-sheet__note'
  note.textContent = label('sourceNote')
  words.append(strong, note)
  const copy = textButton('doc-app__button', label('copy'))
  const close = textButton('doc-app__button', label('close'))
  head.append(words, copy, close)
  const pre = document.createElement('pre')
  pre.textContent = v.code.trim()
  body.append(head, pre)
  sheet.append(body)
  const done = () => {
    sheet.remove()
    document.removeEventListener('keydown', key, true)
  }
  const key = (event: KeyboardEvent) => {
    if (event.key !== 'Escape') return
    event.stopPropagation()
    done()
  }
  copy.addEventListener('click', () => {
    void navigator.clipboard?.writeText(v.code.trim()).then(
      () => toast(label('copied')),
      () => undefined
    )
  })
  close.addEventListener('click', done)
  sheet.addEventListener('click', (event) => {
    if (event.target === sheet) done()
  })
  document.addEventListener('keydown', key, true)
  document.body.append(sheet)
  close.focus()
}

export function appBlock(initial: AppAttrs, opts: AppBlockOptions = {}): AppBlock {
  let attrs = initial
  /** The version on screen: the document's, or one picked from the menu. */
  let shown = initial.version
  const dom = document.createElement('div')
  dom.dataset.block = 'app'
  dom.className = 'doc-app'
  // What fills the screen; `dom` keeps the block's place in the document.
  const box = document.createElement('div')
  box.className = 'doc-app__box'
  const head = document.createElement('div')
  head.className = 'doc-app__head'
  const back = iconButton('doc-app__back', label('back'), ICONS.back)
  const titles = document.createElement('div')
  titles.className = 'doc-app__titles'
  const name = document.createElement('div')
  name.className = 'doc-app__name'
  name.contentEditable = 'false'
  const title = document.createElement('span')
  const version = document.createElement('span')
  version.className = 'doc-app__version'
  name.append(title, version)
  const text = document.createElement('div')
  text.className = 'doc-app__text'
  titles.append(name, text)
  const fullButton = iconButton('doc-app__full', label('full'), ICONS.full)
  const moreButton = iconButton('doc-app__more', label('more'), ICONS.more)
  moreButton.setAttribute('aria-haspopup', 'menu')
  head.append(back, titles, fullButton, moreButton)
  const stage = document.createElement('div')
  stage.className = 'doc-app__stage'
  stage.contentEditable = 'false'
  box.append(head, stage)
  dom.append(box)

  let app: DocApp | null = null
  let loaded: DocAppVersion | null = null
  let frame: HTMLIFrameElement | null = null
  let natural = 0
  let pushed = false
  let held: (() => void) | null = null
  let place = { window: 0, area: 0 }
  const teardown: (() => void)[] = []

  function paintHead() {
    title.textContent = attrs.title || label('untitled')
    version.textContent = shown === attrs.version ? '' : label('version', { n: shown })
  }
  paintHead()

  async function versions(): Promise<DocApp | null> {
    if (app) return app
    try {
      app = (await docApps()?.app(attrs.app)) ?? null
    } catch {
      app = null
    }
    return app
  }

  function clearStage() {
    for (const stop of teardown.splice(0)) stop()
    frame = null
    natural = 0
    stage.classList.remove('is-capped')
    stage.replaceChildren()
  }

  function say(message: string) {
    clearStage()
    const note = document.createElement('div')
    note.className = 'doc-app__note'
    note.textContent = message
    stage.append(note)
  }

  async function fail(message: string) {
    clearStage()
    const box = document.createElement('div')
    box.className = 'doc-app__error'
    const words = document.createElement('div')
    words.textContent = label('failedTitle')
    const detail = document.createElement('div')
    detail.className = 'doc-app__message'
    detail.textContent = message
    const row = document.createElement('div')
    row.className = 'doc-app__actions'
    if (opts.agent) {
      const agent = opts.agent
      const fix = textButton('doc-app__button doc-app__button--primary', label('fix', { name: agent.name }))
      fix.addEventListener('click', () => agent.run(label('fixRequest', { app: attrs.app, n: shown, message })))
      row.append(fix)
    }
    box.append(words, detail, row)
    stage.append(box)
    const setVersion = opts.setVersion
    const earlier = setVersion ? (await versions())?.versions.filter((v) => v.version < shown).at(-1) : undefined
    if (setVersion && earlier) {
      const undo = textButton('doc-app__button', label('backTo', { n: earlier.version }))
      undo.addEventListener('click', () => setVersion(earlier.version))
      row.append(undo)
    }
  }

  /** The room the app takes before it reports its own: its height when kept. */
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
    const target = Math.max(120, Math.min(height, cap()))
    frame.style.height = `${target}px`
    stage.classList.toggle('is-capped', height > cap())
    // Taller above where the reader is: keep what they are reading where it was.
    const delta = target - before.height
    const area = scroller(dom)
    const top = area ? area.getBoundingClientRect().top : 0
    if (delta && before.bottom < top + 80) {
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

  function oldBanner(v: DocAppVersion): HTMLElement | null {
    if (shown === attrs.version) return null
    const banner = document.createElement('div')
    banner.className = 'doc-app__old'
    const words = document.createElement('span')
    words.textContent = v.note ? label('viewing', { n: shown, note: v.note }) : label('viewingPlain', { n: shown })
    banner.append(words)
    const setVersion = opts.setVersion
    if (setVersion) {
      const restore = textButton('doc-app__button', label('restore'))
      restore.addEventListener('click', () => setVersion(shown))
      banner.append(restore)
    }
    return banner
  }

  function run(v: DocAppVersion) {
    if (!v.shell_url) return say(label('unavailable'))
    const picture = stage.querySelector('img')
    const reserved = picture?.offsetHeight || reserve(v)
    clearStage()
    const banner = oldBanner(v)
    if (banner) stage.append(banner)
    const el = document.createElement('iframe')
    el.setAttribute('sandbox', 'allow-scripts')
    el.title = attrs.title || label('untitled')
    // Hold the room the picture took, so the page does not jump as it starts.
    if (reserved) el.style.height = `${Math.min(reserved, cap())}px`
    let loads = 0
    // A second load is the app sending its own frame elsewhere: stop it there.
    el.addEventListener('load', () => {
      if (++loads > 1) void fail(label('navigated'))
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
        void fail(String(data.message ?? '').slice(0, 500))
      }
    }
    window.addEventListener('message', listen)
    teardown.push(() => window.removeEventListener('message', listen))
    el.src = v.shell_url
    frame = el
    const all = textButton('doc-app__all', label('showAll'))
    all.addEventListener('click', enterFull)
    stage.append(el, all)
  }

  /** The picture taken when the version was kept, run on a tap. */
  async function picture(v: DocAppVersion) {
    clearStage()
    const button = document.createElement('button')
    button.type = 'button'
    button.className = 'doc-app__shot'
    const pill = document.createElement('span')
    pill.className = 'doc-app__tap'
    pill.textContent = label('tapToRun')
    button.append(pill)
    button.addEventListener('click', () => run(v))
    stage.append(button)
    const name = `400-${theme()}`
    const all = docApps()
    if (!all || !v.shots.includes(name)) {
      button.classList.add('is-empty')
      return
    }
    try {
      const url = await all.shot(attrs.app, v.version, 400, theme())
      teardown.push(() => URL.revokeObjectURL(url))
      const img = document.createElement('img')
      img.alt = label('shotAlt', { title: attrs.title })
      img.src = url
      button.prepend(img)
    } catch {
      button.classList.add('is-empty')
    }
  }

  async function load(opening = false) {
    const all = docApps()
    if (!all) {
      dom.classList.add('is-closed')
      return say(label('unavailable'))
    }
    try {
      loaded = await all.version(attrs.app, shown)
    } catch (error) {
      loaded = null
      dom.classList.add('is-closed')
      return say(label(PROBLEM[problemOf(error)], { n: shown }))
    }
    dom.classList.remove('is-closed')
    const linked = opening && new URLSearchParams(location.search).get(LINK_PARAM) === attrs.app
    if (window.matchMedia(PHONE).matches && !linked && !dom.classList.contains('is-full')) await picture(loaded)
    else run(loaded)
    if (linked) enterFull()
  }

  function switchTo(v: number) {
    shown = v
    paintHead()
    void load()
  }

  function onKey(event: KeyboardEvent) {
    if (event.key === 'Escape' && !document.querySelector('.doc-app-sheet')) exitFull()
  }
  function onPop() {
    pushed = false
    exitFull()
  }

  function enterFull() {
    if (dom.classList.contains('is-full')) return
    const area = scroller(dom)
    place = { window: window.scrollY, area: area?.scrollTop ?? 0 }
    dom.style.height = `${dom.offsetHeight}px`
    held = release(dom)
    dom.classList.add('is-full')
    document.documentElement.classList.add('doc-app-open')
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
    document.documentElement.classList.remove('doc-app-open')
    dom.style.height = ''
    held?.()
    held = null
    window.removeEventListener('popstate', onPop)
    document.removeEventListener('keydown', onKey)
    if (pushed) {
      pushed = false
      history.back()
    }
    mode()
    if (natural) size(natural)
    const area = scroller(dom)
    if (area) area.scrollTop = place.area
    window.scrollTo(0, place.window)
    fullButton.focus({ preventScroll: true })
  }

  async function openMenu() {
    const items: PopoverItem[] = []
    const known = await versions()
    for (const [i, v] of [...(known?.versions ?? [])].reverse().entries()) {
      const when = relTime(v.created_at)
      items.push({
        heading: i === 0 ? label('versions') : undefined,
        label: v.version === attrs.version ? label('current', { n: v.version }) : label('version', { n: v.version }),
        hint: [when, v.note].filter(Boolean).join(' · ') || undefined,
        pressed: v.version === shown,
        run: () => switchTo(v.version),
      })
    }
    items.push({
      divided: items.length > 0,
      label: label('viewSource'),
      run: () => loaded && showSource(attrs.title || label('untitled'), loaded),
    })
    const agent = opts.agent
    if (agent) items.push({ label: label('ask', { name: agent.name }), run: () => agent.run() })
    items.push({
      label: label('copyLink'),
      run: () => {
        const link = new URL(location.href)
        link.searchParams.set(LINK_PARAM, attrs.app)
        void navigator.clipboard?.writeText(link.toString()).then(
          () => toast(label('linkCopied')),
          () => undefined
        )
      },
    })
    menuAt(moreButton, items)
  }

  back.addEventListener('click', exitFull)
  fullButton.addEventListener('click', enterFull)
  moreButton.addEventListener('click', () => void openMenu())

  let started = false
  return {
    dom,
    text,
    start() {
      started = true
      void load(true)
      const stopTheme = onThemeChange(() => post({ type: 'theme', theme: theme() }))
      return () => {
        stopTheme()
        exitFull()
        clearStage()
        started = false
      }
    },
    update(next) {
      const changed = next.app !== attrs.app || next.version !== attrs.version
      if (next.app !== attrs.app) app = null
      attrs = next
      if (changed) shown = next.version
      paintHead()
      if (changed && started) void load()
    },
  }
}

// ---- In the editor

function attrsOf(node: PMNode): AppAttrs {
  return { app: node.attrs.app as string, version: node.attrs.version as number, title: node.attrs.title as string }
}

/** The editor's view of a mini-app block: its sentence is edited in place, the
 *  rest is controls. Going back to a version changes the document's
 *  `@<version>` — an edit like any other, kept in the document's history. */
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
            run: (request) => {
              const pos = posOf(getPos)
              if (pos !== null) hook.run(editor, pos, pos + current.nodeSize, request)
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

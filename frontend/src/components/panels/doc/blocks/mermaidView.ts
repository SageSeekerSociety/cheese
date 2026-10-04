// Code blocks in the editor, and the one language that is drawn: mermaid.
//
// A mermaid block shows its diagram. Its source is the block's text, edited
// like any code block: it opens while the caret is in it ("源码" puts the
// caret there), so two people can edit it and a suggestion reaches every
// character. Changing a diagram is usually easier said than typed, so the
// block also offers to hand it to the document's AI teammate.
//
// On a narrow screen a left-to-right flowchart is drawn top to bottom (only
// what is drawn; the source keeps its direction), a wide diagram shrinks to
// three quarters at most and then scrolls sideways, and a tap opens it full
// screen.
//
// Mermaid is loaded the first time a diagram is on screen.
import type { Editor, NodeViewRendererProps } from '@tiptap/core'
import type { NodeView } from '@tiptap/pm/view'

import { TextSelection } from '@tiptap/pm/state'

import { controlButton } from './popover'
import { onThemeChange, posOf, token } from './viewKit'

import { t } from '@/i18n'

/** Hands a stretch of the document to the AI teammate; null while there is none. */
export type AgentHook = () => { name: string; run: (editor: Editor, from: number, to: number) => void } | null

type Mermaid = typeof import('mermaid').default

let loading: Promise<Mermaid> | null = null
let renders = 0

function loadMermaid(): Promise<Mermaid> {
  loading ??= import('mermaid').then((m) => m.default)
  return loading
}

const NARROW = 560
const MIN_SCALE = 0.75
// Mermaid lays out on the page's thread; a pasted log labelled mermaid would
// freeze the document, so past this size the block stays source text.
const MAX_SOURCE = 20_000

/** What mermaid is given: on a narrow screen a sideways flowchart stands up. */
export function drawnSource(source: string, narrow: boolean): string {
  if (!narrow) return source
  return source.replace(/^(\s*(?:flowchart|graph)\s+)(LR|RL)\b/m, '$1TD')
}

async function renderDiagram(source: string): Promise<string> {
  const mermaid = await loadMermaid()
  mermaid.initialize({
    startOnLoad: false,
    securityLevel: 'strict',
    theme: 'base',
    logLevel: 'fatal',
    fontFamily: getComputedStyle(document.body).fontFamily,
    themeVariables: {
      background: 'transparent',
      fontSize: '14px',
      primaryColor: token('--canvas'),
      primaryTextColor: token('--text'),
      primaryBorderColor: token('--faint'),
      secondaryColor: token('--surface'),
      tertiaryColor: token('--surface'),
      lineColor: token('--muted'),
      textColor: token('--text'),
      noteBkgColor: token('--canvas'),
      noteTextColor: token('--text'),
      noteBorderColor: token('--line'),
      actorBkg: token('--canvas'),
      actorBorder: token('--faint'),
      actorTextColor: token('--text'),
      signalColor: token('--muted'),
      signalTextColor: token('--text'),
    },
    // Flat shapes like the rest of the document: the default "neo" look adds
    // drop shadows and gradient outlines.
    look: 'classic',
    flowchart: { useMaxWidth: false, htmlLabels: false, padding: 12 },
    sequence: { useMaxWidth: false },
    gantt: { useMaxWidth: false },
    state: { useMaxWidth: false },
  })
  await mermaid.parse(source)
  const id = `doc-mermaid-${++renders}`
  try {
    return (await mermaid.render(id, source)).svg
  } finally {
    document.getElementById(id)?.remove()
    document.getElementById(`d${id}`)?.remove()
  }
}

/** The diagram's own width, from its viewBox. */
function naturalWidth(svg: SVGSVGElement): number {
  return svg.viewBox?.baseVal?.width || svg.getBoundingClientRect().width
}

/** Size the diagram to the column: never past its own size, never under 75%. */
function fit(figure: HTMLElement): void {
  const svg = figure.querySelector('svg')
  if (!svg) return
  const own = naturalWidth(svg)
  const room = figure.clientWidth
  if (!own || !room) return
  const width = own <= room ? own : Math.max(room, own * MIN_SCALE)
  svg.style.width = `${width}px`
  svg.style.maxWidth = 'none'
  svg.style.height = 'auto'
  figure.classList.toggle('doc-mermaid__figure--scroll', width > room)
}

function fullScreen(svg: string): void {
  const layer = document.createElement('div')
  layer.className = 'doc-mermaid-full'
  layer.setAttribute('role', 'dialog')
  layer.setAttribute('aria-modal', 'true')
  const close = document.createElement('button')
  close.type = 'button'
  close.className = 'doc-mermaid-full__close'
  close.textContent = t('work.room.doc.blocks.closeDiagram')
  const body = document.createElement('div')
  body.className = 'doc-mermaid-full__body'
  body.innerHTML = svg
  layer.append(close, body)
  const shut = () => {
    layer.remove()
    document.removeEventListener('keydown', key, true)
  }
  const key = (e: KeyboardEvent) => {
    if (e.key !== 'Escape') return
    e.preventDefault()
    shut()
  }
  close.addEventListener('click', shut)
  document.addEventListener('keydown', key, true)
  document.body.append(layer)
  close.focus()
}

function plainView({ node }: NodeViewRendererProps): NodeView {
  const dom = document.createElement('pre')
  const code = document.createElement('code')
  const paint = (language: string | null) => {
    if (language) dom.dataset.language = language
    else delete dom.dataset.language
    code.className = language ? `language-${language}` : ''
  }
  paint(node.attrs.language as string | null)
  dom.append(code)
  return {
    dom,
    contentDOM: code,
    update(next) {
      if (next.type !== node.type || next.attrs.language === 'mermaid') return false
      paint(next.attrs.language as string | null)
      return true
    },
  }
}

/** A diagram drawn from `read()` into `dom`: the figure (a tap opens it full
 *  screen) and the line that says why it cannot be drawn. It redraws when the
 *  theme changes, when the width crosses the narrow line, and on `redraw`. */
export function diagramFigure(
  dom: HTMLElement,
  read: () => string
): { figure: HTMLElement; error: HTMLElement; redraw: () => void; destroy: () => void } {
  let svg = ''
  let timer = 0
  let alive = true
  const figure = document.createElement('div')
  figure.className = 'doc-mermaid__figure'
  figure.contentEditable = 'false'
  figure.setAttribute('role', 'button')
  figure.setAttribute('aria-label', t('work.room.doc.blocks.openDiagram'))
  const error = document.createElement('div')
  error.className = 'doc-mermaid__error'
  error.contentEditable = 'false'

  const draw = async () => {
    const text = read().trim()
    error.textContent = ''
    dom.classList.toggle('doc-mermaid--empty', !text)
    if (!text) {
      svg = ''
      figure.textContent = t('work.room.doc.blocks.emptyDiagram')
      return
    }
    if (text.length > MAX_SOURCE) {
      svg = ''
      figure.textContent = ''
      dom.classList.add('doc-mermaid--error')
      error.textContent = t('work.room.doc.blocks.diagramTooLong')
      return
    }
    try {
      const drawn = await renderDiagram(drawnSource(text, dom.clientWidth < NARROW))
      if (!alive) return
      svg = drawn
      figure.innerHTML = drawn
      fit(figure)
      dom.classList.remove('doc-mermaid--error')
    } catch (e) {
      if (!alive) return
      dom.classList.add('doc-mermaid--error')
      const reason = e instanceof Error ? e.message.split('\n')[0] : ''
      error.textContent = t('work.room.doc.blocks.diagramError', { reason })
    }
  }
  const later = () => {
    clearTimeout(timer)
    timer = window.setTimeout(() => void draw(), 300)
  }
  requestAnimationFrame(() => void draw())
  figure.addEventListener('click', () => {
    if (svg) fullScreen(svg)
  })

  let width = 0
  const resize = new ResizeObserver(() => {
    const now = dom.clientWidth
    // Crossing the narrow line changes how a flowchart is drawn.
    if (width && now < NARROW !== width < NARROW) later()
    else fit(figure)
    width = now
  })
  resize.observe(dom)
  const stopTheme = onThemeChange(() => void draw())
  return {
    figure,
    error,
    redraw: later,
    destroy() {
      alive = false
      clearTimeout(timer)
      resize.disconnect()
      stopTheme()
    },
  }
}

function mermaidView({ node, editor, getPos }: NodeViewRendererProps, agent: AgentHook): NodeView {
  let source = node.textContent

  const dom = document.createElement('div')
  dom.dataset.block = 'mermaid'
  const bar = document.createElement('div')
  bar.className = 'doc-mermaid__bar'
  bar.contentEditable = 'false'
  const edit = controlButton('doc-mermaid__edit', t('work.room.doc.blocks.diagramSource'))
  edit.textContent = t('work.room.doc.blocks.diagramSource')
  const ask = controlButton('doc-mermaid__ask', '')
  bar.append(edit, ask)
  const { figure, error, redraw, destroy } = diagramFigure(dom, () => source)
  const pre = document.createElement('pre')
  pre.dataset.language = 'mermaid'
  const code = document.createElement('code')
  code.className = 'language-mermaid'
  pre.append(code)
  dom.append(bar, figure, error, pre)

  const paintAsk = () => {
    const hook = agent()
    ask.hidden = !hook
    if (hook) ask.textContent = t('work.room.doc.blocks.askAgent', { name: hook.name })
  }
  paintAsk()

  // The source is open while the caret is in it.
  const track = () => {
    const pos = posOf(getPos)
    const { $from } = editor.state.selection
    let inside = false
    for (let d = $from.depth; d > 0; d--) if ($from.before(d) === pos) inside = true
    dom.classList.toggle('doc-mermaid--source', inside && editor.isEditable)
  }
  editor.on('selectionUpdate', track)
  editor.on('focus', track)

  /** Put the caret at the end of the source, which opens it. */
  const openSource = (): { from: number; to: number } | null => {
    const pos = posOf(getPos)
    const current = pos === null ? null : editor.state.doc.nodeAt(pos)
    if (pos === null || !current || !editor.isEditable) return null
    const end = pos + current.nodeSize - 1
    editor.view.dispatch(editor.state.tr.setSelection(TextSelection.create(editor.state.doc, end)).scrollIntoView())
    editor.view.focus()
    return { from: pos + 1, to: end }
  }
  edit.addEventListener('click', () => void openSource())
  // The AI teammate's box is anchored to the words it is about: show them.
  ask.addEventListener('click', () => {
    const hook = agent()
    const range = hook ? openSource() : null
    if (hook && range && range.to > range.from) hook.run(editor, range.from, range.to)
  })

  return {
    dom,
    contentDOM: code,
    update(next) {
      if (next.type !== node.type || next.attrs.language !== 'mermaid') return false
      paintAsk()
      if (next.textContent !== source) {
        source = next.textContent
        redraw()
      }
      return true
    },
    ignoreMutation: (m) => (m.type === 'attributes' && m.target === dom) || !pre.contains(m.target),
    stopEvent: (e) => figure.contains(e.target as Node),
    destroy() {
      destroy()
      editor.off('selectionUpdate', track)
      editor.off('focus', track)
    },
  }
}

/** The view for every code block: mermaid is drawn, any other language is code. */
export function codeBlockView(agent: AgentHook) {
  return (props: NodeViewRendererProps): NodeView =>
    props.node.attrs.language === 'mermaid' ? mermaidView(props, agent) : plainView(props)
}

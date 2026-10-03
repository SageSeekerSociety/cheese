// Markdown shown for reading: a message, a file, a weekly report, an old
// version of a document. It is read by the document's own parser into the
// document's blocks and drawn the way a document draws them, so the same text
// looks the same in a message and in a document.
//
// Nothing here is written as HTML: the page is built from the parsed blocks,
// so only what the schema knows can appear (no script, no stray tag), and a
// link's address passes the editor's own check.
//
// How the text is read (and the chat's one difference) is lib/docRead.ts.
import 'katex/dist/katex.min.css'

import type { Node as PMNode } from '@tiptap/pm/model'
import type { ReadAs } from '../../../../lib/docRead'
import type { RefNames } from '../../../../lib/refChip'

import { DOMSerializer } from '@tiptap/pm/model'

import { readMarkdown, schema } from '../../../../lib/docRead'
import { lowlight } from '../../../../lib/docSchema'
import { refChip, refTokens } from '../../../../lib/refChip'

import { chartCanvas, chartToggle, kindLabel, tableRows } from './chartView'
import { diagramFigure } from './mermaidView'
import { controlButton, popoverAt } from './popover'
import {
  calloutShape,
  columnShape,
  detailsShape,
  fitTimeline,
  footnoteNote,
  footnoteText,
  mathShape,
  READING,
  statItemShape,
  statsShape,
  tableFit,
  timelineItemShape,
  timelineShape,
  trendOf,
} from './shapes'

import { t } from '@/i18n'

// ---- Code: highlighted the way the editor highlights it

interface Hast {
  type: string
  value?: string
  tagName?: string
  properties?: { className?: string[] }
  children?: Hast[]
}

function hastToDom(nodes: Hast[], into: Node): void {
  for (const node of nodes) {
    if (node.type === 'text') into.appendChild(document.createTextNode(node.value ?? ''))
    else if (node.type === 'element') {
      const el = document.createElement(node.tagName ?? 'span')
      if (node.properties?.className) el.className = node.properties.className.join(' ')
      hastToDom(node.children ?? [], el)
      into.appendChild(el)
    }
  }
}

const COPIED_MS = 1500

/** A code block with a 复制 button in its corner. */
function withCopy(pre: HTMLElement): HTMLElement {
  const box = document.createElement('div')
  box.className = 'md-pre'
  const copy = document.createElement('button')
  copy.type = 'button'
  copy.className = 'md-copy'
  copy.textContent = t('work.room.message.copy')
  copy.addEventListener('click', () => {
    navigator.clipboard.writeText(pre.textContent ?? '').then(
      () => {
        copy.textContent = t('work.room.message.copied')
        setTimeout(() => (copy.textContent = t('work.room.message.copy')), COPIED_MS)
      },
      () => {}
    )
  })
  box.append(copy, pre)
  return box
}

function codeShape(node: PMNode): HTMLElement {
  const language = node.attrs.language as string | null
  const pre = document.createElement('pre')
  if (language) pre.dataset.language = language
  const code = document.createElement('code')
  if (language) code.className = `language-${language}`
  const text = node.textContent
  try {
    const tree =
      language && lowlight.registered(language) ? lowlight.highlight(language, text) : lowlight.highlightAuto(text)
    hastToDom(tree.children as Hast[], code)
  } catch {
    code.textContent = text
  }
  pre.append(code)
  return pre
}

// ---- Drawing the blocks

type Later = (el: HTMLElement, start: () => () => void) => void

function serializer(doc: PMNode, later: Later, opts: ReadOptions): DOMSerializer {
  const base = DOMSerializer.fromSchema(schema)
  const nodes: DOMSerializer['nodes'] = {
    ...base.nodes,
    callout(node) {
      const label = document.createElement('span')
      const { dom, content } = calloutShape(node.attrs.kind as string, label)
      return { dom, contentDOM: content }
    },
    timeline() {
      const dom = timelineShape()
      later(dom, () => {
        fitTimeline(dom)
        // Web fonts change how wide the times are.
        void document.fonts?.ready.then(() => fitTimeline(dom))
        return () => {}
      })
      return { dom, contentDOM: dom }
    },
    timelineItem() {
      const dot = document.createElement('span')
      dot.setAttribute('aria-hidden', 'true')
      const { dom, content } = timelineItemShape(dot)
      return { dom, contentDOM: content }
    },
    timelineBody(node) {
      // A reader does not need to see an empty description.
      return ['div', { 'data-field': 'body', class: node.content.size ? null : 'doc-field--blank' }, 0]
    },
    statDelta(node) {
      return ['div', { 'data-field': 'delta', 'data-trend': trendOf(node.textContent) || null }, 0]
    },
    stats() {
      const { dom, cards } = statsShape()
      return { dom, contentDOM: cards }
    },
    statItem() {
      const { dom, content } = statItemShape()
      return { dom, contentDOM: content }
    },
    column() {
      const { dom, content } = columnShape()
      return { dom, contentDOM: content }
    },
    details() {
      const toggle = controlButton('doc-details__toggle', t('work.room.doc.blocks.toggleDetails'))
      const { dom, content } = detailsShape(toggle, false)
      return { dom, contentDOM: content }
    },
    mathInline: (node) => mathShape(node.attrs.latex as string, false),
    mathBlock: (node) => mathShape(node.attrs.latex as string, true),
    footnoteRef(node) {
      const label = node.attrs.label as string
      const dom = document.createElement('sup')
      dom.dataset.footnoteRef = label
      dom.textContent = label
      dom.addEventListener('click', () =>
        popoverAt(dom.getBoundingClientRect(), footnoteNote(footnoteText(doc, label)))
      )
      return dom
    },
    table(node) {
      const { shape } = tableFit(node)
      const dom = document.createElement('div')
      dom.className = 'tableWrapper'
      dom.dataset.shape = shape
      const table = document.createElement('table')
      const body = document.createElement('tbody')
      table.append(body)
      dom.append(table)
      return { dom, contentDOM: body }
    },
    chart(node) {
      const dom = document.createElement('div')
      dom.dataset.block = 'chart'
      const bar = document.createElement('div')
      bar.className = 'doc-chart__bar'
      const kind = document.createElement('span')
      kind.className = 'doc-chart__kind'
      kind.textContent = kindLabel(node.attrs.kind as string, Boolean(node.attrs.horizontal))
      const data = document.createElement('div')
      data.className = 'doc-chart__data'
      bar.append(kind, chartToggle(dom, data))
      dom.append(bar)
      later(dom, () => {
        const { canvas, destroy } = chartCanvas(() => ({
          kind: node.attrs.kind,
          horizontal: Boolean(node.attrs.horizontal),
          rows: tableRows(node),
        }))
        bar.after(canvas)
        return destroy
      })
      dom.append(data)
      return { dom, contentDOM: data }
    },
    codeBlock(node) {
      if (node.attrs.language !== 'mermaid') return opts.copyCode ? withCopy(codeShape(node)) : codeShape(node)
      const dom = document.createElement('div')
      dom.dataset.block = 'mermaid'
      later(dom, () => {
        const { figure, error, destroy } = diagramFigure(dom, () => node.textContent)
        dom.append(figure, error)
        return destroy
      })
      return dom
    },
  }
  return new DOMSerializer(nodes, base.marks)
}

/** Each value under its column's name, for when a table's rows become cards. */
function labelCards(root: HTMLElement): void {
  for (const body of Array.from(root.querySelectorAll('.tableWrapper[data-shape="cards"] tbody'))) {
    const rows = Array.from(body.children)
    const names = Array.from(rows[0]?.children ?? []).map((cell) => cell.textContent?.trim() ?? '')
    for (const row of rows.slice(1)) {
      Array.from(row.children).forEach((cell, col) => ((cell as HTMLElement).dataset.label = names[col] ?? ''))
    }
  }
}

/** Turn the reference tokens in the drawn text into chips (not inside code). */
function chips(root: HTMLElement, names: RefNames): void {
  const texts: Text[] = []
  const walk = (node: Node) => {
    for (const child of Array.from(node.childNodes)) {
      if (child.nodeType === Node.TEXT_NODE) texts.push(child as Text)
      else if (child.nodeType === Node.ELEMENT_NODE && !['PRE', 'CODE'].includes((child as Element).tagName))
        walk(child)
    }
  }
  walk(root)
  for (const text of texts) {
    const found = refTokens(text.data)
    if (!found.length) continue
    const parts: Node[] = []
    let at = 0
    for (const ref of found) {
      if (ref.index > at) parts.push(document.createTextNode(text.data.slice(at, ref.index)))
      parts.push(refChip(ref.kind, ref.id, names))
      at = ref.index + ref.length
    }
    if (at < text.data.length) parts.push(document.createTextNode(text.data.slice(at)))
    text.replaceWith(...parts)
  }
}

export interface ReadOptions {
  as?: ReadAs
  names?: RefNames
  /** Put a 复制 button on each code block. */
  copyCode?: boolean
}

const NO_NAMES: RefNames = { mentionNames: {}, topicTitles: {} }

/** Draw `md` into `host`. Charts and diagrams are drawn once they come near
 *  the screen. The returned function takes it all down again. */
export function mountMarkdown(host: HTMLElement, md: string, opts: ReadOptions = {}): () => void {
  const doc = readMarkdown(md, opts.as)
  const pending: { el: HTMLElement; start: () => () => void }[] = []
  const fragment = serializer(doc, (el, start) => pending.push({ el, start }), opts).serializeFragment(doc.content)
  host.classList.add(READING)
  host.replaceChildren(fragment)
  labelCards(host)
  chips(host, opts.names ?? NO_NAMES)

  const stops: (() => void)[] = []
  // Where nothing reports what is on screen (a test page), draw everything now.
  const seen =
    typeof IntersectionObserver === 'undefined'
      ? null
      : new IntersectionObserver(
          (entries) => {
            for (const entry of entries) {
              if (!entry.isIntersecting) continue
              seen?.unobserve(entry.target)
              const job = pending.find((p) => p.el === entry.target)
              if (job) stops.push(job.start())
            }
          },
          { rootMargin: '400px 0px' }
        )
  for (const job of pending) {
    if (seen) seen.observe(job.el)
    else stops.push(job.start())
  }
  return () => {
    seen?.disconnect()
    for (const stop of stops) stop()
    host.replaceChildren()
  }
}

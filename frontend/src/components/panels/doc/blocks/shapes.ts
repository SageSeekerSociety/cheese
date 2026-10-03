// What each block looks like on the page, built without an editor.
//
// The schema's own markup (lib/docSchema/blocks.ts) is plain and readable;
// the stylesheet (styles/docBlocks.css) is written for the richer shape here:
// a callout's label above its content, a timeline's dot and line, the wrapper
// a phone's table layout keys on. The editor's views and the reader
// (./reader.ts) both build that shape from these functions, so a block looks
// the same in a document, a message and a file. A view adds its controls and
// handlers on top; the reader adds only what a reader uses.
import type { Node as PMNode } from '@tiptap/pm/model'

import { t } from '@/i18n'

/** The class a reading surface carries: the editor while it cannot be edited,
 *  and every reader. The stylesheet's reading rules key on it. */
export const READING = 'doc-reading'

// ---- Callout

export function calloutLabel(kind: string): string {
  return t(`work.room.doc.blocks.kinds.${kind}`)
}

/** `label` is the editor's kind button, or the reader's plain label. */
export function calloutShape(kind: string, label: HTMLElement): { dom: HTMLElement; content: HTMLElement } {
  const dom = document.createElement('div')
  dom.dataset.block = 'callout'
  dom.dataset.kind = kind
  label.classList.add('doc-callout__kind')
  label.textContent = calloutLabel(kind)
  const content = document.createElement('div')
  dom.append(label, content)
  return { dom, content }
}

// ---- Timeline

export function timelineShape(): HTMLElement {
  const dom = document.createElement('div')
  dom.dataset.block = 'timeline'
  return dom
}

/** Make the time column as wide as the longest time. Each time is laid out
 *  on one line; an empty time still needs room for its hint. */
export function fitTimeline(dom: HTMLElement): void {
  const range = document.createRange()
  const widths = Array.from(dom.querySelectorAll<HTMLElement>('[data-field="when"]')).map((el) => {
    range.selectNodeContents(el)
    return el.textContent ? range.getBoundingClientRect().width : 32
  })
  if (widths.length) dom.style.setProperty('--doc-tl-w', `${Math.ceil(Math.max(...widths)) + 2}px`)
}

/** `dot` draws the item's dot and line; in the editor it is also its menu. */
export function timelineItemShape(dot: HTMLElement): { dom: HTMLElement; content: HTMLElement } {
  const dom = document.createElement('div')
  dom.dataset.item = 'timeline'
  dot.classList.add('doc-tl__dot')
  const content = document.createElement('div')
  dom.append(dot, content)
  return { dom, content }
}

// ---- Stat cards

export function statsShape(): { dom: HTMLElement; cards: HTMLElement } {
  const dom = document.createElement('div')
  dom.dataset.block = 'stats'
  const cards = document.createElement('div')
  cards.className = 'doc-stats__cards'
  dom.append(cards)
  return { dom, cards }
}

export function statItemShape(): { dom: HTMLElement; content: HTMLElement } {
  const dom = document.createElement('div')
  dom.dataset.item = 'stat'
  const content = document.createElement('div')
  dom.append(content)
  return { dom, content }
}

// ---- Columns

export function columnShape(): { dom: HTMLElement; content: HTMLElement } {
  const dom = document.createElement('div')
  dom.dataset.item = 'column'
  const content = document.createElement('div')
  dom.append(content)
  return { dom, content }
}

// ---- Fields: the short lines of a timeline item or a stat card

const SIGN_UP = /^[+＋↑▲]/
const SIGN_DOWN = /^[-−－↓▼]/

/** Whether a stat's change went up or down, read from its sign. */
export function trendOf(delta: string): 'up' | 'down' | '' {
  const sign = delta.trim()
  return SIGN_UP.test(sign) ? 'up' : SIGN_DOWN.test(sign) ? 'down' : ''
}

// ---- Fold

/** `toggle` opens and closes it. Closed for a reader, open while editing. */
export function detailsShape(
  toggle: HTMLElement,
  open: boolean
): { dom: HTMLElement; content: HTMLElement; set: (open: boolean) => void } {
  const dom = document.createElement('div')
  dom.dataset.block = 'details'
  toggle.classList.add('doc-details__toggle')
  const content = document.createElement('div')
  dom.append(toggle, content)
  const set = (next: boolean) => {
    dom.classList.toggle('doc-details--open', next)
    toggle.setAttribute('aria-expanded', String(next))
  }
  set(open)
  toggle.addEventListener('click', () => set(!dom.classList.contains('doc-details--open')))
  return { dom, content, set }
}

// ---- Formulas

type Katex = typeof import('katex').default
let katexLoading: Promise<Katex> | null = null

/** KaTeX is loaded the first time a formula is on the page. */
function loadKatex(): Promise<Katex> {
  katexLoading ??= import('katex').then((m) => m.default)
  return katexLoading
}

export function renderMath(el: HTMLElement, latex: string, displayMode: boolean): void {
  if (!latex.trim()) {
    el.textContent = t('work.room.doc.blocks.emptyFormula')
    el.classList.add('doc-math--empty')
    return
  }
  el.classList.remove('doc-math--empty')
  // The source shows until KaTeX is there; a later call wins over an earlier one.
  el.textContent = latex
  el.dataset.latex = latex
  void loadKatex().then((katex) => {
    if (el.dataset.latex !== latex) return
    try {
      katex.render(latex, el, { displayMode, throwOnError: true })
      el.classList.remove('doc-math--error')
    } catch {
      el.textContent = latex
      el.classList.add('doc-math--error')
      el.title = t('work.room.doc.blocks.formulaError')
    }
  })
}

export function mathShape(latex: string, displayMode: boolean): HTMLElement {
  const dom = document.createElement(displayMode ? 'div' : 'span')
  dom.dataset.math = displayMode ? 'block' : 'inline'
  renderMath(dom, latex, displayMode)
  return dom
}

// ---- Footnotes

/** A footnote's text and where its definition sits in the document. */
export function footnoteText(doc: PMNode, label: string): { text: string; pos: number } | null {
  let found: { text: string; pos: number } | null = null
  doc.forEach((child, offset) => {
    if (!found && child.type.name === 'footnoteDef' && child.attrs.label === label) {
      found = { text: child.textContent, pos: offset }
    }
  })
  return found
}

/** The note shown where it is referenced; `extra` is what the editor adds. */
export function footnoteNote(note: { text: string } | null, ...extra: HTMLElement[]): HTMLElement {
  const el = document.createElement('div')
  el.classList.add('doc-pop--note')
  const text = document.createElement('p')
  text.textContent = note?.text || t('work.room.doc.blocks.footnoteMissing')
  el.append(text, ...extra)
  return el
}

// ---- Tables

// A number with an optional sign, currency and a short unit: 1,274 / $410 / 2.1% / 6.1 秒.
const NUMBER = /^[-+−]?[$¥€£]?\s?\d[\d.,]*\s?\S{0,3}$/

/** Whether the cells after the first column are mostly numbers. */
export function isNumberTable(table: PMNode): boolean {
  let numbers = 0
  let filled = 0
  table.forEach((row, _offset, rowIndex) => {
    if (rowIndex === 0) return
    row.forEach((cell, _o, col) => {
      const text = cell.textContent.trim()
      if (col === 0 || !text) return
      filled++
      if (NUMBER.test(text)) numbers++
    })
  })
  return filled > 0 && numbers / filled >= 0.6
}

/** How a table fits a phone while it is read: a table of words becomes one
 *  card per row, each value under its column's name (`labels`, one per
 *  column); a table of numbers keeps its columns and scrolls sideways. */
export function tableFit(table: PMNode): { shape: 'numbers' | 'cards'; labels: string[] } {
  if (isNumberTable(table)) return { shape: 'numbers', labels: [] }
  const labels: string[] = []
  table.firstChild?.forEach((cell) => labels.push(cell.textContent.trim()))
  return { shape: 'cards', labels }
}

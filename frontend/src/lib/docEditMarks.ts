// What the document shows around a change the AI teammate is making or made:
// the selection it was asked to rewrite (and the caret saying it is at work),
// the new text lit up for a moment, the suggestion being looked at, the
// changes someone asked it for (new text marked, the old text struck before
// it), and room under a block for the card that talks about it.
//
// None of it is part of the document: these are decorations, drawn from state
// the panel sets through `setEditMarks`.
//
// The range has to survive other people's typing. A change that arrives from
// the shared document is applied by replacing the whole document, so mapping a
// position through it lands at the start or the end. The range is therefore
// also kept as positions in the shared document (Yjs relative positions), and
// read back from those after such a change. When the text under them was
// rewritten wholesale (its anchors went with it), the range is looked up again
// by the text it held, nearest to where it was.

import type { Node as PMNode } from '@tiptap/pm/model'
import type { EditorState, Transaction } from '@tiptap/pm/state'
import type * as Y from 'yjs'
import type { DocEdit } from './docEdits'

import { Extension } from '@tiptap/core'
import { Plugin, PluginKey } from '@tiptap/pm/state'
import { Decoration, DecorationSet } from '@tiptap/pm/view'
import {
  absolutePositionToRelativePosition,
  relativePositionToAbsolutePosition,
  ySyncPluginKey,
} from '@tiptap/y-tiptap'

import { flatText, occurrences, rangeOf } from './docEdits'
import { locateEdits } from './docReview'
import { suggestionRanges } from './docSuggestionList'

/** How the target range is drawn: chosen for a rewrite, being rewritten,
 *  just rewritten (lit up), or only held so a card can stay beside it. */
export type TargetMode = 'select' | 'pending' | 'flash' | 'anchor'

export interface EditTarget {
  from: number
  to: number
  mode: TargetMode
  /** The caret's label while pending. */
  label: string
}

interface Anchored extends EditTarget {
  rel: { from: unknown; to: unknown } | null
  /** The text in the range, to find it again when its anchors are gone. */
  text: string
}

export interface EditMarksState {
  target: Anchored | null
  /** The suggestion being looked at. */
  suggestion: string | null
  /** The changes under review, and which one is looked at. */
  review: ReviewMarks | null
}

export interface ReviewMarks {
  edits: DocEdit[]
  active: number | null
}

export interface EditMarksPatch {
  target?: EditTarget | null
  suggestion?: string | null
  review?: ReviewMarks | null
}

const EMPTY: EditMarksState = { target: null, suggestion: null, review: null }

export const editMarksKey = new PluginKey<EditMarksState>('cheeseDocEditMarks')

/** Change what the marks show. */
export function setEditMarks(tr: Transaction, patch: EditMarksPatch): Transaction {
  return tr.setMeta(editMarksKey, patch)
}

export function editMarks(state: EditorState): EditMarksState {
  return editMarksKey.getState(state) ?? EMPTY
}

interface YState {
  doc: Y.Doc
  type: Y.XmlFragment
  binding: { mapping: Map<unknown, unknown> }
  isChangeOrigin?: boolean
}

function ySync(state: EditorState): YState | null {
  const y = ySyncPluginKey.getState(state) as YState | undefined
  return y?.binding ? y : null
}

function fromDocument(tr: Transaction): boolean {
  return !!(tr.getMeta(ySyncPluginKey) as { isChangeOrigin?: boolean } | undefined)?.isChangeOrigin
}

function clamp(doc: PMNode, pos: number): number {
  return Math.max(0, Math.min(doc.content.size, pos))
}

function moveTarget(target: Anchored, tr: Transaction, next: EditorState): Anchored | null {
  if (!tr.docChanged) return target
  const y = ySync(next)
  if (fromDocument(tr)) {
    const from = y && target.rel ? resolve(y, target.rel.from) : null
    const to = y && target.rel ? resolve(y, target.rel.to) : null
    if (from !== null && to !== null) {
      const moved = { ...target, from: clamp(next.doc, from), to: clamp(next.doc, Math.max(from, to)) }
      if (next.doc.textBetween(moved.from, moved.to, '\n') === target.text) return moved
    }
    const found = nearestText(next.doc, target.text, target.from)
    return found ? { ...target, ...found, rel: null } : null
  }
  const from = tr.mapping.map(target.from, 1)
  const to = tr.mapping.map(target.to, -1)
  return { ...target, from, to: Math.max(from, to), rel: null }
}

function resolve(y: YState, rel: unknown): number | null {
  return relativePositionToAbsolutePosition(y.doc, y.type, rel, y.binding.mapping as never)
}

/** Where `text` occurs in the document, nearest to `near`. */
export function nearestText(doc: PMNode, text: string, near: number): { from: number; to: number } | null {
  if (!text) return null
  const flat = flatText(doc)
  const hits = occurrences(flat.text, text)
  if (!hits.length) return null
  let at = flat.pos.findIndex((p) => p >= near)
  if (at < 0) at = flat.pos.length
  const best = hits.reduce((a, b) => (Math.abs(b - at) < Math.abs(a - at) ? b : a))
  return rangeOf(flat, best, best + text.length)
}

function struck(text: string, active: boolean): HTMLElement {
  const el = document.createElement('del')
  el.className = active ? 'doc-review-old is-active' : 'doc-review-old'
  el.textContent = text
  return el
}

function caret(label: string): HTMLElement {
  const el = document.createElement('span')
  el.className = 'doc-edit-caret'
  const tag = document.createElement('span')
  tag.className = 'doc-edit-caret__label'
  tag.textContent = label
  el.appendChild(tag)
  return el
}

// Drawn once per document and state: the editor asks on every update.
let drawn: { doc: PMNode; marks: EditMarksState; set: DecorationSet } | null = null

function decorations(state: EditorState): DecorationSet {
  const marks = editMarks(state)
  if (drawn && drawn.doc === state.doc && drawn.marks === marks) return drawn.set
  const set = draw(state.doc, marks)
  drawn = { doc: state.doc, marks, set }
  return set
}

function draw(doc: PMNode, marks: EditMarksState): DecorationSet {
  const { target, suggestion, review } = marks
  const out: Decoration[] = []
  if (target && target.to > target.from && target.mode !== 'anchor') {
    out.push(Decoration.inline(target.from, target.to, { class: `doc-edit-target doc-edit-target--${target.mode}` }))
  }
  if (target?.mode === 'pending') {
    out.push(Decoration.widget(target.to, () => caret(target.label), { side: 1, key: `caret:${target.label}` }))
  }
  if (suggestion) {
    for (const range of suggestionRanges(doc).filter((r) => r.id === suggestion)) {
      for (const span of range.spans) out.push(Decoration.inline(span.from, span.to, { class: 'doc-suggestion-focus' }))
    }
  }
  if (review) {
    for (const change of locateEdits(doc, review.edits)) {
      if (!change.live) continue
      const active = change.index === review.active ? ' is-active' : ''
      if (change.oldText) {
        const old = change.oldText
        out.push(
          Decoration.widget(change.from, () => struck(old, !!active), {
            side: -1,
            key: `old:${change.index}:${active}:${old}`,
          })
        )
      }
      if (change.to > change.from) {
        out.push(
          Decoration.inline(change.from, change.to, {
            class: `doc-review-new${active}`,
            'data-review': String(change.index),
          })
        )
      }
    }
  }
  return DecorationSet.create(doc, out)
}

/** The extension the editor carries; the panel drives it with `setEditMarks`. */
export function createEditMarks() {
  return Extension.create({
    name: 'cheeseDocEditMarks',
    addProseMirrorPlugins() {
      return [
        new Plugin<EditMarksState>({
          key: editMarksKey,
          state: {
            init: () => EMPTY,
            apply(tr, prev, _old, next) {
              let target = prev.target ? moveTarget(prev.target, tr, next) : null
              let suggestion = prev.suggestion
              let review = prev.review
              const patch = tr.getMeta(editMarksKey) as EditMarksPatch | undefined
              if (patch && 'target' in patch) {
                const set = patch.target
                target = set ? { ...set, rel: null, text: next.doc.textBetween(set.from, set.to, '\n') } : null
              }
              if (patch && 'suggestion' in patch) suggestion = patch.suggestion ?? null
              if (patch && 'review' in patch) review = patch.review ?? null
              const same = target === prev.target && suggestion === prev.suggestion
              if (same && review === prev.review) return prev
              return { target, suggestion, review }
            },
          },
          // After every update the shared document has caught up with this
          // editor (the sync plugin runs first), so a range that moved is
          // anchored in it again here.
          view: () => ({
            update(view) {
              const { target } = editMarks(view.state)
              const y = ySync(view.state)
              if (!target || target.rel || !y) return
              try {
                target.rel = {
                  from: absolutePositionToRelativePosition(target.from, y.type, y.binding.mapping as never),
                  to: absolutePositionToRelativePosition(target.to, y.type, y.binding.mapping as never),
                }
              } catch {
                target.rel = null
              }
            },
          }),
          props: { decorations },
        }),
      ]
    },
  })
}

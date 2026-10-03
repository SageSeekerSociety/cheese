// Status tags as a style on words: find the tag around the caret, put one on
// the selected words, change its kind, take it off.
//
// Lives in lib/ because it is about the document, not the panel: the selection
// bar, the keyboard shortcuts and the tests all call the same three functions.
import type { Editor } from '@tiptap/core'
import type { EditorState, Transaction } from '@tiptap/pm/state'
import type { StatusKind } from './docSchema/blocks'

import { BUBBLE_META } from './docBubble'

export interface StatusRun {
  from: number
  to: number
  kind: StatusKind
}

/** The run of words carrying one status tag at `pos`, if the caret is in one. */
export function statusAt(state: EditorState, pos: number): StatusRun | null {
  const type = state.schema.marks.status
  if (!type) return null
  const $pos = state.doc.resolve(pos)
  if (!$pos.parent.isTextblock) return null
  const mark = $pos.nodeAfter?.marks.find((m) => m.type === type) ?? $pos.nodeBefore?.marks.find((m) => m.type === type)
  if (!mark) return null
  const start = $pos.start()
  let found: StatusRun | null = null
  let runFrom: number | null = null
  let end = start
  $pos.parent.forEach((child, offset) => {
    const at = start + offset
    if (child.marks.some((m) => m.eq(mark))) {
      if (runFrom === null) runFrom = at
    } else if (runFrom !== null) {
      if (!found && runFrom <= pos && pos <= at) found = { from: runFrom, to: at, kind: mark.attrs.kind as StatusKind }
      runFrom = null
    }
    end = at + child.nodeSize
  })
  if (!found && runFrom !== null && runFrom <= pos && pos <= end) {
    found = { from: runFrom, to: end, kind: mark.attrs.kind as StatusKind }
  }
  return found
}

/** The status the selection is wholly inside, if any: what the bar shows pressed. */
export function currentStatus(state: EditorState): StatusKind | null {
  const { from, to, empty } = state.selection
  const run = statusAt(state, from)
  if (!run) return null
  return empty || (run.from <= from && run.to >= to) ? run.kind : null
}

/** Where a status change applies: the selected words, or the tag the caret is in. */
function target(state: EditorState): { from: number; to: number; kind: StatusKind | null } | null {
  const { from, to, empty, $from, $to } = state.selection
  if (empty) return statusAt(state, from)
  if (!$from.sameParent($to) || !$from.parent.isTextblock) return null
  return { from, to, kind: currentStatus(state) }
}

/** Whether a status can be set here: words are selected in one paragraph, or
 *  the caret is in a tag. */
export function canSetStatus(state: EditorState): boolean {
  return target(state) !== null
}

/** Give the selected words (or the tag under the caret) this kind of status;
 *  the kind it already has takes it off. `null` takes it off. */
export function statusTransaction(state: EditorState, kind: StatusKind | null): Transaction | null {
  const type = state.schema.marks.status
  const range = target(state)
  if (!type || !range) return null
  const tr = state.tr.removeMark(range.from, range.to, type)
  if (kind && range.kind !== kind) tr.addMark(range.from, range.to, type.create({ kind }))
  return tr.setMeta(BUBBLE_META, true)
}

export function setStatus(editor: Editor, kind: StatusKind | null): boolean {
  const tr = statusTransaction(editor.state, kind)
  if (!tr) return false
  editor.view.dispatch(tr)
  return true
}

/** A new tag with these words at the caret, typing on after it in plain text. */
export function insertStatus(editor: Editor, at: number, kind: StatusKind, words: string): void {
  const type = editor.schema.marks.status
  const text = words.trim()
  if (!type || !text) return
  editor
    .chain()
    .focus()
    .command(({ tr }) => {
      tr.insert(at, editor.schema.text(text, [type.create({ kind })]))
      tr.setStoredMarks([])
      return true
    })
    .setTextSelection(at + text.length)
    .run()
}

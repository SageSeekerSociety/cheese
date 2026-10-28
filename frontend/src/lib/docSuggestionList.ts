// The suggestions waiting in the document, where each one sits, and the commands
// that decide them — what the suggestion strip and card read and run.
//
// A suggestion is the insertion, deletion and modification marks sharing one
// id, on text or on whole blocks (see lib/docSchema/suggestions.ts). Accepting
// or rejecting one is an ordinary edit of the shared document, so every reader
// sees the decision.

import type { Node as PMNode } from '@tiptap/pm/model'
import type { EditorState, Transaction } from '@tiptap/pm/state'

import {
  applySuggestion,
  applySuggestions,
  revertSuggestion,
  revertSuggestions,
} from '@handlewithcare/prosemirror-suggest-changes'

import { suggestionAuthor } from './docSchema'

const MARKS = new Set(['insertion', 'deletion', 'modification'])

export interface SuggestionRange {
  id: string
  author: string
  /** From the first character it touches to the last. */
  from: number
  to: number
  /** Every run of text, and every whole block, it marks. */
  spans: { from: number; to: number }[]
}

/** The pending suggestions in document order. */
export function suggestionRanges(doc: PMNode): SuggestionRange[] {
  const byId = new Map<string, SuggestionRange>()
  doc.descendants((node, pos) => {
    const marked = node.marks.some((mark) => MARKS.has(mark.type.name))
    // A block's own text belongs to a suggestion on the block, unless that one
    // only retypes it: then its text may carry suggestions of its own.
    if (!node.isInline && !marked) return true
    const descend =
      !node.isInline && node.marks.every((mark) => !MARKS.has(mark.type.name) || mark.type.name === 'modification')
    for (const mark of node.marks) {
      if (!MARKS.has(mark.type.name)) continue
      const id = String(mark.attrs.id ?? '')
      if (!id) continue
      const entry = byId.get(id) ?? { id, author: suggestionAuthor(id), from: pos, to: pos, spans: [] }
      entry.to = Math.max(entry.to, pos + node.nodeSize)
      entry.from = Math.min(entry.from, pos)
      entry.spans.push({ from: pos, to: pos + node.nodeSize })
      byId.set(id, entry)
    }
    return descend
  })
  return [...byId.values()].sort((a, b) => a.from - b.from)
}

type Dispatch = (tr: Transaction) => void

/** Make one suggestion part of the text (`accept`) or drop it. */
export function decideSuggestion(state: EditorState, dispatch: Dispatch, id: string, accept: boolean): boolean {
  return (accept ? applySuggestion(id) : revertSuggestion(id))(state, dispatch)
}

/** Decide every pending suggestion the same way. */
export function decideAllSuggestions(state: EditorState, dispatch: Dispatch, accept: boolean): boolean {
  return (accept ? applySuggestions : revertSuggestions)(state, dispatch)
}

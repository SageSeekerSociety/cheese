// What a selection rewrite sends: the Markdown of the top-level block holding
// the selection, and where the selected text sits in that Markdown.
//
// The service finds the block in the document's Markdown and replaces the
// range, so both have to be exactly what the shared schema's serializer writes,
// with pending suggestions rejected (that is the document's text; see
// lib/docSchema/suggestions.ts).
//
// Where the selection lands in the Markdown is found by writing two markers into
// a copy of the block at the selection's ends and serializing that: the markers
// come out wherever the serializer put that character, past escapes, list
// markers and formatting. A selection that starts inside a bold run and ends
// outside it has no Markdown range of its own (`ld** cc` is not text); it is
// refused rather than sent, which `parsesTo` checks.

import type { Node as PMNode } from '@tiptap/pm/model'
import type { EditorState } from '@tiptap/pm/state'

import { plainOf } from './docEdits'
import { withoutSuggestions } from './docSchema'

export interface RewriteTarget {
  /** The block's Markdown. */
  block: string
  start: number
  end: number
  /** The selected text as it reads. */
  text: string
}

const OPEN = ''
const CLOSE = ''

const squash = (text: string) => text.replace(/\s+/g, ' ').trim()

/** The block and offsets for the selection `[from, to)`, or null when the
 *  selection cannot be sent: it spans blocks, is empty, overlaps a pending
 *  suggestion, or cuts through formatting. `serialize` writes a document as
 *  the editor's Markdown. */
export function rewriteTarget(
  state: EditorState,
  from: number,
  to: number,
  serialize: (doc: PMNode) => string
): RewriteTarget | null {
  const { doc, schema } = state
  if (from >= to) return null
  const $from = doc.resolve(from)
  const $to = doc.resolve(to)
  if ($from.depth < 1 || $to.depth < 1 || $from.before(1) !== $to.before(1)) return null
  const text = doc.textBetween(from, to, '\n')
  if (!squash(text)) return null

  const index = $from.index(0)
  const marked = state.tr.insertText(CLOSE, to).insertText(OPEN, from).doc.child(index)
  const wrap = (node: PMNode) => withoutSuggestions(schema.topNodeType.create(null, [node]))
  const withMarkers = serialize(wrap(marked)).trimEnd()
  const start = withMarkers.indexOf(OPEN)
  const close = withMarkers.indexOf(CLOSE)
  if (start < 0 || close < start || withMarkers.indexOf(OPEN, start + 1) >= 0) return null
  const block = withMarkers.replace(OPEN, '').replace(CLOSE, '')
  const end = close - 1
  // The markers must not have changed how the rest of the block is written.
  if (block !== serialize(wrap(doc.child(index))).trimEnd()) return null
  if (!parsesTo(block.slice(start, end), text)) return null
  return { block, start, end, text }
}

function parsesTo(markdown: string, text: string): boolean {
  return squash(plainOf(markdown)) === squash(text)
}

// What a selection rewrite sends: the Markdown of the top-level blocks holding
// the selection (one, or a run of them when it spans paragraphs), and where the
// selected text sits in that Markdown.
//
// The service finds the blocks in the document's Markdown and replaces the
// range, so both have to be exactly what the shared schema's serializer writes,
// with pending suggestions rejected (that is the document's text; see
// lib/docSchema/suggestions.ts).
//
// Where the selection lands in the Markdown is found by writing two markers into
// a copy of the blocks at the selection's ends and serializing that: the markers
// come out wherever the serializer put that character, past escapes, list
// markers and formatting. A selection that starts inside a bold run and ends
// outside it has no Markdown range of its own (`ld** cc` is not text); it is
// refused rather than sent, which `parsesTo` checks.

import type { Node as PMNode } from '@tiptap/pm/model'
import type { EditorState } from '@tiptap/pm/state'

import { plainOf } from './docEdits'
import { nodeMarkdown, withoutSuggestions } from './docSchema'

export interface RewriteTarget {
  /** The Markdown of the blocks holding the selection. */
  block: string
  start: number
  end: number
  /** The selected text as it reads. */
  text: string
}

const OPEN = ''
const CLOSE = ''

const squash = (text: string) => text.replace(/\s+/g, ' ').trim()

/** The blocks and offsets for the selection `[from, to)`, or null when the
 *  selection cannot be sent: it is empty, overlaps a pending suggestion, or
 *  cuts through formatting. */
export function rewriteTarget(state: EditorState, from: number, to: number): RewriteTarget | null {
  const { doc, schema } = state
  if (from >= to) return null
  const $from = doc.resolve(from)
  const $to = doc.resolve(to)
  if ($from.depth < 1 || $to.depth < 1) return null
  const text = doc.textBetween(from, to, '\n')
  if (!squash(text)) return null

  const first = $from.index(0)
  const last = $to.index(0)
  const blocks = (root: PMNode) => {
    const out: PMNode[] = []
    for (let i = first; i <= last; i++) out.push(root.child(i))
    return out
  }
  const marked = state.tr.insertText(CLOSE, to).insertText(OPEN, from).doc
  const wrap = (nodes: PMNode[]) => withoutSuggestions(schema.topNodeType.create(null, nodes))
  const withMarkers = nodeMarkdown(wrap(blocks(marked))).trimEnd()
  const start = withMarkers.indexOf(OPEN)
  const close = withMarkers.indexOf(CLOSE)
  if (start < 0 || close < start || withMarkers.indexOf(OPEN, start + 1) >= 0) return null
  const block = withMarkers.replace(OPEN, '').replace(CLOSE, '')
  const end = close - 1
  // The markers must not have changed how the rest of the block is written.
  if (block !== nodeMarkdown(wrap(blocks(doc))).trimEnd()) return null
  if (!parsesTo(block.slice(start, end), text)) return null
  return { block, start, end, text }
}

function parsesTo(markdown: string, text: string): boolean {
  return squash(plainOf(markdown)) === squash(text)
}

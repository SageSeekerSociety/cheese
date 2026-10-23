// What a person who may only suggest is let write into a live document (#3830):
// suggestions of their own, and nothing else.
//
// A mentor comments and suggests but does not change a document. Their editor
// is in 建议 mode, which turns every edit into insertion and deletion marks;
// the service does not trust that. Each update such a connection sends is
// applied to a copy first and let through only when the document still reads
// the same with every pending suggestion rejected — its text is untouched —,
// every mark it leaves is one the schema allows there, and every suggestion it
// adds or takes away is the sender's own. Anything
// else is acknowledged as refused and not applied. A client that sent it is
// left ahead of the document; only a tampered one gets there, and it is not
// brought back.

import * as Y from 'yjs'

import { exportMarkdown, liveNode, liveSuggestions, suggestionAuthor } from '../src/lib/docSchema'

export type SuggestProblem = 'text' | 'author' | 'shape'

function ids(doc: Y.Doc): Set<string> {
  return new Set(liveSuggestions(doc).map((item) => item.id))
}

function valid(doc: Y.Doc): boolean {
  try {
    liveNode(doc).check()
    return true
  } catch {
    return false
  }
}

/** Why `update` is not only `handle`'s suggestions; null when it is. */
export function suggestOnlyProblem(doc: Y.Doc, update: Uint8Array, handle: string): SuggestProblem | null {
  const copy = new Y.Doc()
  Y.applyUpdate(copy, Y.encodeStateAsUpdate(doc))
  Y.applyUpdate(copy, update)
  // A mark the schema does not allow where it was put (the shared document
  // keeps a block's marks, whatever they are) leaves a document no edit can be
  // checked against.
  if (valid(doc) && !valid(copy)) return 'shape'
  if (exportMarkdown(copy) !== exportMarkdown(doc)) return 'text'
  const before = ids(doc)
  const after = ids(copy)
  const touched = [...after].filter((id) => !before.has(id)).concat([...before].filter((id) => !after.has(id)))
  return touched.some((id) => suggestionAuthor(id) !== handle) ? 'author' : null
}

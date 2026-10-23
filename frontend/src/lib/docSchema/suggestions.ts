// Suggested changes: text someone proposed to insert or delete, kept in the
// document as marks until a person accepts or rejects it.
//
// The marks are the ones @handlewithcare/prosemirror-suggest-changes works
// with, so its commands accept, reject and create them. They live in the
// shared document, so every reader sees the same suggestions, and accepting
// one is an edit like any other.
//
// A suggestion marks text, or a whole block: a paragraph added or removed, a
// list item added, a paragraph turned into a heading (see suggestableBlocks).
// The shared document keeps a block's marks too (patches/@tiptap__y-tiptap*).
//
// A suggestion is not yet part of the text. The document's Markdown (what is
// stored, searched and given to the agent) is the text with every pending
// suggestion rejected; `pendingSuggestions` (./yjs.ts) says separately what is
// proposed.

import type { AnyExtension } from '@tiptap/core'
import type { Node as PMNode } from '@tiptap/pm/model'

import { revertSuggestions } from '@handlewithcare/prosemirror-suggest-changes'
import { Mark, Node } from '@tiptap/core'
import { EditorState } from '@tiptap/pm/state'

export const SUGGESTION_MARKS = ['insertion', 'deletion', 'modification'] as const

const exclusive = 'insertion deletion modification'
const idAttr = { id: { default: null } }

const Insertion = Mark.create({
  name: 'insertion',
  inclusive: false,
  excludes: exclusive,
  addAttributes: () => idAttr,
  parseHTML: () => [{ tag: 'ins[data-suggestion]' }],
  renderHTML: ({ mark }) => ['ins', { 'data-suggestion': mark.attrs.id, class: 'doc-suggestion' }, 0],
})

const Deletion = Mark.create({
  name: 'deletion',
  inclusive: false,
  excludes: exclusive,
  addAttributes: () => idAttr,
  parseHTML: () => [{ tag: 'del[data-suggestion]' }],
  renderHTML: ({ mark }) => ['del', { 'data-suggestion': mark.attrs.id, class: 'doc-suggestion' }, 0],
})

const Modification = Mark.create({
  name: 'modification',
  inclusive: false,
  // Not itself: a block can carry several, a paragraph made a heading and then
  // given another level, and rejecting it has to undo each (as the library's
  // own spec has it).
  excludes: 'insertion deletion',
  addAttributes: () => ({
    ...idAttr,
    type: { default: null },
    attrName: { default: null },
    previousValue: { default: null },
    newValue: { default: null },
  }),
  parseHTML: () => [{ tag: 'span[data-suggestion-modification]' }],
  renderHTML: ({ mark }) => ['span', { 'data-suggestion-modification': mark.attrs.id, class: 'doc-suggestion' }, 0],
})

export const suggestionMarks = [Insertion, Deletion, Modification]

// The nodes whose content is a run of blocks or of list items. A whole block
// in one of them — a paragraph added at the end, a list item removed, a
// paragraph turned into a heading — is suggested by marking the block itself,
// which a node allows only on the children its `marks` names. Containers of a
// fixed shape (a table's rows, a timeline's fields, the columns of a row) are
// not here: what they hold is changed directly.
const BLOCK_RUNS = new Set([
  'doc',
  'blockquote',
  'bulletList',
  'orderedList',
  'taskList',
  'listItem',
  'taskItem',
  'tableCell',
  'tableHeader',
  'callout',
  'column',
  'detailsContent',
])

/** `extension`, letting the blocks it holds carry a suggestion when it is one
 *  of the containers above. */
export function suggestableBlocks(extension: AnyExtension): AnyExtension {
  if (!(extension instanceof Node) || !BLOCK_RUNS.has(extension.name)) return extension
  return extension.extend({ marks: SUGGESTION_MARKS.join(' ') })
}

/** Who proposed a suggestion: its id is `<handle>:<random>`, because ids are
 *  made by different people's editors and the service, and numbers they each
 *  count up on their own would collide. */
export function suggestionAuthor(id: string): string {
  const cut = id.indexOf(':')
  return cut > 0 ? id.slice(0, cut) : ''
}

export function suggestionId(author: string): string {
  return `${author}:${Math.random().toString(36).slice(2, 10)}`
}

function hasSuggestions(node: PMNode): boolean {
  let found = false
  node.descendants((child) => {
    if (found) return false
    if (child.marks.some((m) => (SUGGESTION_MARKS as readonly string[]).includes(m.type.name))) found = true
    return !found
  })
  return found
}

/** The document as it reads with every pending suggestion rejected. */
export function withoutSuggestions(node: PMNode): PMNode {
  if (!hasSuggestions(node)) return node
  let reverted = node
  revertSuggestions(EditorState.create({ doc: node }), (tr) => {
    reverted = tr.doc
  })
  return reverted
}

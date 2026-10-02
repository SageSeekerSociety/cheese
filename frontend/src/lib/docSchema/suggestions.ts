// Suggested changes: text someone proposed to insert or delete, kept in the
// document as marks until a person accepts or rejects it.
//
// The marks are the ones @handlewithcare/prosemirror-suggest-changes works
// with, so its commands accept, reject and create them. They live in the
// shared document, so every reader sees the same suggestions, and accepting
// one is an edit like any other.
//
// A suggestion is not yet part of the text. The document's Markdown (what is
// stored, searched and given to the agent) is the text with every pending
// suggestion rejected; `pendingSuggestions` says separately what is proposed.

import type { Node as PMNode } from '@tiptap/pm/model'

import { revertSuggestions } from '@handlewithcare/prosemirror-suggest-changes'
import { Mark } from '@tiptap/core'
import { EditorState } from '@tiptap/pm/state'

const SUGGESTION_MARKS = ['insertion', 'deletion', 'modification'] as const

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
  excludes: exclusive,
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

export interface PendingSuggestion {
  id: string
  author: string
  /** The text it would remove. */
  old: string
  /** The text it would add. */
  new: string
}

/** Every suggestion still waiting for a decision, in document order. */
export function pendingSuggestions(node: PMNode): PendingSuggestion[] {
  const byId = new Map<string, PendingSuggestion>()
  node.descendants((child) => {
    if (!child.isText) return
    for (const mark of child.marks) {
      const name = mark.type.name
      if (name !== 'insertion' && name !== 'deletion') continue
      const id = String(mark.attrs.id ?? '')
      if (!id) continue
      const entry = byId.get(id) ?? { id, author: suggestionAuthor(id), old: '', new: '' }
      if (name === 'insertion') entry.new += child.text ?? ''
      else entry.old += child.text ?? ''
      byId.set(id, entry)
    }
  })
  return [...byId.values()]
}

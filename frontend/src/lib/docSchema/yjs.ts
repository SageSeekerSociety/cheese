// The living document as a Yjs document — what the editors bind to — read and
// written as Markdown with no editor and no DOM. The collaboration service
// converts, exports and rewrites documents with it; tests and the preview site
// build documents with it.
//
// The schema is the editors' own, so what this writes is exactly what an editor
// would, and what it exports is exactly what an editor's Markdown would be.

import type { Node as PMNode } from '@tiptap/pm/model'
import type { marked } from 'marked'

import { getSchema } from '@tiptap/core'
import { MarkdownManager } from '@tiptap/markdown'
import { Fragment } from '@tiptap/pm/model'
import { prosemirrorJSONToYXmlFragment, updateYFragment, yXmlFragmentToProseMirrorRootNode } from '@tiptap/y-tiptap'
import * as Y from 'yjs'

import { carryCommentAnchors } from './commentAnchors'
import { docExtensions } from './extensions'
import { docMarked, finishMarkdown } from './markdown'
import { SUGGESTION_MARKS, suggestionAuthor, withoutSuggestions } from './suggestions'

/** The Y.XmlFragment the editors' Collaboration extension binds to. */
export const FIELD = 'default'

const extensions = docExtensions()
const schema = getSchema(extensions)
// The cast: same reason as in docExtensions — the option is typed as the marked
// module, and an instance carries every member the manager reads.
const markdown = new MarkdownManager({ marked: docMarked as unknown as typeof marked, extensions })

export function parseMarkdown(md: string): PMNode {
  return schema.nodeFromJSON(markdown.parse(md))
}

export function liveNode(doc: Y.Doc): PMNode {
  return yXmlFragmentToProseMirrorRootNode(doc.getXmlFragment(FIELD), schema)
}

/** The document's text: what is stored, searched and given to the agent. A
 *  pending suggestion is not part of it yet (see ./suggestions.ts). */
export function exportMarkdown(doc: Y.Doc): string {
  return nodeMarkdown(withoutSuggestions(liveNode(doc)))
}

/** A document node's Markdown, exactly as it stands (suggestion marks and all
 *  are written as their text). */
export function nodeMarkdown(node: PMNode): string {
  return finishMarkdown(markdown.serialize(node.toJSON()))
}

export interface PendingSuggestion {
  id: string
  author: string
  /** What it would remove: the text, or a whole block's Markdown. */
  old: string
  /** What it would add, the same way. */
  new: string
}

const SUGGESTION = new Set<string>(SUGGESTION_MARKS)

/** A block's Markdown on its own: inside a copy of its parent when only a
 *  parent can hold it (a list item in its list). */
function blockMarkdown(block: PMNode, parent: PMNode | null): string {
  const doc = schema.topNodeType
  const content = doc.contentMatch.matchType(block.type) || !parent ? block : parent.copy(Fragment.from(block))
  return nodeMarkdown(doc.create(null, content)).replace(/\u200B/g, '')
}

/** The block as it was before the modifications `id` made to it. */
function unmodified(block: PMNode, id: string): PMNode {
  let type = block.type
  const attrs = { ...block.attrs }
  for (const mark of block.marks) {
    if (mark.type.name !== 'modification' || mark.attrs.id !== id) continue
    if (mark.attrs.type === 'nodeType') type = schema.nodes[mark.attrs.previousValue] ?? type
    if (mark.attrs.type === 'attr') attrs[mark.attrs.attrName] = mark.attrs.previousValue
  }
  return type.create(type === block.type ? attrs : null, block.content)
}

function append(passage: string, block: string): string {
  return passage && block ? `${passage}\n\n${block}` : passage + block
}

/** Every suggestion still waiting for a decision, in document order. A
 *  suggestion on text says the text; one on a whole block says the block as
 *  Markdown, so a paragraph turned into a heading reads `x` → `## x`. */
export function pendingSuggestions(node: PMNode): PendingSuggestion[] {
  const byId = new Map<string, PendingSuggestion>()
  const entry = (id: string) => {
    const found = byId.get(id) ?? { id, author: suggestionAuthor(id), old: '', new: '' }
    byId.set(id, found)
    return found
  }
  node.descendants((child, _pos, parent) => {
    const marks = child.marks.filter((mark) => SUGGESTION.has(mark.type.name) && mark.attrs.id)
    if (!marks.length) return true
    if (child.isText) {
      // A zero-width space is the anchor a suggestion leaves where it splits
      // or joins blocks, not text anyone wrote.
      const text = (child.text ?? '').replace(/\u200B/g, '')
      for (const mark of marks) {
        if (mark.type.name === 'insertion') entry(String(mark.attrs.id)).new += text
        if (mark.type.name === 'deletion') entry(String(mark.attrs.id)).old += text
      }
      return false
    }
    const bare = child.mark(child.marks.filter((mark) => !SUGGESTION.has(mark.type.name)))
    if (child.isInline) {
      // An inline node that is not text (a formula, a footnote reference) is
      // suggested whole, and reads as its Markdown.
      const inline = (node: PMNode) => blockMarkdown(schema.nodes.paragraph.create(null, node), null)
      for (const mark of marks) {
        const item = entry(String(mark.attrs.id))
        if (mark.type.name === 'insertion') item.new += inline(bare)
        if (mark.type.name === 'deletion') item.old += inline(bare)
        if (mark.type.name === 'modification') {
          item.old += inline(unmodified(child, String(mark.attrs.id)).mark(bare.marks))
          item.new += inline(bare)
        }
      }
      return false
    }
    let whole = false
    for (const id of new Set(marks.map((mark) => String(mark.attrs.id)))) {
      const item = entry(id)
      const kinds = new Set(marks.filter((mark) => mark.attrs.id === id).map((mark) => mark.type.name))
      // A block added or removed whole: what it holds is part of it.
      if (kinds.has('insertion')) item.new = append(item.new, blockMarkdown(bare, parent))
      if (kinds.has('deletion')) item.old = append(item.old, blockMarkdown(bare, parent))
      whole ||= kinds.has('insertion') || kinds.has('deletion')
      if (kinds.has('modification')) {
        item.old = append(item.old, blockMarkdown(unmodified(child, id).mark(bare.marks), parent))
        item.new = append(item.new, blockMarkdown(bare, parent))
      }
    }
    // A block whose type changed keeps its text, which may carry suggestions
    // of its own.
    return !whole
  })
  return [...byId.values()]
}

/** The suggestions in the live document still waiting for a decision. */
export function liveSuggestions(doc: Y.Doc) {
  return pendingSuggestions(liveNode(doc))
}

/** Rewrite the document to read `md`, as a diff against what it holds, so the
 *  parts that did not change keep their identity — and with it every caret
 *  and every concurrent edit inside them. */
export function writeMarkdown(doc: Y.Doc, md: string): void {
  const fragment = doc.getXmlFragment(FIELD)
  const next = parseMarkdown(md)
  if (fragment.length === 0) {
    doc.transact(() => prosemirrorJSONToYXmlFragment(schema, next.toJSON(), fragment))
    return
  }
  // Markdown has no comment marks: give back the ones the rewrite would drop.
  const marked = carryCommentAnchors(liveNode(doc), next)
  doc.transact(() => updateYFragment(doc, fragment, marked, { mapping: new Map(), isOMark: new Map() }))
}

/** Whether the live document still reads `base`: the same text, or — for a
 *  document converted from Markdown nobody has edited since — the same
 *  structure that text parses to. `null` is "there was no document". */
export function readsAs(doc: Y.Doc, base: string | null): boolean {
  const live = exportMarkdown(doc)
  if (base === null) return live.trim() === ''
  if (live === base) return true
  return parseMarkdown(base).eq(withoutSuggestions(liveNode(doc)))
}

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
import { prosemirrorJSONToYXmlFragment, updateYFragment, yXmlFragmentToProseMirrorRootNode } from '@tiptap/y-tiptap'
import * as Y from 'yjs'

import { carryCommentAnchors } from './commentAnchors'
import { docExtensions } from './extensions'
import { docMarked, finishMarkdown } from './markdown'
import { pendingSuggestions, withoutSuggestions } from './suggestions'

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

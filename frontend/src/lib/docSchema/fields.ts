// Fields: the short lines a timeline item or a stat card is made of, and the
// other one-line parts of a block (`detailsSummary`, `footnoteDef`). Only
// inline things go in a field — words, a status tag, a link — never a block.

import type { JSONContent, MarkdownToken } from '@tiptap/core'
import type { Node as PMNode, Schema } from '@tiptap/pm/model'

import { mergeAttributes, Node } from '@tiptap/core'

/** One field node of a block: its own name, and the `data-field` role it is
 *  addressed by in HTML. */
export function field(name: string, role: string) {
  return Node.create({
    name,
    content: 'inline*',
    defining: true,
    parseHTML: () => [{ tag: `div[data-field="${role}"]` }],
    renderHTML: ({ HTMLAttributes }) => ['div', mergeAttributes(HTMLAttributes, { 'data-field': role }), 0],
  })
}

/** The inline content of a field, from the tokens a tokenizer read for it. */
export function inline(
  helpers: { parseInline: (t: MarkdownToken[]) => JSONContent[] },
  tokens: MarkdownToken[] | undefined
) {
  return tokens?.length ? helpers.parseInline(tokens) : []
}

/** A field node, empty when its tokens held nothing. */
export function fieldNode(type: string, content: JSONContent[]): JSONContent {
  return content.length ? { type, content } : { type }
}

/** The short one-line fields: only inline things (words, a status tag) go in them. */
export const FIELD_NODES = new Set([
  'timelineWhen',
  'timelineTitle',
  'timelineBody',
  'statName',
  'statValue',
  'statDelta',
  'detailsSummary',
  'footnoteDef',
  // A mini-app's one sentence (./app.ts).
  'app',
])

/** An empty item of a timeline or a set of stat cards. */
export function emptyItem(schema: Schema, type: 'timelineItem' | 'statItem'): PMNode {
  const fields =
    type === 'timelineItem' ? ['timelineWhen', 'timelineTitle', 'timelineBody'] : ['statName', 'statValue', 'statDelta']
  return schema.nodes[type].create(
    null,
    fields.map((name) => schema.nodes[name].create())
  )
}

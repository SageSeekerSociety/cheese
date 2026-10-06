// Markdown read the way a document reads it, without an editor: into the
// document's blocks (lib/docSchema), or into one line of plain words.
//
// Chat reads with one difference: a single newline is a line break, because
// that is the newline its author typed.
//
// Drawing the blocks for a reader is components/panels/doc/blocks/reader.ts.
import type { Node as PMNode } from '@tiptap/pm/model'

import { getSchema } from '@tiptap/core'
import { MarkdownManager } from '@tiptap/markdown'

import { t } from '../i18n'

import { STATUS_KINDS } from './docSchema/blocks'
import { buildDocMarked, docExtensions } from './docSchema'

export type ReadAs = 'doc' | 'chat'

const extensions = docExtensions()
export const schema = getSchema(extensions)
const managers: Partial<Record<ReadAs, MarkdownManager>> = {}

function manager(as: ReadAs): MarkdownManager {
  let m = managers[as]
  if (!m) {
    const marked = buildDocMarked()
    if (as === 'chat') marked.setOptions({ breaks: true })
    // The cast: same reason as in docExtensions.
    m = new MarkdownManager({ marked: marked as never, extensions })
    managers[as] = m
  }
  return m
}

/** Markdown read into the document's blocks. */
export function readMarkdown(md: string, as: ReadAs = 'doc'): PMNode {
  return schema.nodeFromJSON(manager(as).parse(md))
}

// ---- Plain text: one line of it, for a quote or a preview

function blockText(node: PMNode): string {
  if (node.isText) {
    const status = node.marks.find((mark) => mark.type.name === 'status')
    const sign = status ? STATUS_KINDS[status.attrs.kind as keyof typeof STATUS_KINDS] : ''
    return sign ? `${sign} ${node.text}` : node.text ?? ''
  }
  switch (node.type.name) {
    case 'chart':
      return `[${t('work.room.doc.blocks.plain.chart')}]`
    case 'codeBlock':
      return node.attrs.language === 'mermaid' ? `[${t('work.room.doc.blocks.plain.diagram')}]` : node.textContent
    case 'mathInline':
    case 'mathBlock':
      return node.attrs.latex as string
    case 'footnoteRef':
    case 'footnoteDef':
    case 'docComment':
      return ''
    case 'image':
      return `[${t('work.room.doc.blocks.plain.image')}]`
    case 'video':
      return `[${t('work.room.doc.blocks.plain.video')}]`
  }
  const parts: string[] = []
  node.forEach((child) => parts.push(blockText(child)))
  return parts.join(node.isTextblock ? '' : ' ')
}

/** What the text says, as one line of words: no Markdown marks, a status
 *  tag as its sign and words, a chart or a diagram as what it is. */
export function plainText(md: string, as: ReadAs = 'doc'): string {
  return blockText(readMarkdown(md, as)).replace(/\s+/g, ' ').trim()
}

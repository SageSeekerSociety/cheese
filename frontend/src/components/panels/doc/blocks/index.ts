// The editor's half of the document blocks: views on the schema's nodes, the
// keys and hints for typing in them, and a reader's table on a phone.
import 'katex/dist/katex.min.css'
import '@/styles/docBlocks.css'

import type { AnyExtension } from '@tiptap/core'

import { BlockEditing } from './blockEditing'
import { withBlockViews } from './blockViews'
import { TableShape } from './tableShape'

export { newStatusAt } from './newStatus'

/** The document's extensions as the document panel's editor runs them. */
export function editorBlocks(schema: AnyExtension[]): AnyExtension[] {
  return [...withBlockViews(schema), BlockEditing, TableShape]
}

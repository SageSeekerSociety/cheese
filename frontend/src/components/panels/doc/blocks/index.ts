// The editor's half of the document blocks: views on the schema's nodes, the
// keys and hints for typing in them, a table's row and column handles, and a
// reader's table on a phone.
import 'katex/dist/katex.min.css'
import '@/styles/docBlocks.css'

import type { AnyExtension } from '@tiptap/core'
import type { AgentHook } from './mermaidView'

import { BlockEditing } from './blockEditing'
import { withBlockViews } from './blockViews'
import { TableHandles } from './tableHandles'
import { TableShape } from './tableShape'

export type { AgentHook } from './mermaidView'
export { newStatusAt } from './newStatus'

/** The document's extensions as the document panel's editor runs them. `agent`
 *  says whether a diagram can be handed to the AI teammate, and how. */
export function editorBlocks(schema: AnyExtension[], agent: AgentHook = () => null): AnyExtension[] {
  return [...withBlockViews(schema, agent), BlockEditing, TableShape, TableHandles]
}

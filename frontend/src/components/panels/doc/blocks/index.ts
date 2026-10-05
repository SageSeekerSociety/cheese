// The editor's half of the document blocks: views on the schema's nodes, the
// keys and hints for typing in them, a table's row and column handles, and a
// reader's table on a phone.
import '@/styles/docBlocks.css'

import type { AnyExtension } from '@tiptap/core'
import type { AgentHook } from './mermaidView'

import { Extension } from '@tiptap/core'
import { Plugin } from '@tiptap/pm/state'

import { BlockEditing, BlockSelect } from './blockEditing'
import { withBlockViews } from './blockViews'
import { READING } from './shapes'
import { TableHandles } from './tableHandles'
import { TableShape } from './tableShape'

export type { AgentHook } from './mermaidView'
export { newStatusAt } from './newStatus'

/** The editor carries the reading class while it cannot be edited. */
const Reading = Extension.create({
  name: 'docReading',
  addProseMirrorPlugins() {
    const editor = this.editor
    return [
      new Plugin({
        props: { attributes: (): Record<string, string> => (editor.isEditable ? {} : { class: READING }) },
      }),
    ]
  },
})

/** The document's extensions as the document panel's editor runs them. `agent`
 *  says whether a diagram can be handed to the AI teammate, and how. */
export function editorBlocks(schema: AnyExtension[], agent: AgentHook = () => null): AnyExtension[] {
  return [...withBlockViews(schema, agent), BlockEditing, BlockSelect, TableShape, TableHandles, Reading]
}

// The document panel's editor: tiptap's Vue editor, kept in step with its view
// when a plugin is added or removed.
//
// tiptap's Vue editor keeps its own copy of the state. Registering a plugin
// (the drag handle does, when it mounts) makes the collaboration plugin
// re-render, and that leaves the copy behind the view: the next transaction is
// built on the old copy, and collaboration throws its change away. So the
// first edit after opening a document was lost whenever nothing had moved the
// caret before it (a block's own button, the code language picker). One empty
// transaction after the plugin change brings the two back together.
import type { Plugin, PluginKey } from '@tiptap/pm/state'

import { Editor } from '@tiptap/vue-3'

export class DocEditor extends Editor {
  registerPlugin(plugin: Plugin, handlePlugins?: (newPlugin: Plugin, plugins: Plugin[]) => Plugin[]) {
    super.registerPlugin(plugin, handlePlugins)
    return this.resync()
  }

  unregisterPlugin(nameOrPluginKey: string | PluginKey) {
    const changed = super.unregisterPlugin(nameOrPluginKey)
    return changed && this.resync()
  }

  private resync() {
    if (!this.isDestroyed) this.view.dispatch(this.state.tr)
    return this.state
  }
}

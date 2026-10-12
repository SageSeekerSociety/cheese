// The canvas block in the editor: its view (CanvasBlockView.vue), and the rule
// that a canvas block pasted or dropped in from elsewhere gets a canvas of its
// own. A canvas belongs to one document and appears in it once; copying the
// block copies the board, the way copying a whiteboard in a document does.
import type { AnyExtension } from '@tiptap/core'
import type { Node as PMNode } from '@tiptap/pm/model'
import type { CanvasHost } from '@/types/canvas'

import { Fragment, Slice } from '@tiptap/pm/model'
import { Plugin, PluginKey } from '@tiptap/pm/state'
import { VueNodeViewRenderer } from '@tiptap/vue-3'

import { promiseCanvas } from '../../../../lib/docSchema/canvas'

import CanvasBlockView from './CanvasBlockView.vue'

/** `fragment` with every canvas block in it marked to get a canvas of its own. */
function marked(fragment: Fragment): Fragment {
  const nodes: PMNode[] = []
  fragment.forEach((node) => {
    if (node.type.name === 'canvas') {
      const work = node.attrs.canvas ? 'copy' : 'make'
      nodes.push(node.type.create({ ...node.attrs, pending: promiseCanvas(work) }))
    } else if (holdsCanvas(node)) {
      nodes.push(node.copy(marked(node.content)))
    } else {
      nodes.push(node)
    }
  })
  return Fragment.from(nodes)
}

function holdsCanvas(node: PMNode): boolean {
  let found = false
  node.descendants((child) => {
    found ||= child.type.name === 'canvas'
    return !found
  })
  return found
}

const pasted = new PluginKey('docCanvasPaste')

/** The document's extensions with the canvas block drawn as a board, read and written through `host`. */
export function withCanvas(extensions: AnyExtension[], host: CanvasHost | null): AnyExtension[] {
  return extensions.map((extension) => {
    if (extension.name !== 'canvas' || extension.type !== 'node') return extension
    return (extension as unknown as { extend: (config: object) => AnyExtension }).extend({
      addOptions: () => ({ host }),
      addNodeView: () =>
        VueNodeViewRenderer(CanvasBlockView, {
          // The board takes its own pointer and keys: ProseMirror would select the block on every press.
          stopEvent: ({ event }) =>
            !!(event.target as Element | null)?.closest?.('.doc-canvas__stage, .doc-canvas__head'),
          ignoreMutation: () => true,
        }),
      addProseMirrorPlugins: () => [
        new Plugin({
          key: pasted,
          props: {
            // A block moved by dragging is the same block; one pasted or dropped from a copy is a new one.
            transformPasted: (slice, view) =>
              view.dragging?.move ? slice : new Slice(marked(slice.content), slice.openStart, slice.openEnd),
          },
        }),
      ],
    })
  })
}

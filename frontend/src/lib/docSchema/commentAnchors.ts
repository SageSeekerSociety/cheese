// Comment anchors: the words a comment thread is about, marked in the shared
// document with the thread's id.
//
// The mark lives in the document, so it moves with the words: text typed
// before it, a paragraph inserted above it, a teammate's edit elsewhere leave
// it on the same words for every reader. Rewriting the marked words themselves
// as one replacement drops it with them, and then the thread has nothing left
// to point at.
//
// It is not part of the text. The document's Markdown (what is stored,
// searched and given to the agent) has no trace of it, and copying marked text
// does not copy the mark: a pasted passage would otherwise point a second place
// at the same thread. Dragging a block to move it keeps the mark, since the
// words only moved.
import type { Node as PMNode, MarkType } from '@tiptap/pm/model'

import { Mark, mergeAttributes } from '@tiptap/core'
import { Fragment, Slice } from '@tiptap/pm/model'
import { Plugin, PluginKey } from '@tiptap/pm/state'

export const COMMENT_ANCHOR = 'commentAnchor'

function strip(fragment: Fragment, type: MarkType): Fragment {
  const out: PMNode[] = []
  fragment.forEach((node) => {
    out.push(node.isText ? node.mark(type.removeFromSet(node.marks)) : node.copy(strip(node.content, type)))
  })
  return Fragment.from(out)
}

export const CommentAnchor = Mark.create({
  name: COMMENT_ANCHOR,
  // Typing at either edge does not extend the comment to the new words.
  inclusive: false,
  // Two threads on overlapping words are two marks of this one type.
  excludes: '',
  addAttributes() {
    return {
      thread: {
        default: null,
        parseHTML: (element) => element.getAttribute('data-comment-thread'),
        renderHTML: (attributes) => ({ 'data-comment-thread': attributes.thread as string | null }),
      },
    }
  },
  parseHTML() {
    return [{ tag: 'span[data-comment-thread]' }]
  },
  renderHTML({ HTMLAttributes }) {
    return ['span', mergeAttributes(HTMLAttributes, { class: 'doc-comment-anchor' }), 0]
  },
  renderMarkdown: (node, helpers) => helpers.renderChildren(node),
  addProseMirrorPlugins() {
    const type = this.type
    return [
      new Plugin({
        key: new PluginKey('cheeseCommentAnchorPaste'),
        props: {
          transformPasted(slice, view) {
            if (view.dragging?.move) return slice
            return new Slice(strip(slice.content, type), slice.openStart, slice.openEnd)
          },
        },
      }),
    ]
  },
})

/** Where each thread's words are in the document: thread id → the ranges it marks, in order. */
export function commentAnchors(doc: PMNode): Map<string, { from: number; to: number }[]> {
  const found = new Map<string, { from: number; to: number }[]>()
  doc.descendants((node, pos) => {
    if (!node.isText) return
    for (const mark of node.marks) {
      if (mark.type.name !== COMMENT_ANCHOR || !mark.attrs.thread) continue
      const id = mark.attrs.thread as string
      const ranges = found.get(id) ?? []
      const last = ranges[ranges.length - 1]
      if (last && last.to === pos) last.to = pos + node.nodeSize
      else ranges.push({ from: pos, to: pos + node.nodeSize })
      found.set(id, ranges)
    }
  })
  return found
}

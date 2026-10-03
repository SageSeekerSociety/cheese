// Comment anchors: the words a comment thread is about, marked in the shared
// document with the thread's id.
//
// The mark lives in the document, so it moves with the words: text typed
// before it, a paragraph inserted above it, a teammate's edit elsewhere leave
// it on the same words for every reader. Rewriting the marked words themselves
// as one replacement drops it with them, and then the thread has nothing left
// to point at.
//
// Whatever rewrites a passage (the agent's edit, a whole document written from
// Markdown) builds the new text without marks, so every such change is
// followed by `carryCommentAnchors`: a thread whose words are still there gets
// its mark back on them. Words that were rewritten or deleted are not the words
// the comment was about, so the mark is not moved onto what replaced them; the
// thread keeps where it was instead (lib/docCommentSpots, `placeOf`).
//
// It is not part of the text. The document's Markdown (what is stored,
// searched and given to the agent) has no trace of it, and copying marked text
// does not copy the mark: a pasted passage would otherwise point a second place
// at the same thread. Dragging a block to move it keeps the mark, since the
// words only moved.
import type { Mark as PMMark, MarkType, Node as PMNode } from '@tiptap/pm/model'
import type { Transaction } from '@tiptap/pm/state'

import { Mark, mergeAttributes } from '@tiptap/core'
import { Fragment, Slice } from '@tiptap/pm/model'
import { Plugin, PluginKey } from '@tiptap/pm/state'
import { Transform } from '@tiptap/pm/transform'
import { ySyncPluginKey } from '@tiptap/y-tiptap'

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
        // A local change that rebuilt marked words without changing them (a
        // block style, a paste over the same text) puts the mark back. A change
        // from another reader already carries its marks: their editor did this.
        appendTransaction(trs, before, after) {
          if (!trs.some((tr) => tr.docChanged) || trs.some((tr) => tr.getMeta(CARRIED) || isRemote(tr))) return null
          const tr = after.tr
          for (const run of lostAnchors(before.doc, after.doc)) tr.addMark(run.from, run.to, run.mark)
          return tr.docChanged ? tr.setMeta(CARRIED, true).setMeta('addToHistory', false) : null
        },
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

const CARRIED = 'commentAnchorsCarried'

/** A change that arrived from the shared document (another reader's edit). */
function isRemote(tr: Transaction): boolean {
  return !!(tr.getMeta(ySyncPluginKey) as { isChangeOrigin?: boolean } | undefined)?.isChangeOrigin
}

/** The document's text as one string (blocks joined by a newline), and where
 *  each character is in the document; a joining newline has no position. */
function flatten(doc: PMNode): { text: string; pos: number[] } {
  let text = ''
  const pos: number[] = []
  doc.descendants((node, at) => {
    if (!node.isTextblock) return true
    if (text) {
      text += '\n'
      pos.push(-1)
    }
    node.forEach((child, offset) => {
      const start = at + 1 + offset
      if (child.isText) {
        text += child.text
        for (let i = 0; i < child.text!.length; i++) pos.push(start + i)
      } else {
        text += '\ufffc'
        pos.push(start)
      }
    })
    return false
  })
  return { text, pos }
}

/** `after`, with the comment marks `before` had and `after` lost put back on
 *  the same words, where those words are still there. `after` itself when
 *  nothing was lost. */
export function carryCommentAnchors(before: PMNode, after: PMNode): PMNode {
  const runs = lostAnchors(before, after)
  if (!runs.length) return after
  const tr = new Transform(after)
  for (const run of runs) tr.addMark(run.from, run.to, run.mark)
  return tr.doc
}

/** The marks `carryCommentAnchors` puts back, as ranges of `after`. */
function lostAnchors(before: PMNode, after: PMNode): { from: number; to: number; mark: PMMark }[] {
  const had = commentAnchors(before)
  if (!had.size) return []
  const has = commentAnchors(after)
  const lost = [...had].filter(([id]) => !has.has(id))
  const type = after.type.schema.marks[COMMENT_ANCHOR]
  if (!lost.length || !type) return []
  const old = flatten(before)
  const now = flatten(after)
  // Where the two texts start and stop differing.
  let head = 0
  const max = Math.min(old.text.length, now.text.length)
  while (head < max && old.text[head] === now.text[head]) head++
  let tail = 0
  while (tail < max - head && old.text[old.text.length - 1 - tail] === now.text[now.text.length - 1 - tail]) tail++
  const shift = now.text.length - old.text.length
  const indexOf = (flat: { pos: number[] }, position: number) => {
    const at = flat.pos.findIndex((p) => p >= position)
    return at < 0 ? flat.pos.length : at
  }
  const runs: { from: number; to: number; mark: PMMark }[] = []
  for (const [id, ranges] of lost) {
    const from = indexOf(old, ranges[0].from)
    const to = indexOf(old, ranges[ranges.length - 1].to)
    const words = old.text.slice(from, to)
    if (!words) continue
    let start = -1
    if (to <= head) start = from
    else if (from >= old.text.length - tail) start = from + shift
    else {
      // The change spans them: they are still theirs only if the same words
      // are still in what changed, nearest to where they were.
      const expected = from + shift
      const until = now.text.length - tail
      for (
        let at = now.text.indexOf(words, head);
        at >= 0 && at + words.length <= until;
        at = now.text.indexOf(words, at + 1)
      ) {
        if (start < 0 || Math.abs(at - expected) < Math.abs(start - expected)) start = at
      }
    }
    if (start < 0) continue
    const end = start + words.length
    const mark = type.create({ thread: id })
    for (let i = Math.max(0, start); i < Math.min(end, now.pos.length); i++) {
      const at = now.pos[i]
      if (at < 0) continue
      const last = runs[runs.length - 1]
      if (last?.mark === mark && last.to === at) last.to = at + 1
      else runs.push({ from: at, to: at + 1, mark })
    }
  }
  return runs
}

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

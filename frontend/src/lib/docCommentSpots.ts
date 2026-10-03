// Where a comment being written goes, from the moment its words are selected to
// the moment the thread exists and its mark can be put on them.
//
// The thread's id comes back from the server, so the mark (docSchema
// commentAnchors) is put on the words only after the comment is posted. In the
// meantime teammates keep typing, and their changes reach this editor as whole
// replacements of the document, which ProseMirror's own position mapping cannot
// follow. So the selection is also kept as positions in the shared document
// (Yjs relative positions), which follow the words through anyone's edits.
// When those are gone the words are looked for again, nearest to where they
// were.
//
// Putting the mark on is not an edit the commenter would undo: it stays out of
// the undo history.
//
// The thread also remembers where its words began, as a position in the
// shared document kept beside the text (the `commentPlaces` map). When the
// words are rewritten or deleted the mark goes with them, but that position
// survives, pointing at the nearest text still there: the thread can still be
// shown at about the place it was about.
import type { Editor } from '@tiptap/core'
import type { EditorState } from '@tiptap/pm/state'

import {
  absolutePositionToRelativePosition,
  relativePositionToAbsolutePosition,
  ySyncPluginKey,
} from '@tiptap/y-tiptap'
import * as Y from 'yjs'

import { nearestText } from './docEditMarks'
import { COMMENT_ANCHOR } from './docSchema'

/** The shared map of where each thread's words began. */
const PLACES = 'commentPlaces'

export interface CommentSpot {
  /** The selected words, shown on the thread and looked for if the positions are lost. */
  quote: string
  from: number
  to: number
  rel: { from: unknown; to: unknown } | null
}

interface YState {
  doc: Y.Doc
  type: Y.XmlFragment
  binding: { mapping: Map<unknown, unknown> }
}

function ySync(state: EditorState): YState | null {
  const y = ySyncPluginKey.getState(state) as YState | undefined
  return y?.binding ? y : null
}

/** The selected words from `from` to `to`, kept so they can be found again. */
export function spotAt(editor: Editor, from: number, to: number): CommentSpot {
  const { state } = editor
  const y = ySync(state)
  const rel = y
    ? {
        from: absolutePositionToRelativePosition(from, y.type, y.binding.mapping as never),
        to: absolutePositionToRelativePosition(to, y.type, y.binding.mapping as never),
      }
    : null
  return { quote: state.doc.textBetween(from, to, '\n'), from, to, rel }
}

/** Where the spot's words are now, or null when they are no longer in the document. */
export function locateSpot(editor: Editor, spot: CommentSpot): { from: number; to: number } | null {
  const { doc } = editor.state
  const y = ySync(editor.state)
  if (y && spot.rel) {
    const from = relativePositionToAbsolutePosition(y.doc, y.type, spot.rel.from as never, y.binding.mapping as never)
    const to = relativePositionToAbsolutePosition(y.doc, y.type, spot.rel.to as never, y.binding.mapping as never)
    if (from !== null && to !== null && to > from && doc.textBetween(from, to, '\n') === spot.quote) return { from, to }
  }
  if (doc.textBetween(spot.from, Math.min(spot.to, doc.content.size), '\n') === spot.quote)
    return { from: spot.from, to: spot.to }
  return nearestText(doc, spot.quote, spot.from)
}

/** Mark the spot's words as what `thread` is about. False when they are gone. */
export function anchorComment(editor: Editor, spot: CommentSpot, thread: string): boolean {
  if (!spot.quote || editor.isDestroyed) return false
  const at = locateSpot(editor, spot)
  const type = editor.schema.marks[COMMENT_ANCHOR]
  if (!at || !type) return false
  editor.view.dispatch(editor.state.tr.addMark(at.from, at.to, type.create({ thread })).setMeta('addToHistory', false))
  const y = ySync(editor.state)
  if (y) {
    const place = absolutePositionToRelativePosition(at.from, y.type, y.binding.mapping as never)
    y.doc.getMap<Uint8Array>(PLACES).set(thread, Y.encodeRelativePosition(place as Y.RelativePosition))
  }
  return true
}

/** Where `thread`'s words began, even after they were rewritten or deleted;
 *  null for a thread that never had a place (written before places were kept). */
export function placeOf(editor: Editor, thread: string): number | null {
  const y = ySync(editor.state)
  const saved = y?.doc.getMap<Uint8Array>(PLACES).get(thread)
  if (!y || !saved) return null
  const at = relativePositionToAbsolutePosition(
    y.doc,
    y.type,
    Y.decodeRelativePosition(saved) as never,
    y.binding.mapping as never
  )
  return at === null ? null : Math.min(at, editor.state.doc.content.size)
}

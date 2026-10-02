// Where a newly-loaded server version of the document differs from the one on
// screen, so installing it rewrites only that span.

import type { Node as PMNode } from '@tiptap/pm/model'

/** The span `docReplaceRange` says has to be rewritten. */
export interface DocReplaceRange {
  /** First position in the current doc that differs. */
  from: number
  /** End of the differing span, in the CURRENT doc. */
  to: number
  /** End of the differing span, in the INCOMING doc. */
  sliceTo: number
}

/** Where a newly-loaded server version actually differs from what is on screen.
 *
 * Installing a reload by replacing the whole document (`replaceWith(0, size,
 * next)`) maps every position through a step that deleted everything, so a caret
 * parked in a paragraph that did NOT change lands at the end of the document.
 * The living doc is an editor you can click into just to point at a line, and
 * clicking does not make it dirty — so 芝士's next update arrives with someone's
 * caret sitting in it, and yanks it away. Replacing only the span that differs
 * leaves every position before that span alone.
 *
 * NOT a repaint optimisation: prosemirror-view already diffs node by node and
 * reuses the DOM of unchanged children on a full replace. What a full replace
 * costs is positions — selection, and any decoration that has to be mapped.
 *
 * Returns null when the two documents are identical (nothing to do). Otherwise
 * `from`/`to` are positions in `current` and the replacement is
 * `next.slice(from, sliceTo)`.
 */
export function docReplaceRange(current: PMNode, next: PMNode): DocReplaceRange | null {
  const from = current.content.findDiffStart(next.content)
  if (from === null) return null
  const ends = current.content.findDiffEnd(next.content)
  // findDiffStart already said they differ, so findDiffEnd cannot be null; the
  // guard keeps the types honest rather than guarding a reachable case.
  if (!ends) return null
  let { a: to, b: sliceTo } = ends
  // The tail match can run PAST the head match when one side is shorter (delete
  // a paragraph and the surviving text matches from both directions). Push both
  // ends forward by the overlap so the range stays well-formed.
  const overlap = from - Math.min(to, sliceTo)
  if (overlap > 0) {
    to += overlap
    sliceTo += overlap
  }
  return { from, to, sliceTo }
}

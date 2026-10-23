// Changing part of the live document: replace this text with that one.
//
// The agent speaks Markdown, so an edit names its text as Markdown — `old`, a
// passage of the document as it reads, and `new`, what it should read instead.
// The document is blocks, and other people may be typing in it and have
// suggestions pending in it, so an edit is not applied by rewriting the whole
// document from Markdown (that would erase every pending suggestion and fight
// every caret). It is found in the Markdown, and only the blocks it touches are
// changed in the live document:
//
//   1. R is the live document with pending suggestions rejected — the text the
//      agent read — and md its Markdown. `old` must occur in md exactly once.
//   2. md with the edit applied must convert without losing visible text.
//   3. Both versions of md are parsed; where they differ is the change. The
//      blocks it touches must read the same in R as in the parse of md, or the
//      change cannot be placed (409 unstable): Markdown does not always round-
//      trip, and a passage that does not is not one we can locate safely.
//   4. The change must not touch a pending suggestion (409 suggested), unless
//      an agent is making the change and the suggestion it lands on is one an
//      agent seat left: that suggestion is dropped and the change goes in. A
//      suggestion a person left still waits for its decision.
//   5. The change is replayed on the live document at the matching place,
//      either as it is or, for a suggestion, as tracked insertions and
//      deletions under one suggestion id per edit.
//
// A suggestion is not part of the text, so in suggest mode the edits of one
// request all apply to the same text: a second edit that lands on the first
// one's passage is a change to a pending suggestion, and refused.

import type { Node as PMNode, Slice } from '@tiptap/pm/model'
import type { Mapping } from '@tiptap/pm/transform'

import {
  applySuggestion,
  revertSuggestion,
  revertSuggestions,
  transformToSuggestionTransaction,
} from '@handlewithcare/prosemirror-suggest-changes'
import { EditorState, type Transaction } from '@tiptap/pm/state'
import { Transform } from '@tiptap/pm/transform'
import { updateYFragment } from '@tiptap/y-tiptap'
import * as Y from 'yjs'

import {
  carryCommentAnchors,
  COMMENT_ANCHOR,
  FIELD,
  nodeMarkdown,
  parseMarkdown,
  pendingSuggestions,
  suggestionAuthor,
  suggestionId,
  withoutSuggestions,
} from '../src/lib/docSchema'

import { checkMarkdownWrite } from './writeCheck'

export interface Edit {
  old: string
  new: string
}

export type EditMode = 'direct' | 'suggest'

export interface AppliedEdit extends Edit {
  suggestion_id?: string
}

export type EditRefusal =
  | { status: 422; body: { error: 'edit'; index: number; reason: 'not_found' | 'ambiguous' | 'empty' | 'structure' } }
  | { status: 422; body: { error: 'content'; index: number; message: string; line: number } }
  | { status: 409; body: { error: 'edit'; index: number; reason: 'unstable' | 'suggested' } }

export type EditResult = { ok: true; doc: PMNode; edits: AppliedEdit[] } | ({ ok: false } & EditRefusal)

const SUGGESTION_MARKS = new Set(['insertion', 'deletion', 'modification'])

/** The live document with pending suggestions rejected, and the way back. */
function readable(doc: PMNode): { doc: PMNode; back: Mapping | null } {
  let out: { doc: PMNode; back: Mapping | null } = { doc, back: null }
  revertSuggestions(EditorState.create({ doc }), (tr) => {
    if (tr.steps.length) out = { doc: tr.doc, back: tr.mapping.invert() }
  })
  // Nor are comment marks part of the text: R is compared with Markdown,
  // which has none. Taking a mark off moves nothing, so `back` still holds.
  const comment = out.doc.type.schema.marks[COMMENT_ANCHOR]
  if (comment) out = { ...out, doc: new Transform(out.doc).removeMark(0, out.doc.content.size, comment).doc }
  return out
}

function count(haystack: string, needle: string): number {
  let n = 0
  for (let at = haystack.indexOf(needle); at >= 0; at = haystack.indexOf(needle, at + 1)) n++
  return n
}

/** Where each top-level block starts. */
function offsets(doc: PMNode): number[] {
  const out: number[] = []
  doc.forEach((_child, offset) => out.push(offset))
  return out
}

/** The block of `before` matching block `k`, found in R: blocks are matched
 *  by index when both have as many, otherwise only in the run of equal blocks
 *  at either end. */
function counterpart(before: PMNode, r: PMNode): (k: number) => number | null {
  const nb = before.childCount
  const nr = r.childCount
  if (nb === nr) return (k) => k
  let prefix = 0
  while (prefix < Math.min(nb, nr) && before.child(prefix).eq(r.child(prefix))) prefix++
  let suffix = 0
  while (suffix < Math.min(nb, nr) - prefix && before.child(nb - 1 - suffix).eq(r.child(nr - 1 - suffix))) suffix++
  return (k) => {
    if (k < prefix || (k === prefix && k === nb)) return k
    if (k >= nb - suffix) return k + nr - nb
    return null
  }
}

/** The change `[start, endA)` of `before` → `[start, endB)` of `after`, as
 *  whole blocks when it does not stay inside one block's text.
 *
 *  The two versions are compared character by character, so a change that
 *  adds, removes or retypes blocks is found from the first character that
 *  differs to the last: a paragraph added before one that starts with the same
 *  character begins inside that paragraph's text. Placed as it is, it splits
 *  and joins paragraphs, which a suggestion records on text, and which leaves
 *  an empty paragraph behind when rejected. So such a change is taken again,
 *  block by block, in the container both its ends are in: the blocks that
 *  differ, between the ones both versions have alike at either end. Everything
 *  before `start` is the same in both versions, so that container opens at the
 *  same place in both. */
function wholeBlocks(
  before: PMNode,
  after: PMNode,
  start: number,
  endA: number,
  endB: number
): { start: number; endA: number; endB: number } {
  const $a = before.resolve(start)
  const $b = after.resolve(start)
  if ($a.parent.isTextblock && $a.sameParent(before.resolve(endA)) && $b.sameParent(after.resolve(endB))) {
    return { start, endA, endB }
  }
  const depth = Math.min($a.sharedDepth(endA), $b.sharedDepth(endB))
  const one = $a.node(depth)
  const two = $b.node(depth)
  const base = $a.start(depth)
  let head = 0
  let from = base
  while (head < one.childCount && head < two.childCount && one.child(head).eq(two.child(head))) {
    from += one.child(head).nodeSize
    head++
  }
  let toA = base + one.content.size
  let toB = base + two.content.size
  for (
    let tail = 0;
    tail < one.childCount - head &&
    tail < two.childCount - head &&
    one.child(one.childCount - 1 - tail).eq(two.child(two.childCount - 1 - tail));
    tail++
  ) {
    toA -= one.child(one.childCount - 1 - tail).nodeSize
    toB -= two.child(two.childCount - 1 - tail).nodeSize
  }
  return { start: from, endA: toA, endB: toB }
}

interface Located {
  from: number
  to: number
  slice: Slice
}

/** Where the change from `before` to `after` lands in R, or why it cannot. */
function locate(before: PMNode, after: PMNode, r: PMNode, mode: EditMode): Located | 'unstable' | null {
  let start = before.content.findDiffStart(after.content)
  if (start === null) return null
  const ends = before.content.findDiffEnd(after.content) ?? { a: start, b: start }
  let { a: endA, b: endB } = ends
  // Repeated text lets the two ends cross; push both past the start.
  const overlap = start - Math.min(endA, endB)
  if (overlap > 0) {
    endA += overlap
    endB += overlap
  }
  // A suggestion is made of whole blocks where it is not text inside one; a
  // direct change goes in exactly where it differs.
  if (mode === 'suggest') ({ start, endA, endB } = wholeBlocks(before, after, start, endA, endB))
  const starts = offsets(before)
  const rStarts = offsets(r)
  const nb = before.childCount
  // The first block that ends after `start`, the last that begins before `endA`.
  let first = nb
  for (let k = 0; k < nb; k++) {
    if (starts[k] + before.child(k).nodeSize > start) {
      first = k
      break
    }
  }
  let last = -1
  for (let k = nb - 1; k >= 0; k--) {
    if (starts[k] < endA) {
      last = k
      break
    }
  }
  const toR = counterpart(before, r)
  for (let k = first; k <= last; k++) {
    const m = toR(k)
    if (m === null || m >= r.childCount || !r.child(m).eq(before.child(k))) return 'unstable'
  }
  const at = (pos: number, k: number): number | null => {
    // Past the last block: the end of the document.
    if (k >= nb) return r.content.size
    const m = toR(k)
    if (m === null || m >= r.childCount) return null
    return rStarts[m] + (pos - starts[k])
  }
  const from = at(start, first)
  const to = endA === start ? from : at(endA, last)
  if (from === null || to === null) return 'unstable'
  return { from, to, slice: after.slice(start, endB) }
}

/** The ids of the pending suggestions the range `[from, to)` lands on. */
function suggestionsAt(doc: PMNode, from: number, to: number): Set<string> {
  const ids = new Set<string>()
  if (from < to) {
    doc.nodesBetween(from, to, (node) => {
      for (const mark of node.marks) {
        if (SUGGESTION_MARKS.has(mark.type.name)) ids.add(String(mark.attrs.id))
      }
      return true
    })
    return ids
  }
  // An insertion point inside a suggested passage: the same suggestion on both
  // sides of it.
  const $pos = doc.resolve(from)
  const before = new Set<string>()
  for (const mark of $pos.nodeBefore?.marks ?? []) {
    if (SUGGESTION_MARKS.has(mark.type.name)) before.add(String(mark.attrs.id))
  }
  for (const mark of $pos.nodeAfter?.marks ?? []) {
    if (SUGGESTION_MARKS.has(mark.type.name) && before.has(String(mark.attrs.id))) ids.add(String(mark.attrs.id))
  }
  return ids
}

/** The document with the marks of the given suggestions dropped, each one
 *  rejected: an insertion's text goes, a deletion's stays, a modification
 *  returns to its previous value. */
function revertMarks(doc: PMNode, ids: Iterable<string>): PMNode {
  let out = doc
  for (const id of ids) {
    revertSuggestion(id)(EditorState.create({ doc: out }), (tr) => {
      out = tr.doc
    })
  }
  return out
}

/** The change as a suggestion, or null when it cannot be one. A suggestion
 *  marks the text it adds and removes, or the whole blocks it adds, removes or
 *  retypes — a block in a run of blocks or list items (suggestableBlocks). A
 *  change to the rows or columns of a table, the columns of a row of columns,
 *  or the items of a timeline or stat cards
 *  marks a part those hold in a fixed shape, so it can only be made directly.
 *  So can one that leaves nothing to accept or reject, one that would change
 *  the text before anyone accepts it, and one whose acceptance would not give
 *  the text the change asks for. */
function suggestAs(tr: Transaction, state: EditorState, id: string): PMNode | null {
  let doc: PMNode
  try {
    doc = transformToSuggestionTransaction(tr, state, () => id).doc
    doc.check()
  } catch {
    return null
  }
  const proposed = pendingSuggestions(doc).find((item) => item.id === id)
  if (!proposed || (!proposed.old && !proposed.new)) return null
  if (nodeMarkdown(withoutSuggestions(doc)) !== nodeMarkdown(withoutSuggestions(state.doc))) return null
  let accepted = doc
  applySuggestion(id)(EditorState.create({ doc }), (applied) => {
    accepted = applied.doc
  })
  if (nodeMarkdown(withoutSuggestions(accepted)) !== nodeMarkdown(withoutSuggestions(tr.doc))) return null
  return doc
}

/** Whether an agent making the change may drop the suggestions it lands on:
 *  only an agent seat's own suggestions, never a person's. */
function mayDrop(touched: Set<string>, editorIsAgent: boolean | undefined, agentAuthors: ReadonlySet<string>): boolean {
  if (touched.size === 0) return true
  if (editorIsAgent !== true) return false
  return [...touched].every((id) => agentAuthors.has(suggestionAuthor(id)))
}

/** The live document with `edits` applied by `actor`, or why they cannot be.
 *
 * `context.editorIsAgent`: the writer is an agent seat. `context.agentAuthors`:
 * the handles among the document's pending suggestions that are agent seats —
 * the backend judges them (a suggestion's author is the part of its id before
 * the colon). An agent's change that lands on one of those drops it and goes in;
 * one that lands on a person's suggestion is still refused (409 ``suggested``). */
export function applyEdits(
  live: PMNode,
  edits: Edit[],
  mode: EditMode,
  actor: string,
  context: { editorIsAgent?: boolean; agentAuthors?: ReadonlySet<string> } = {}
): EditResult {
  let working = live
  const applied: AppliedEdit[] = []
  const agentAuthors = context.agentAuthors ?? new Set<string>()
  for (const [index, edit] of edits.entries()) {
    if (!edit.old) return { ok: false, status: 422, body: { error: 'edit', index, reason: 'empty' } }
    // An agent's change may drop the suggestions it lands on; each attempt drops
    // at least one, so this ends.
    for (let attempt = 0; ; attempt++) {
      const { doc: r, back } = readable(working)
      const md = nodeMarkdown(r)
      const found = count(md, edit.old)
      if (found !== 1) {
        return {
          ok: false,
          status: 422,
          body: { error: 'edit', index, reason: found === 0 ? 'not_found' : 'ambiguous' },
        }
      }
      const next = md.replace(edit.old, () => edit.new)
      const problem = checkMarkdownWrite(next)
      if (problem) {
        return {
          ok: false,
          status: 422,
          body: { error: 'content', index, message: problem.message, line: problem.line },
        }
      }
      const located = locate(parseMarkdown(md), parseMarkdown(next), r, mode)
      if (located === 'unstable') return { ok: false, status: 409, body: { error: 'edit', index, reason: 'unstable' } }
      if (located === null) {
        applied.push({ old: edit.old, new: edit.new })
        break
      }
      // Back from R to the live document: a range starts after, and ends
      // before, a suggested insertion R does not have.
      const from = back ? back.map(located.from, 1) : located.from
      const to = located.to === located.from ? from : back ? back.map(located.to, -1) : located.to
      if (from > to) return { ok: false, status: 409, body: { error: 'edit', index, reason: 'suggested' } }
      const touched = suggestionsAt(working, from, to)
      if (touched.size) {
        if (attempt >= 8 || !mayDrop(touched, context.editorIsAgent, agentAuthors)) {
          return { ok: false, status: 409, body: { error: 'edit', index, reason: 'suggested' } }
        }
        // Drop the stale suggestions, then place the change on the document
        // they were no longer part of.
        working = revertMarks(working, touched)
        continue
      }
      const state = EditorState.create({ doc: working })
      const tr = state.tr.replace(from, to, located.slice)
      if (mode === 'suggest') {
        const id = suggestionId(actor)
        const suggested = suggestAs(tr, state, id)
        if (!suggested) return { ok: false, status: 422, body: { error: 'edit', index, reason: 'structure' } }
        working = suggested
        applied.push({ old: edit.old, new: edit.new, suggestion_id: id })
      } else {
        working = tr.doc
        applied.push({ old: edit.old, new: edit.new })
      }
      break
    }
  }
  // The replayed blocks come from Markdown, without comment marks: give them back.
  return { ok: true, doc: carryCommentAnchors(live, working), edits: applied }
}

/** Make the Yjs document hold `node`, as a diff against what it holds (the
 *  way writeMarkdown does), so everything the edit did not touch keeps its
 *  identity. */
export function writeNode(doc: Y.Doc, node: PMNode): void {
  const fragment = doc.getXmlFragment(FIELD)
  doc.transact(() => updateYFragment(doc, fragment, node, { mapping: new Map(), isOMark: new Map() }))
}

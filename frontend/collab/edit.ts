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
//   4. The change must not touch a pending suggestion (409 suggested): someone
//      has to decide that one first.
//   5. The change is replayed on the live document at the matching place,
//      either as it is or, for a suggestion, as tracked insertions and
//      deletions under one suggestion id per edit.
//
// A suggestion is not part of the text, so in suggest mode the edits of one
// request all apply to the same text: a second edit that lands on the first
// one's passage is a change to a pending suggestion, and refused.

import type { Node as PMNode, Slice } from '@tiptap/pm/model'
import type { Mapping } from '@tiptap/pm/transform'

import { revertSuggestions, transformToSuggestionTransaction } from '@handlewithcare/prosemirror-suggest-changes'
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

interface Located {
  from: number
  to: number
  slice: Slice
}

/** Where the change from `before` to `after` lands in R, or why it cannot. */
function locate(before: PMNode, after: PMNode, r: PMNode): Located | 'unstable' | null {
  const start = before.content.findDiffStart(after.content)
  if (start === null) return null
  const ends = before.content.findDiffEnd(after.content) ?? { a: start, b: start }
  let { a: endA, b: endB } = ends
  // Repeated text lets the two ends cross; push both past the start.
  const overlap = start - Math.min(endA, endB)
  if (overlap > 0) {
    endA += overlap
    endB += overlap
  }
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

function suggestionAt(doc: PMNode, from: number, to: number): boolean {
  let found = false
  if (from < to) {
    doc.nodesBetween(from, to, (node) => {
      if (found) return false
      if (node.marks.some((mark) => SUGGESTION_MARKS.has(mark.type.name))) found = true
      return !found
    })
    return found
  }
  // An insertion point inside a suggested passage.
  const $pos = doc.resolve(from)
  const ids = (node: PMNode | null | undefined) =>
    new Set(
      (node?.marks ?? []).filter((mark) => SUGGESTION_MARKS.has(mark.type.name)).map((mark) => String(mark.attrs.id))
    )
  const before = ids($pos.nodeBefore)
  return [...ids($pos.nodeAfter)].some((id) => before.has(id))
}

/** The change as a suggestion, or null when it cannot be one. A suggestion
 *  marks text: the document's blocks cannot carry a suggestion mark, so a
 *  change that reshapes whole blocks (a paragraph turned into a list, two
 *  paragraphs joined) can only be made directly. So can one that leaves no
 *  text to accept or reject, or that would change the document's text before
 *  anyone accepts it. */
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
  return doc
}

/** The live document with `edits` applied by `actor`, or why they cannot be. */
export function applyEdits(live: PMNode, edits: Edit[], mode: EditMode, actor: string): EditResult {
  let working = live
  const applied: AppliedEdit[] = []
  for (const [index, edit] of edits.entries()) {
    if (!edit.old) return { ok: false, status: 422, body: { error: 'edit', index, reason: 'empty' } }
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
    const located = locate(parseMarkdown(md), parseMarkdown(next), r)
    if (located === 'unstable') return { ok: false, status: 409, body: { error: 'edit', index, reason: 'unstable' } }
    if (located === null) {
      applied.push({ old: edit.old, new: edit.new })
      continue
    }
    // Back from R to the live document: a range starts after, and ends
    // before, a suggested insertion R does not have.
    const from = back ? back.map(located.from, 1) : located.from
    const to = located.to === located.from ? from : back ? back.map(located.to, -1) : located.to
    if (to < from || suggestionAt(working, from, to)) {
      return { ok: false, status: 409, body: { error: 'edit', index, reason: 'suggested' } }
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

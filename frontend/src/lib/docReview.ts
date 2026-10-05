// The changes someone asked the AI teammate to make, found again in the
// document: where each one's new text sits now, what it replaced, and whether it
// is still there (a change that was restored, or edited away since, is gone).
//
// A change is a Markdown replacement (`old` → `new`, each with enough context to
// occur once). It is found by the plain text of `new`; what changed inside it is
// what `old` and `new` do not share at their two ends.

import type { Node as PMNode } from '@tiptap/pm/model'
import type { DocEdit } from './docEdits'

import { flatText, occurrences, plainOf, rangeOf } from './docEdits'

/** 「查看改动」opens the document with these. */
export interface DocReviewRequest {
  /** Who asked for the changes, as people call them. */
  requester: string
  edits: DocEdit[]
}

/** 聊天里点开的一份项目资料库文档：打开它，而不是这个对话自己的文档。 */
export interface OpenedDocument {
  id: string
  title: string
}

export interface LocatedEdit {
  /** Its place in the request. */
  index: number
  edit: DocEdit
  /** Its new text is still in the document. */
  live: boolean
  /** The part that changed, in the document (empty for a pure deletion). */
  from: number
  to: number
  /** What that part replaced. */
  oldText: string
}

const plain = new Map<string, string>()
function plainCached(markdown: string): string {
  let text = plain.get(markdown)
  if (text === undefined) {
    text = plainOf(markdown)
    if (plain.size > 200) plain.clear()
    plain.set(markdown, text)
  }
  return text
}

/** How many characters two strings share at the start, and then at the end. */
function sharedEnds(a: string, b: string): [number, number] {
  let head = 0
  while (head < a.length && head < b.length && a[head] === b[head]) head++
  let tail = 0
  while (tail < a.length - head && tail < b.length - head && a[a.length - 1 - tail] === b[b.length - 1 - tail]) tail++
  return [head, tail]
}

export function locateEdits(doc: PMNode, edits: DocEdit[]): LocatedEdit[] {
  const flat = flatText(doc)
  return edits.map((edit, index) => {
    const before = plainCached(edit.old)
    const after = plainCached(edit.new)
    const [at] = occurrences(flat.text, after)
    const [head, tail] = sharedEnds(before, after)
    const oldText = before.slice(head, before.length - tail)
    if (!after || at === undefined) return { index, edit, live: false, from: 0, to: 0, oldText }
    // Nothing differs in the text (only formatting did): the whole new text is the change.
    const whole = head + tail >= after.length && head + tail >= before.length
    const range = whole ? rangeOf(flat, at, at + after.length) : rangeOf(flat, at + head, at + after.length - tail)
    return { index, edit, live: true, ...range, oldText: whole ? '' : oldText }
  })
}

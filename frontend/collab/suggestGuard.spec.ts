// What a mentor's connection may write (#3830): suggestions of their own, and
// nothing else. The text with every suggestion rejected must stay as it was;
// a suggestion they add or take away must be theirs.
import type { Node as PMNode } from '@tiptap/pm/model'

import { Transform } from '@tiptap/pm/transform'
import { updateYFragment } from '@tiptap/y-tiptap'
import { describe, expect, it } from 'vitest'
import * as Y from 'yjs'

import { FIELD, liveNode, suggestionId, writeMarkdown } from '../src/lib/docSchema'

import { suggestOnlyProblem } from './suggestGuard'

function live(markdown: string): Y.Doc {
  const doc = new Y.Doc()
  writeMarkdown(doc, markdown)
  return doc
}

/** The update that turns `doc` into what `change` makes of its node. */
function updateOf(doc: Y.Doc, change: (node: PMNode) => PMNode): Uint8Array {
  const copy = new Y.Doc()
  Y.applyUpdate(copy, Y.encodeStateAsUpdate(doc))
  const next = change(liveNode(copy))
  copy.transact(() =>
    updateYFragment(copy, copy.getXmlFragment(FIELD), next, { mapping: new Map(), isOMark: new Map() })
  )
  return Y.encodeStateAsUpdate(copy, Y.encodeStateVector(doc))
}

/** Insert `text` after the first paragraph's text, marked as a suggestion by `id`. */
function suggestInsert(text: string, id: string) {
  return (node: PMNode) => {
    const at = 1 + (node.firstChild?.content.size ?? 0)
    const mark = node.type.schema.marks.insertion.create({ id })
    return new Transform(node).insert(at, node.type.schema.text(text, [mark])).doc
  }
}

describe('suggestOnlyProblem', () => {
  it('lets through a suggestion of the sender’s own', () => {
    const doc = live('第一周领工位。\n')
    const update = updateOf(doc, suggestInsert('再开通服务器账号。', suggestionId('mentor')))
    expect(suggestOnlyProblem(doc, update, 'mentor')).toBeNull()
  })

  it('refuses a change to the text itself', () => {
    const doc = live('第一周领工位。\n')
    const copy = new Y.Doc()
    Y.applyUpdate(copy, Y.encodeStateAsUpdate(doc))
    writeMarkdown(copy, '第一周不用领工位。\n')
    const update = Y.encodeStateAsUpdate(copy, Y.encodeStateVector(doc))
    expect(suggestOnlyProblem(doc, update, 'mentor')).toBe('text')
  })

  it('refuses a suggestion made in somebody else’s name', () => {
    const doc = live('第一周领工位。\n')
    const update = updateOf(doc, suggestInsert('再开通服务器账号。', suggestionId('owner')))
    expect(suggestOnlyProblem(doc, update, 'mentor')).toBe('author')
  })
})

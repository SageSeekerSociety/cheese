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

  /** Mark the first formula in the document as a suggested deletion by `id`. */
  function deleteFormula(id: string) {
    return (node: PMNode) => {
      let at = -1
      node.descendants((child, pos) => {
        if (at < 0 && child.type.name === 'mathInline') at = pos
      })
      expect(at).toBeGreaterThan(-1)
      const mark = node.type.schema.marks.deletion.create({ id })
      return new Transform(node).addMark(at, at + 1, mark).doc
    }
  }

  it('refuses a suggestion on a formula made in somebody else’s name, or taken away from them', () => {
    const doc = live('面积是 $x+1$。\n')
    expect(suggestOnlyProblem(doc, updateOf(doc, deleteFormula(suggestionId('mentor'))), 'mentor')).toBeNull()
    expect(suggestOnlyProblem(doc, updateOf(doc, deleteFormula(suggestionId('owner'))), 'mentor')).toBe('author')

    Y.applyUpdate(doc, updateOf(doc, deleteFormula(suggestionId('owner'))))
    const taken = updateOf(doc, (node) => new Transform(node).removeMark(0, node.content.size).doc)
    expect(suggestOnlyProblem(doc, taken, 'mentor')).toBe('author')
  })

  it('refuses a mark the document does not allow on a block, which no later suggestion could be placed beside', () => {
    const doc = live('第一段。\n\n第二段。\n')
    // Built by hand, as a tampered client would send it: a transform refuses it.
    const bold = (node: PMNode) => {
      const first = node.child(0).mark([node.type.schema.marks.bold.create()])
      return node.copy(node.content.replaceChild(0, first))
    }
    expect(suggestOnlyProblem(doc, updateOf(doc, bold), 'mentor')).toBe('shape')
  })
})

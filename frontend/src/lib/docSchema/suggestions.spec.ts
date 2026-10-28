// A suggested change is not part of the text until a person accepts it: the
// stored text, what search sees and what the agent reads stay the text as it
// was, and every reader of the shared document sees the same suggestion.
import type { Transaction } from '@tiptap/pm/state'

import {
  applySuggestion,
  revertSuggestion,
  transformToSuggestionTransaction,
} from '@handlewithcare/prosemirror-suggest-changes'
import { Editor } from '@tiptap/core'
import Collaboration from '@tiptap/extension-collaboration'
import { afterEach, describe, expect, it } from 'vitest'
import * as Y from 'yjs'

import { docExtensions, exportMarkdown, liveSuggestions, suggestionId, writeMarkdown } from '.'

const editors: Editor[] = []
afterEach(() => editors.splice(0).forEach((editor) => editor.destroy()))

function open(doc: Y.Doc) {
  const editor = new Editor({ extensions: [...docExtensions(), Collaboration.configure({ document: doc })] })
  editors.push(editor)
  return editor
}

/** Two people with the same document open, kept in sync the way the service would. */
function twoReaders(markdown: string) {
  const a = new Y.Doc()
  const b = new Y.Doc()
  writeMarkdown(a, markdown)
  Y.applyUpdate(b, Y.encodeStateAsUpdate(a))
  a.on('update', (u: Uint8Array, origin: unknown) => origin !== 'peer' && Y.applyUpdate(b, u, 'peer'))
  b.on('update', (u: Uint8Array, origin: unknown) => origin !== 'peer' && Y.applyUpdate(a, u, 'peer'))
  return { a, b, editorA: open(a), editorB: open(b) }
}

/** `author` proposes replacing `from` with `to` in the editor's document. */
function suggest(editor: Editor, author: string, from: string, to: string) {
  let at = -1
  editor.state.doc.descendants((node, pos) => {
    if (at < 0 && node.isText && node.text?.includes(from)) at = pos + node.text.indexOf(from)
  })
  expect(at).toBeGreaterThan(-1)
  const tr = editor.state.tr.insertText(to, at, at + from.length)
  editor.view.dispatch(transformToSuggestionTransaction(tr, editor.state, () => suggestionId(author)))
}

function decide(editor: Editor, accept: boolean) {
  const [pending] = liveSuggestions(editorDoc(editor))
  const command = accept ? applySuggestion(pending.id) : revertSuggestion(pending.id)
  command(editor.state, editor.view.dispatch)
}

const docs = new WeakMap<Editor, Y.Doc>()
function editorDoc(editor: Editor): Y.Doc {
  return docs.get(editor)!
}

describe('a suggested change', () => {
  it('reaches every reader but stays out of the text until someone accepts it', () => {
    const { a, b, editorA, editorB } = twoReaders('数据量到一千万行时开始评估迁移。')
    docs.set(editorA, a).set(editorB, b)

    suggest(editorA, 'cheese', '一千万', '五百万')

    expect(liveSuggestions(b)).toEqual([expect.objectContaining({ author: 'cheese', old: '一千万', new: '五百万' })])
    expect(exportMarkdown(a)).toBe('数据量到一千万行时开始评估迁移。')
    expect(exportMarkdown(b)).toBe('数据量到一千万行时开始评估迁移。')

    decide(editorB, true)

    expect(liveSuggestions(a)).toEqual([])
    expect(exportMarkdown(a)).toBe('数据量到五百万行时开始评估迁移。')
  })

  it('leaves the text as it was when rejected', () => {
    const { a, b, editorA, editorB } = twoReaders('数据量到一千万行时开始评估迁移。')
    docs.set(editorA, a).set(editorB, b)

    suggest(editorA, 'cheese', '一千万', '五百万')
    decide(editorB, false)

    expect(liveSuggestions(a)).toEqual([])
    expect(exportMarkdown(a)).toBe('数据量到一千万行时开始评估迁移。')
    expect(editorA.getText()).toBe('数据量到一千万行时开始评估迁移。')
  })
})

/** `author` makes the change `change` builds, as a suggestion. */
function suggestBlocks(editor: Editor, author: string, change: (tr: Transaction) => void) {
  const tr = editor.state.tr
  change(tr)
  editor.view.dispatch(transformToSuggestionTransaction(tr, editor.state, () => suggestionId(author)))
}

function paragraph(editor: Editor, text: string) {
  return editor.schema.nodes.paragraph.create(null, editor.schema.text(text))
}

describe('a suggestion on a whole block', () => {
  const TEXT = '第一段讲目标。\n\n第二段讲范围。'

  function addedAtEnd() {
    const readers = twoReaders(TEXT)
    docs.set(readers.editorA, readers.a).set(readers.editorB, readers.b)
    const { editorA } = readers
    suggestBlocks(editorA, 'mentor', (tr) => tr.insert(tr.doc.content.size, paragraph(editorA, '第三段讲排期。')))
    return readers
  }

  it('adding a paragraph reaches the other reader and is still there when the document is opened again', () => {
    const { a, b } = addedAtEnd()

    const pending = [expect.objectContaining({ author: 'mentor', old: '', new: '第三段讲排期。' })]
    expect(liveSuggestions(b)).toEqual(pending)
    expect(exportMarkdown(a)).toBe(TEXT)
    expect(exportMarkdown(b)).toBe(TEXT)

    const reopened = new Y.Doc()
    Y.applyUpdate(reopened, Y.encodeStateAsUpdate(b))
    expect(liveSuggestions(reopened)).toEqual(pending)
    expect(exportMarkdown(reopened)).toBe(TEXT)
  })

  it('adding a paragraph keeps it once accepted', () => {
    const { a, editorB } = addedAtEnd()
    decide(editorB, true)

    expect(liveSuggestions(a)).toEqual([])
    expect(exportMarkdown(a)).toBe(`${TEXT}\n\n第三段讲排期。`)
  })

  it('adding a paragraph leaves nothing behind once rejected', () => {
    const { a, editorA, editorB } = addedAtEnd()
    decide(editorB, false)

    expect(liveSuggestions(a)).toEqual([])
    expect(exportMarkdown(a)).toBe(TEXT)
    expect(editorA.state.doc.childCount).toBe(2)
  })

  it('removing a paragraph takes it out only once accepted', () => {
    for (const accept of [true, false]) {
      const { a, b, editorA, editorB } = twoReaders(TEXT)
      docs.set(editorA, a).set(editorB, b)
      const second = editorA.state.doc.child(0).nodeSize
      suggestBlocks(editorA, 'mentor', (tr) => tr.delete(second, tr.doc.content.size))

      expect(liveSuggestions(b)).toEqual([expect.objectContaining({ old: '第二段讲范围。', new: '' })])
      expect(exportMarkdown(b)).toBe(TEXT)
      decide(editorB, accept)
      expect(exportMarkdown(a)).toBe(accept ? '第一段讲目标。' : TEXT)
    }
  })

  it('turning a paragraph into a heading is decided on its own, apart from another person’s', () => {
    const { a, b, editorA, editorB } = twoReaders(TEXT)
    docs.set(editorA, a).set(editorB, b)
    const heading = editorA.schema.nodes.heading
    const second = editorA.state.doc.child(0).nodeSize
    suggestBlocks(editorA, 'mentor', (tr) => tr.setBlockType(0, 1, heading, { level: 2 }))
    suggestBlocks(editorA, 'teacher', (tr) => tr.setBlockType(second, second + 1, heading, { level: 3 }))

    expect(exportMarkdown(b)).toBe(TEXT)
    const byAuthor = (author: string) => liveSuggestions(b).find((item) => item.author === author)!
    expect(byAuthor('mentor')).toMatchObject({ old: '第一段讲目标。', new: '## 第一段讲目标。' })

    revertSuggestion(byAuthor('mentor').id)(editorB.state, editorB.view.dispatch)
    expect(liveSuggestions(a)).toEqual([expect.objectContaining({ author: 'teacher' })])
    applySuggestion(byAuthor('teacher').id)(editorB.state, editorB.view.dispatch)
    expect(exportMarkdown(a)).toBe('第一段讲目标。\n\n### 第二段讲范围。')
  })
})

// A suggested change is not part of the text until a person accepts it: the
// stored text, what search sees and what the agent reads stay the text as it
// was, and every reader of the shared document sees the same suggestion.
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

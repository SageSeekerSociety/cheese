// The shared document belongs to everyone who has it open, so an editor may
// only change it when somebody edits. Anything an editor adds on its own —
// a paragraph after a closing table, say — would be recorded as a version by
// every reader, and appended once per reader.
import { Editor } from '@tiptap/core'
import Collaboration from '@tiptap/extension-collaboration'
import { afterEach, describe, expect, it } from 'vitest'
import * as Y from 'yjs'

import { docExtensions, exportMarkdown, writeMarkdown } from '.'

const editors: Editor[] = []
afterEach(() => editors.splice(0).forEach((editor) => editor.destroy()))

function open(doc: Y.Doc) {
  const editor = new Editor({ extensions: [...docExtensions(), Collaboration.configure({ document: doc })] })
  editors.push(editor)
  return editor
}

describe('opening a shared document', () => {
  it.each([
    ['a table', '正文\n\n| 列 | 值 |\n| --- | --- |\n| a | 1 |\n'],
    ['a list', '正文\n\n- 一\n- 二\n'],
    ['a code block', '正文\n\n```ts\nconst a = 1\n```\n'],
  ])('leaves a document that ends in %s as it was', (_name, markdown) => {
    const doc = new Y.Doc()
    writeMarkdown(doc, markdown)
    const before = exportMarkdown(doc)
    let changes = 0
    doc.on('update', () => changes++)

    const editor = open(doc)
    // Anything that redraws without editing: a decoration rebuild, a selection.
    editor.view.dispatch(editor.state.tr.setMeta('redraw', true))
    editor.commands.setTextSelection(1)

    expect(changes).toBe(0)
    expect(exportMarkdown(doc)).toBe(before)
  })
})

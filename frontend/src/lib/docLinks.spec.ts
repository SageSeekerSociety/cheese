import { Editor } from '@tiptap/core'
import Collaboration from '@tiptap/extension-collaboration'
import { afterEach, describe, expect, it } from 'vitest'

import { applyDocLink, captureDocLink, captureNewDocLink, safeDocHref } from './docLinks'
import { localDocSession } from './docLocalSession'
import { docExtensions } from './docSchema'

const editors: Editor[] = []
afterEach(() => editors.splice(0).forEach((editor) => editor.destroy()))
function setup() {
  // The document is always collaborative, and undo is the collaboration
  // extension's: a link edit has to be one step of it.
  const session = localDocSession('[first](https://old.example) and tail')
  const editor = new Editor({
    extensions: [...docExtensions(), Collaboration.configure({ document: session.doc })],
  })
  editors.push(editor)
  return editor
}
describe('real PM link operations', () => {
  it('changes one mark in one transaction and preserves the unrelated caret', () => {
    const editor = setup()
    const target = captureDocLink(editor, 2)!
    editor.commands.setTextSelection(15)
    const caret = editor.state.selection.from
    let changes = 0
    editor.on('transaction', ({ transaction }) => {
      if (transaction.docChanged) changes++
    })
    expect(applyDocLink(target, 'https://new.example')).toBe('ok')
    expect(changes).toBe(1)
    expect(editor.state.selection.from).toBe(caret)
    expect(captureDocLink(editor, 2)?.href).toBe('https://new.example')
    editor.commands.undo()
    expect(captureDocLink(editor, 2)?.href).toBe('https://old.example')
  })
  it('removes only the link mark and retains text', () => {
    const editor = setup()
    const before = editor.state.doc.textContent
    expect(applyDocLink(captureDocLink(editor, 2)!, null)).toBe('ok')
    expect(editor.state.doc.textContent).toBe(before)
    expect(captureDocLink(editor, 2)).toBeNull()
  })
  it('refuses stale, unsafe, or readonly edits', () => {
    const editor = setup()
    const target = captureDocLink(editor, 2)!
    expect(applyDocLink(target, 'javascript:alert(1)')).toBe('invalid')
    editor.setEditable(false)
    expect(applyDocLink(target, 'https://new.example')).toBe('readonly')
    editor.setEditable(true)
    editor.commands.insertContent('changed')
    expect(applyDocLink(target, 'https://new.example')).toBe('stale')
  })
  it('captures a new link before moving focus and never navigates unsafe schemes', () => {
    const editor = setup()
    editor.commands.setTextSelection({ from: 11, to: 15 })
    const target = captureNewDocLink(editor)!
    editor.commands.setTextSelection(1)
    expect(applyDocLink(target, '/topics/123')).toBe('ok')
    expect(captureDocLink(editor, 12)?.href).toBe('/topics/123')
    expect(safeDocHref('//evil.example')).toBeNull()
    expect(safeDocHref('data:text/html,x')).toBeNull()
  })
})

// @vitest-environment jsdom
// The first edit after a document opens is kept, even when a plugin was added
// after the editor (the drag handle adds one when it mounts) and nothing moved
// the caret before the edit.
import Collaboration from '@tiptap/extension-collaboration'
import { Plugin, PluginKey } from '@tiptap/pm/state'
import { Editor } from '@tiptap/vue-3'
import { afterEach, describe, expect, it } from 'vitest'
import * as Y from 'yjs'

import { docExtensions, exportMarkdown, writeMarkdown } from '../../../lib/docSchema'

import { DocEditor } from './docEditor'

const editors: Editor[] = []
afterEach(() => editors.splice(0).forEach((editor) => editor.destroy()))

function open(make: typeof Editor) {
  const doc = new Y.Doc()
  writeMarkdown(doc, '正文。\n')
  const editor = new make({
    element: document.createElement('div'),
    extensions: [...docExtensions(), Collaboration.configure({ document: doc })],
  })
  editors.push(editor)
  editor.registerPlugin(new Plugin({ key: new PluginKey('late') }))
  return { doc, editor }
}

describe('the first edit after opening', () => {
  it('is kept', () => {
    const { doc, editor } = open(DocEditor)
    editor.view.dispatch(editor.state.tr.insertText('新', 1))
    expect(exportMarkdown(doc)).toBe('新正文。')
  })
})

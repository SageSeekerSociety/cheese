import { webcrypto } from 'node:crypto'

import { Editor } from '@tiptap/core'
import { TextSelection } from '@tiptap/pm/state'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { captureDocSelection, validateDocSelection } from './docAiSelection'
import { docExtensions } from './docMarkdown'

const editors: Editor[] = []
afterEach(() => {
  editors.splice(0).forEach((editor) => editor.destroy())
  vi.unstubAllGlobals()
})
function setup(source: string, prefix = '') {
  vi.stubGlobal('crypto', webcrypto)
  const editor = new Editor({
    extensions: docExtensions(),
    content: source.slice(prefix.length),
    contentType: 'markdown',
  })
  editors.push(editor)
  const canonical = {
    source,
    document_id: 'doc',
    base_version: 4,
    offset_unit: 'utf8-bytes' as const,
    nodes: [{ node_id: 'node', start: 0, end: new TextEncoder().encode(source).length }],
  }
  return { editor, canonical }
}
function select(editor: Editor, from: number, to: number) {
  editor.view.dispatch(editor.state.tr.setSelection(TextSelection.create(editor.state.doc, from, to)))
  return captureDocSelection(editor)!
}

describe('persisted raw source selection', () => {
  it('authorizes the second repeated paragraph by PM position, never the first quote', async () => {
    const source = '重复😀文字\n\n重复😀文字'
    const { editor, canonical } = setup(source)
    const second = editor.state.doc.firstChild!.nodeSize + 1
    const result = await validateDocSelection(select(editor, second, second + 4), canonical, source, 4, '')
    expect(result?.start).toBe(new TextEncoder().encode('重复😀文字\n\n').length)
    expect(result?.end).toBe(new TextEncoder().encode('重复😀文字\n\n重复😀').length)
    expect(result?.exact_hash).toMatch(/^[a-f0-9]{64}$/)
  })
  it('maps nested list text and markdown marks without treating PM as bytes', async () => {
    const source = '- 一\n  - **中文** tail'
    const { editor, canonical } = setup(source)
    let position = 0
    editor.state.doc.descendants((node, pos) => {
      if (node.isText && node.text === '中文') position = pos
    })
    const result = await validateDocSelection(select(editor, position, position + 2), canonical, source, 4, '')
    expect(result?.start).toBe(new TextEncoder().encode('- 一\n  - **').length)
    expect(result?.end).toBe(new TextEncoder().encode('- 一\n  - **中文').length)
  })
  it('accounts for the omitted duplicate title and rejects split surrogate boundaries', async () => {
    const prefix = '# Title\r\n\r\n'
    const source = `${prefix}😀中`
    const { editor, canonical } = setup(source, prefix)
    expect((await validateDocSelection(select(editor, 1, 3), canonical, source, 4, prefix))?.start).toBe(
      new TextEncoder().encode(prefix).length
    )
    expect(await validateDocSelection(select(editor, 1, 2), canonical, source, 4, prefix)).toBeNull()
  })
  it('rejects stale source, stale PM, cross-node and unsupported HTML', async () => {
    const source = 'one\n\ntwo'
    const { editor, canonical } = setup(source)
    const selection = select(editor, 1, 4)
    expect(await validateDocSelection(selection, canonical, source, 3, '')).toBeNull()
    expect(await validateDocSelection(selection, { ...canonical, source: 'changed' }, source, 4, '')).toBeNull()
    editor.commands.insertContent('new')
    expect(await validateDocSelection(selection, canonical, source, 4, '')).toBeNull()
    const html = setup('<b>hello</b>')
    expect(
      await validateDocSelection(select(html.editor, 1, 3), html.canonical, html.canonical.source, 4, '')
    ).toBeNull()
  })
})

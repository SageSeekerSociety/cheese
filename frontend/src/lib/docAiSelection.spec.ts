import { webcrypto } from 'node:crypto'

import { Editor } from '@tiptap/core'
import { TextSelection } from '@tiptap/pm/state'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { captureDocSelection, validateDocSelection } from './docAiSelection'
import { docExtensions } from './docSchema'

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

function prosePositions(editor: Editor) {
  const positions: number[] = []
  editor.state.doc.forEach((node, pos) => {
    if (node.type.name === 'paragraph' && node.content.size) positions.push(pos + 1)
  })
  return positions
}

function mixedSource(newline = '\n', fence = '```') {
  const repeated = '重复😀文字'
  const before =
    [
      repeated,
      '| A | B |\n| --- | --- |\n| 重复😀文字 | cell |',
      `${fence}python\n重复😀文字\nprint("| not a table |")\n${fence}`,
    ].join('\n\n') + '\n\n'
  return { before: before.replaceAll('\n', newline), repeated }
}

async function exactSelection(before: string, selected: string, nodeId = 'node') {
  const encoder = new TextEncoder()
  const digest = await webcrypto.subtle.digest('SHA-256', encoder.encode(selected))
  return {
    node_id: nodeId,
    start: encoder.encode(before).length,
    end: encoder.encode(before + selected).length,
    exact_hash: Array.from(new Uint8Array(digest), (value) => value.toString(16).padStart(2, '0')).join(''),
  }
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

  it.each(['\n', '\r\n', '\r'])('maps repeated prose around tables and code with %j line endings', async (newline) => {
    const { before, repeated } = mixedSource(newline)
    const source = before + repeated
    const { editor, canonical } = setup(source)
    const [first, last] = prosePositions(editor)
    expect(await validateDocSelection(select(editor, first, first + 4), canonical, source, 4, '')).toEqual(
      await exactSelection('', '重复😀')
    )
    expect(await validateDocSelection(select(editor, last, last + 4), canonical, source, 4, '')).toEqual(
      await exactSelection(before, '重复😀')
    )
  })

  it('keeps the omitted title, tilde fences, marks and the actual canonical node span', async () => {
    const prefix = '# 重复标题😀\r\n\r\n'
    const { before } = mixedSource('\r\n', '~~~')
    const target = '**中文😀** 与 &amp; 及 \\*符号'
    const source = prefix + before + target
    const { editor, canonical } = setup(source, prefix)
    const start = new TextEncoder().encode(prefix + before).length
    canonical.nodes = [
      { node_id: 'earlier', start: 0, end: start },
      { node_id: 'last-prose', start, end: new TextEncoder().encode(source).length },
    ]
    const last = prosePositions(editor).at(-1)!
    expect(await validateDocSelection(select(editor, last, last + 4), canonical, source, 4, prefix)).toEqual(
      await exactSelection(prefix + before + '**', '中文😀', 'last-prose')
    )
    expect(await validateDocSelection(select(editor, last, last + 3), canonical, source, 4, prefix)).toBeNull()
    canonical.nodes[1].end = start + 3
    expect(await validateDocSelection(select(editor, last, last + 4), canonical, source, 4, prefix)).toBeNull()
  })

  it('does not authorize cells, code contents or a selection across opaque blocks', async () => {
    const { before, repeated } = mixedSource()
    const source = before + repeated
    const { editor, canonical } = setup(source)
    const [first, last] = prosePositions(editor)
    const opaque: number[] = []
    editor.state.doc.descendants((node, pos, parent) => {
      if (node.type.name === 'codeBlock') opaque.push(pos + 1)
      if (node.type.name === 'paragraph' && parent?.type.name === 'tableCell') opaque.push(pos + 1)
    })
    expect(opaque.length).toBeGreaterThan(0)
    for (const position of opaque) {
      expect(await validateDocSelection(select(editor, position, position + 1), canonical, source, 4, '')).toBeNull()
    }
    expect(await validateDocSelection(select(editor, first, last + 4), canonical, source, 4, '')).toBeNull()
  })

  it('hashes the original entity and escape bytes in mixed prose', async () => {
    const { before } = mixedSource('\r\n')
    const target = '**中文😀** 与 &amp; 及 \\*符号'
    const source = before + target
    const { editor, canonical } = setup(source)
    const last = prosePositions(editor).at(-1)!
    const rendered = editor.state.doc.lastChild!.textContent
    expect(rendered).toBe('中文😀 与 & 及 *符号')
    const entity = last + rendered.indexOf('&')
    expect(await validateDocSelection(select(editor, entity, entity + 1), canonical, source, 4, '')).toEqual(
      await exactSelection(before + '**中文😀** 与 ', '&amp;')
    )
    const escape = last + rendered.indexOf('*')
    expect(await validateDocSelection(select(editor, escape, escape + 1), canonical, source, 4, '')).toEqual(
      await exactSelection(before + '**中文😀** 与 &amp; 及 ', '\\*')
    )
  })

  it('rejects a document edited while mixed selection hashing is pending', async () => {
    const { before, repeated } = mixedSource()
    const source = before + repeated
    const { editor, canonical } = setup(source)
    const last = prosePositions(editor).at(-1)!
    let releaseDigest!: (value: ArrayBuffer) => void
    const digest = new Promise<ArrayBuffer>((resolve) => {
      releaseDigest = resolve
    })
    vi.stubGlobal('crypto', { subtle: { digest: () => digest } })
    const pending = validateDocSelection(select(editor, last, last + 4), canonical, source, 4, '')
    editor.commands.insertContent('已编辑')
    releaseDigest(await webcrypto.subtle.digest('SHA-256', new TextEncoder().encode('重复😀')))
    expect(await pending).toBeNull()
  })

  it.each(['![image](uploads/a.png)', '<b>unproven</b>', '[ref]: https://example.com\n\n[ref]', '&nbsp;'])(
    'does not shift provenance past an unproven sibling: %s',
    async (sibling) => {
      const source = `before\n\n${sibling}\n\n\`\`\`\nx\n\`\`\`\n\nafter`
      const { editor, canonical } = setup(source)
      const last = prosePositions(editor).at(-1)!
      expect(await validateDocSelection(select(editor, last, last + 5), canonical, source, 4, '')).toBeNull()
    }
  )

  it('does not misalign implicit empty paragraphs or a terminal table caret', async () => {
    const { before, repeated } = mixedSource()
    const extraBlank = before.replace('\n\n', '\n\n\n\n') + repeated
    const empty = setup(extraBlank)
    const last = prosePositions(empty.editor).at(-1)!
    expect(
      await validateDocSelection(select(empty.editor, last, last + 4), empty.canonical, extraBlank, 4, '')
    ).toBeNull()
    const tableSource = 'prose😀\n\n| A | B |\n| --- | --- |\n| 1 | 2 |'
    const table = setup(tableSource)
    expect(await validateDocSelection(select(table.editor, 1, 6), table.canonical, tableSource, 4, '')).toEqual(
      await exactSelection('', 'prose')
    )
  })
})

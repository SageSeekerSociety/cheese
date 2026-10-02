// @vitest-environment jsdom
// A selection rewrite sends the Markdown of the blocks holding the selection and
// where the selection sits in them. The service replaces exactly that range, so
// replacing it must give the document with exactly the selected text replaced
// — and a selection that has no such range is not sent at all.
import { transformToSuggestionTransaction } from '@handlewithcare/prosemirror-suggest-changes'
import { Editor } from '@tiptap/core'
import { afterEach, describe, expect, it } from 'vitest'
import * as Y from 'yjs'

import { flatText, occurrences, rangeOf } from './docEdits'
import { rewriteTarget } from './docRewrite'
import { docExtensions, exportMarkdown, finishMarkdown, parseMarkdown, suggestionId, writeMarkdown } from './docSchema'

const editors: Editor[] = []
afterEach(() => editors.splice(0).forEach((editor) => editor.destroy()))

function open(markdown: string) {
  const editor = new Editor({ extensions: docExtensions(), content: parseMarkdown(markdown).toJSON() })
  editors.push(editor)
  return editor
}

/** The positions of the `nth` occurrence of `text` as it reads. */
function find(editor: Editor, text: string, nth = 0): { from: number; to: number } {
  const flat = flatText(editor.state.doc)
  const hits = occurrences(flat.text, text)
  expect(hits.length).toBeGreaterThan(nth)
  return rangeOf(flat, hits[nth], hits[nth] + text.length)
}

/** What the document reads after the service replaces the range with `replacement`. */
function serviceApplies(markdown: string, target: { block: string; start: number; end: number }, replacement: string) {
  const at = markdown.indexOf(target.block)
  expect(at, 'the block is found in the document as stored').toBeGreaterThanOrEqual(0)
  expect(markdown.indexOf(target.block, at + 1), 'and only once').toBe(-1)
  const block = target.block.slice(0, target.start) + replacement + target.block.slice(target.end)
  return parseMarkdown(markdown.slice(0, at) + block + markdown.slice(at + target.block.length))
}

function stored(editor: Editor) {
  const doc = new Y.Doc()
  writeMarkdown(doc, finishMarkdown(editor.markdown!.serialize(editor.state.doc.toJSON())))
  return exportMarkdown(doc)
}

describe('a selection rewrite', () => {
  it.each([
    ['plain text next to formatting', '数据量到**一千万行**时开始评估迁移。\n\n第二段。', '开始评估', 0],
    ['text that contains formatting', '数据量到**一千万行**时开始评估迁移。', '到一千万行时', 0],
    ['text the serializer escapes', '价格写成 a*b 和 [备注] 两种。', 'a*b 和 [备注]', 0],
    ['the second of two equal phrases', '先评估，再评估。', '评估', 1],
    ['an item in a list', '- 第一项\n- 第二项要改\n- 第三项', '第二项要改', 0],
    ['a heading', '# 存储选型\n\n正文', '存储选型', 0],
    ['text across two paragraphs', '第一段。\n\n第二段。\n\n第三段。', '一段。\n第二', 0],
    ['text from a heading into the paragraph under it', '# 存储选型\n\n正文', '选型\n正', 0],
  ])('replaces exactly the selected text: %s', (_case, markdown, selected, nth) => {
    const editor = open(markdown)
    const { from, to } = find(editor, selected, nth)
    const target = rewriteTarget(editor.state, from, to)
    expect(target).not.toBeNull()

    const expected = editor.state.tr.insertText('新的字', from, to).doc
    expect(serviceApplies(stored(editor), target!, '新的字').toJSON()).toEqual(expected.toJSON())
  })

  it('is not sent when the selection starts inside formatting and ends outside it', () => {
    const editor = open('数据量到**一千万行**时开始评估迁移。')
    const start = find(editor, '万行').from
    const end = find(editor, '时开始').to
    expect(rewriteTarget(editor.state, start, end)).toBeNull()
  })

  it('names the block as stored, with a pending suggestion in it left out', () => {
    const editor = open('数据量到一千万行时开始评估迁移。')
    const at = find(editor, '一千万')
    const tr = editor.state.tr.insertText('五百万', at.from, at.to)
    editor.view.dispatch(transformToSuggestionTransaction(tr, editor.state, () => suggestionId('cheese')))

    const target = rewriteTarget(editor.state, find(editor, '开始评估').from, find(editor, '开始评估').to)
    expect(target?.block).toBe('数据量到一千万行时开始评估迁移。')
    expect(target && target.block.slice(target.start, target.end)).toBe('开始评估')
  })
})

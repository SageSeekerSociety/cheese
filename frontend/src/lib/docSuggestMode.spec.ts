// 建议模式：人打的字记成自己名下的修订，删掉的字划掉而不是消失；文档的正文（拒绝
// 所有修订之后读到的）不变。撤销把刚才那条修订撤掉，而不是又记一条。整段的增删、改
// 段落类型也记成修订；表格、时间线、数据卡里增删行或项记不成修订，不改文档并说一声。
// 关掉之后照常改。
import { Editor } from '@tiptap/core'
import Collaboration from '@tiptap/extension-collaboration'
import { afterEach, describe, expect, it } from 'vitest'
import * as Y from 'yjs'

import { docExtensions, exportMarkdown, liveSuggestions, writeMarkdown } from './docSchema'
import { createSuggestMode, setSuggesting } from './docSuggestMode'

const editors: Editor[] = []
afterEach(() => editors.splice(0).forEach((editor) => editor.destroy()))

function open(markdown: string, author = 'mentor', onRefused?: () => void) {
  const doc = new Y.Doc()
  writeMarkdown(doc, markdown)
  const text = exportMarkdown(doc)
  const editor = new Editor({
    extensions: [
      ...docExtensions(),
      Collaboration.configure({ document: doc }),
      createSuggestMode({ author: () => author, onRefused }),
    ],
  })
  editors.push(editor)
  return { doc, editor, text }
}

const endOfFirstLine = (editor: Editor) => 1 + (editor.state.doc.firstChild?.content.size ?? 0)
/** What typing does: text inserted at the caret. */
function typeAtEnd(editor: Editor, text: string) {
  editor.view.dispatch(editor.state.tr.insertText(text, endOfFirstLine(editor)))
}

describe('建议模式', () => {
  it('打的字记成自己名下的修订，正文不变', () => {
    const { doc, editor, text } = open('第一周领工位。\n')
    setSuggesting(editor, true)
    typeAtEnd(editor, '再开通账号。')

    expect(exportMarkdown(doc)).toBe(text)
    const pending = liveSuggestions(doc)
    expect(pending).toHaveLength(1)
    expect(pending[0]).toMatchObject({ author: 'mentor', new: '再开通账号。' })
  })

  it('删掉的字划掉，不从正文里消失', () => {
    const { doc, editor, text } = open('第一周领工位。\n')
    setSuggesting(editor, true)
    editor.chain().setTextSelection({ from: 4, to: 7 }).deleteSelection().run()

    expect(exportMarkdown(doc)).toBe(text)
    expect(liveSuggestions(doc)[0]).toMatchObject({ author: 'mentor', old: '领工位' })
  })

  it('撤销把刚才那条修订撤掉', () => {
    const { doc, editor, text } = open('第一周领工位。\n')
    setSuggesting(editor, true)
    typeAtEnd(editor, '再开通账号。')
    editor.commands.undo()

    expect(liveSuggestions(doc)).toHaveLength(0)
    expect(exportMarkdown(doc)).toBe(text)
  })

  it('另起一段也记成修订，正文不变', () => {
    const { doc, editor, text } = open('第一周领工位。\n')
    setSuggesting(editor, true)
    editor.chain().setTextSelection(3).splitBlock().run()

    expect(exportMarkdown(doc)).toBe(text)
    expect(liveSuggestions(doc).map((item) => item.author)).toEqual(['mentor'])
  })

  it('整段贴进来也记成自己名下的修订，正文不变', () => {
    let refused = 0
    const { doc, editor, text } = open('第一周领工位。\n', 'mentor', () => refused++)
    setSuggesting(editor, true)
    editor.commands.insertContentAt(editor.state.doc.content.size, '<p>新的一段</p><p>又一段</p>')

    expect(refused).toBe(0)
    expect(exportMarkdown(doc)).toBe(text)
    expect(liveSuggestions(doc)).toEqual([expect.objectContaining({ author: 'mentor', new: '新的一段\n\n又一段' })])
  })

  it('把段落改成标题也记成修订，正文不变', () => {
    const { doc, editor, text } = open('第一周领工位。\n')
    setSuggesting(editor, true)
    editor.chain().setTextSelection(2).setNode('heading', { level: 2 }).run()

    expect(exportMarkdown(doc)).toBe(text)
    expect(liveSuggestions(doc)).toEqual([expect.objectContaining({ author: 'mentor', new: '## 第一周领工位。' })])
  })

  it('表格里加一行记不成修订：不动文档，并说一声', () => {
    let refused = 0
    const { doc, editor, text } = open('| 周 | 事项 |\n| --- | --- |\n| 一 | 领工位 |\n', 'mentor', () => refused++)
    setSuggesting(editor, true)
    let cell = 0
    editor.state.doc.descendants((node, pos) => {
      if (!cell && node.isText && node.text === '领工位') cell = pos
    })
    editor.chain().setTextSelection(cell).addRowAfter().run()

    expect(refused).toBe(1)
    expect(exportMarkdown(doc)).toBe(text)
    expect(liveSuggestions(doc)).toHaveLength(0)
  })

  it('关掉以后直接改正文', () => {
    const { doc, editor, text } = open('第一周领工位。\n')
    setSuggesting(editor, true)
    setSuggesting(editor, false)
    typeAtEnd(editor, '再开通账号。')

    expect(liveSuggestions(doc)).toHaveLength(0)
    expect(exportMarkdown(doc)).toContain('再开通账号。')
  })
})

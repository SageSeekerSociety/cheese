// @vitest-environment jsdom
// 大纲的抽取：从一份真的文档里按出现顺序取出 h1–h3，带上级别、文字和位置。
import { Editor } from '@tiptap/core'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'

import { extractOutline } from './docOutline'
import { docExtensions } from './docSchema'

let editor: Editor
beforeAll(() => {
  editor = new Editor({ element: document.createElement('div'), extensions: docExtensions(), content: '' })
})
afterAll(() => editor.destroy())

function outlineOf(markdown: string) {
  editor.commands.setContent(markdown, { contentType: 'markdown' })
  return extractOutline(editor.state.doc)
}

describe('大纲的抽取', () => {
  it('按出现顺序取出标题，带级别与文字', () => {
    const headings = outlineOf('# 章程\n\n开头一段话。\n\n## 第一章\n\n内容。\n\n### 第一节\n\n更细的内容。')
    expect(headings.map((h) => [h.level, h.text])).toEqual([
      [1, '章程'],
      [2, '第一章'],
      [3, '第一节'],
    ])
  })

  it('位置指向标题节点，滚过去能拿到那一段', () => {
    const headings = outlineOf('# 甲\n\n正文\n\n## 乙')
    for (const heading of headings) {
      expect(editor.state.doc.nodeAt(heading.pos)?.type.name).toBe('heading')
    }
    // 位置随文档自增（第二个标题在第一个之后的外面）。
    expect(headings[1].pos).toBeGreaterThan(headings[0].pos)
  })

  it('空标题不入列', () => {
    const headings = outlineOf('# 有字\n\n##\n\n### 也有字')
    expect(headings.map((h) => h.text)).toEqual(['有字', '也有字'])
  })

  it('超过三级的标题不入列（编辑器只提供 h1–h3）', () => {
    const headings = outlineOf('# 一\n\n#### 太深了\n\n## 二')
    expect(headings.map((h) => h.text)).toEqual(['一', '二'])
  })

  it('没有标题的文档是空数组', () => {
    expect(outlineOf('只有一段普通的话，没有标题。')).toEqual([])
  })
})

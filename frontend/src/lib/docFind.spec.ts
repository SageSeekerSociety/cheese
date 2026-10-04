// @vitest-environment jsdom
// 文档内查找：匹配的位置对不对，以及「第几处 / 共几处」的下标推进。
import { Editor } from '@tiptap/core'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'

import { clampIndex, findMatches, stepIndex } from './docFind'
import { docExtensions } from './docSchema'

let editor: Editor
beforeAll(() => {
  editor = new Editor({ element: document.createElement('div'), extensions: docExtensions(), content: '' })
})
afterAll(() => editor.destroy())

function matchesOf(markdown: string, query: string) {
  editor.commands.setContent(markdown, { contentType: 'markdown' })
  return findMatches(editor.state.doc, query)
}

/** 每处匹配在文档里的原文（用来核对位置没错位）。 */
function matchedTexts(markdown: string, query: string): string[] {
  const matches = matchesOf(markdown, query)
  return matches.map((m) => editor.state.doc.textBetween(m.from, m.to))
}

describe('文档内查找的匹配', () => {
  it('找出所有出现处，按顺序', () => {
    expect(matchedTexts('苹果 香蕉 苹果 苹果', '苹果')).toEqual(['苹果', '苹果', '苹果'])
  })

  it('不区分大小写', () => {
    expect(matchedTexts('Deploy deploy DEPLOY', 'deploy')).toEqual(['Deploy', 'deploy', 'DEPLOY'])
  })

  it('同一处不重复匹配（不重叠）', () => {
    expect(matchesOf('aaaa', 'aa')).toHaveLength(2)
  })

  it('一次匹配里跨标记也连读（一段里一部分加粗）', () => {
    // 「看」是普通字、「这里重点」加粗：查询横跨这条分界也要匹配到。
    expect(matchedTexts('看**这里重点**就好', '看这')).toEqual(['看这'])
    expect(matchedTexts('看**这里重点**就好', '里重点')).toEqual(['里重点'])
  })

  it('不跨段落匹配', () => {
    expect(matchesOf('苹果\n\n香蕉', '果香')).toHaveLength(0)
  })

  it('空查询没有匹配', () => {
    expect(matchesOf('随便什么', '')).toEqual([])
  })

  it('没有出现就是空', () => {
    expect(matchesOf('苹果 香蕉', '橘子')).toEqual([])
  })
})

describe('当前下标的推进', () => {
  it('下一步到头绕回开头', () => {
    expect(stepIndex(0, 3, 1)).toBe(1)
    expect(stepIndex(2, 3, 1)).toBe(0)
  })

  it('上一步从开头绕回末尾', () => {
    expect(stepIndex(0, 3, -1)).toBe(2)
    expect(stepIndex(1, 3, -1)).toBe(0)
  })

  it('没有匹配时停在 0', () => {
    expect(stepIndex(0, 0, 1)).toBe(0)
    expect(stepIndex(5, 0, -1)).toBe(0)
  })

  it('把越界的下标夹回范围内', () => {
    expect(clampIndex(7, 3)).toBe(2)
    expect(clampIndex(-1, 3)).toBe(0)
    expect(clampIndex(2, 0)).toBe(0)
  })
})

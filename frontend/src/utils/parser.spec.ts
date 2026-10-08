// @vitest-environment jsdom
//
// 必须在 jsdom 下跑，不能用仓库默认的 happy-dom：DOMPurify 3.2.6 在 happy-dom 里
// 不可靠 —— 输入里只要带 `<script>` 就整段返回空串，只含 MathML 的输入也被清空，
// 于是「危险内容没了」的断言会假绿。jsdom 下它按真实浏览器的行为工作。
import { describe, expect, it } from 'vitest'

import { parse } from './parser'

// 题目和回答的正文是服务端原样存下的 Editor.js JSON，发问、答题的人可以往里塞任意
// HTML。`parse()` 出口过一道 DOMPurify，这里守住「危险内容清掉、正常内容留下」。
const doc = (blocks: unknown[]) => ({ time: Date.now(), blocks, version: '2.28.2' })

// 字段照 codecup 存下的形状：两个开关默认都是 true（见 editorjs-codecup 的 save()）。
const codeBlock = (code: string) => ({
  id: 'code-1',
  type: 'code',
  data: { code, language: 'html', showlinenumbers: true, showCopyButton: true },
})

describe('parse 出口的白名单', () => {
  it('清掉 code 块里的事件属性，代码块结构还在', () => {
    // 纯 `<img>` 的 textContent 为空，Prism 在 `if (!env.code)` 早退、不覆写 innerHTML，
    // 光靠高亮挡不住这一段，所以拿它当载荷。
    const html = parse(doc([codeBlock('<img src=x onerror=alert(1)>')]))

    expect(html).not.toMatch(/on\w+=/)
    expect(html).not.toContain('alert(1)')
    // 代码块本身照常渲染：Prism 只重排了外层，没动被当成 HTML 插进来的那段内容。
    expect(html).toContain('language-html')
  })

  it('清掉 nestedList 的 item.content 里的事件属性，列表结构保留', () => {
    const html = parse(
      doc([
        {
          id: 'list-1',
          type: 'nestedList',
          data: { style: 'unordered', items: [{ content: '<img src=x onerror=alert(1)>', items: [] }] },
        },
      ])
    )

    expect(html).not.toMatch(/on\w+=/)
    expect(html).toContain('cdx-nested-list')
  })

  it('整段去掉 script 标签，正文还在', () => {
    const html = parse(doc([{ id: 'p-1', type: 'paragraph', data: { text: '<script>alert(1)</script>正文' } }]))

    expect(html).not.toContain('<script')
    expect(html).toContain('正文')
  })

  it('留下 math 块的 KaTeX 产物', () => {
    const html = parse(doc([{ id: 'math-1', type: 'math', data: { math: 'x^2' } }]))

    expect(html).toContain('katex')
    expect(html).toContain('x^2')
  })
})

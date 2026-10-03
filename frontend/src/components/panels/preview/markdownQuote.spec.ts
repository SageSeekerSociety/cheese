/**
 * 这条出口不存锚点，报出去的就是「哪一节 + 原文 + 两侧各 32 字」，所以判据全在这三样
 * 上：标题说的是最近的那一节（不是全文第一个标题），两侧取的是**渲染出来的**文字
 * （选区在 DOM 里，不在源码里），指不成的一律返回 null。
 *
 * 不从这里点面板：面板那条路要过事件层，而这里每一问都能直接把选区做出来。
 */
import { afterEach, expect, it } from 'vitest'

import { CONTEXT_CHARS, headingPath, quoteFromSelection } from './markdownQuote'

afterEach(() => {
  window.getSelection()?.removeAllRanges()
  document.body.innerHTML = ''
})

function rootFrom(html: string): HTMLElement {
  const root = document.createElement('div')
  root.innerHTML = html
  document.body.append(root)
  return root
}

function select(node: Node, start: number, end: number): Selection {
  const range = document.createRange()
  range.setStart(node, start)
  range.setEnd(node, end)
  const selection = window.getSelection()!
  selection.removeAllRanges()
  selection.addRange(range)
  return selection
}

it('报出选中的原文、它所在那一节的标题路径，以及两侧的文字', () => {
  const root = rootFrom('<h1>配置</h1><h2>重试</h2><p>失败以后重试 3 次，间隔一秒。</p>')
  const node = root.querySelector('p')!.firstChild!
  // 失败以后|重试 3 次|，间隔一秒。
  const quote = quoteFromSelection(root, select(node, 4, 10))!
  expect(quote.text).toBe('重试 3 次')
  expect(quote.heading).toBe('配置 › 重试')
  expect(quote.prefix.endsWith('失败以后')).toBe(true)
  expect(quote.suffix.startsWith('，间隔一秒')).toBe(true)
})

it('同级标题关掉比它深的那几节，路径跟着走', () => {
  const root = rootFrom('<h1>配置</h1><h2>重试</h2><h3>间隔</h3><p>第一段。</p><h2>回退</h2><p>第二段。</p>')
  expect(headingPath(root, root.querySelectorAll('p')[0]!.firstChild!)).toBe('配置 › 重试 › 间隔')
  expect(headingPath(root, root.querySelectorAll('p')[1]!.firstChild!)).toBe('配置 › 回退')
})

it('第一个标题之前的那一段没有节名', () => {
  const root = rootFrom('<p>开头的一段话。</p><h2>后面</h2>')
  expect(headingPath(root, root.querySelector('p')!.firstChild!)).toBe('')
})

it('两侧各取 32 个字，取的是渲染出来的那一段文字', () => {
  const body = '一二三四五六七八九十'.repeat(8)
  const root = rootFrom(`<p>${body}中间这句${body}</p>`)
  const quote = quoteFromSelection(root, select(root.querySelector('p')!.firstChild!, 80, 84))!
  expect(quote.text).toBe('中间这句')
  expect(quote.prefix).toHaveLength(CONTEXT_CHARS)
  expect(quote.suffix).toHaveLength(CONTEXT_CHARS)
  expect(quote.prefix.endsWith('九十')).toBe(true)
  expect(quote.suffix.startsWith('一二三')).toBe(true)
})

it('文件开头的那一段，前面没有字就是空的', () => {
  const root = rootFrom('<p>开头的一段话。</p>')
  const quote = quoteFromSelection(root, select(root.querySelector('p')!.firstChild!, 0, 6))!
  expect(quote.text).toBe('开头的一段话')
  expect(quote.prefix).toBe('')
  expect(quote.heading).toBe('')
})

it('指不成的一律不出去：太短、没选、或者选到了这块正文以外', () => {
  const root = rootFrom('<p>失败以后重试 3 次。</p>')
  const node = root.querySelector('p')!.firstChild!
  // 一个字过不了「至少两个字」那道坎——和幻灯片、分页文档用的是同一道。
  expect(quoteFromSelection(root, select(node, 0, 1))).toBeNull()
  // 光标落在一处（没选任何东西）。
  expect(quoteFromSelection(root, select(node, 3, 3))).toBeNull()
  // 选中的是别处的东西：这块正文里没有它，报出去的位置就是错的。
  const other = rootFrom('<p>另一个组件里的文字。</p>')
  expect(quoteFromSelection(root, select(other.querySelector('p')!.firstChild!, 0, 5))).toBeNull()
  // 没有选区（鼠标刚点下来）也返回 null。
  expect(quoteFromSelection(root, null)).toBeNull()
  // 正对照：同一块正文里选中同样长的一段，照样出得去——免得上面几条只是「什么都没发生」。
  expect(quoteFromSelection(root, select(node, 0, 4))!.text).toBe('失败以后')
})

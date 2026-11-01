/**
 * 选区跨过好几段文字时，底色画不画得出来。
 *
 * 拖选越过一段文字的边界（从这一行拖到下一行、或者往上多拖一点），浏览器给回来的那一头
 * 常常是**元素**——包着那几段文字的容器，偏移量是「第几个孩子」，不是某个文字节点上的
 * 第几个字。求每段文字和选区的交集时要是拿 `compareDocumentPosition` 比「谁在前」，容器
 * 里的每一段都会答「排在终点之后」，于是一段也收不到、橙色整个灭掉：读者看到的就是
 * 「选中往上多拖一点，高亮没了」。这里用 `Range.comparePoint` 逐段求交，把两头是元素的
 * 情形也钉住。
 *
 * 把 `rectsIn` 换回按文档顺序比较，前两条会红（一条框也收不到）。
 */
import { beforeAll, beforeEach, expect, it } from 'vitest'

import { rectsIn } from './selectionHighlight'

/** 每一段文字的框：happy-dom 没有排版，给一个固定的。 */
const RECT = { left: 100, top: 50, width: 100, height: 12, right: 200, bottom: 62 } as DOMRect

beforeAll(() => {
  Range.prototype.getClientRects = function () {
    return [RECT] as unknown as DOMRectList
  }
})
beforeEach(() => {
  document.body.innerHTML = ''
  window.getSelection()!.removeAllRanges()
})

/** 一个文字层，三段文字用 `<br>` 隔开（文字节点是层的直接孩子：happy-dom 的 TreeWalker
 *  不下钻到 span 里，真产品里 pdf.js 杀的 span 在这里走不到）。 */
function makeLayer() {
  const layer = document.createElement('div')
  layer.className = 'pv-text'
  layer.append('第一段文字', document.createElement('br'), '第二段文字', document.createElement('br'), '第三段文字')
  document.body.append(layer)
  const nodes = [...layer.childNodes].filter((node): node is Text => node.nodeType === Node.TEXT_NODE)
  return { layer, nodes }
}

it('终点落在文字层这一层元素上（拖过一整段）：两段都收得到', () => {
  const { layer, nodes } = makeLayer()
  const range = document.createRange()
  range.setStart(nodes[0], 0)
  // 终点是「层里第 3 个孩子之前」——第一、二段文字都在选区里。
  range.setEnd(layer, 3)
  expect(range.endContainer).toBe(layer)

  expect(rectsIn(range, layer)).toHaveLength(2)
})

it('起点落在文字层这一层元素上（往上多拖一点）：起点之前的那一段不收', () => {
  const { layer, nodes } = makeLayer()
  const range = document.createRange()
  // 起点是「层里第 3 个孩子之前」——第二段文字之后；终点在第二段文字里面。
  // 只该收第二段，层里第一段在起点之前。
  range.setStart(layer, 2)
  range.setEnd(nodes[1], 3)
  expect(range.startContainer).toBe(layer)

  expect(rectsIn(range, layer)).toHaveLength(1)
})

it('两头都是文字节点时照旧只收选中的那一段', () => {
  const { layer, nodes } = makeLayer()
  const range = document.createRange()
  range.setStart(nodes[1], 0)
  range.setEnd(nodes[1], 3)

  expect(rectsIn(range, layer)).toHaveLength(1)
})

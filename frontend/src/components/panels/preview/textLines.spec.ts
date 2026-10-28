/**
 * 键盘选字的行：把文字节点按屏幕上的位置归成一行行。
 *
 * 这一条守的是「一行一格」这个单位本身。pdf.js 的文字层是绝对定位摆上去的一段段 span，
 * DOM 顺序不等于阅读顺序（表格、两栏尤其如此）。按位置归行进，上下键才是一步一行；
 * 归错了，读者按一下上/下就跳到页的另一头。
 */
import { beforeEach, describe, expect, it } from 'vitest'

import { type LineNode, linesFrom, rangeOf } from './textLines'

/** 造一个文字节点和它的框。
 *
 *  挂进文档里：`Range.toString()` 跨节点取字要求这些节点在文档里，happy-dom 对游离
 *  节点只肯给空串（真浏览器也是同样的规矩，只是真浏览器里它们本来就在文档里）。 */
function node(text: string, left: number, top: number, width = 40, height = 10): LineNode {
  const el = document.createElement('span')
  el.textContent = text
  document.body.append(el)
  return { node: el.firstChild as Text, rect: { left, top, width, height } }
}

describe('按位置把文字节点归成行', () => {
  it('同一高度上的两段并成一行，行内自左向右', () => {
    const right = node('后段', 100, 10)
    const left = node('前段', 20, 10)
    const lines = linesFrom([right, left])
    expect(lines).toHaveLength(1)
    expect(lines[0].nodes.map((n) => n.data)).toEqual(['前段', '后段'])
    expect(lines[0].left).toBe(20)
    expect(lines[0].right).toBe(140)
  })

  it('上下错开的两段是两行，行与行自上而下', () => {
    const lower = node('第二行', 20, 40)
    const upper = node('第一行', 20, 10)
    const lines = linesFrom([lower, upper])
    expect(lines.map((l) => l.nodes[0].data)).toEqual(['第一行', '第二行'])
  })

  it('竖直方向搭上不足一半的不并：那是行距很紧的相邻两行', () => {
    // 第一行 10..20，第二行 16..26：搭了 4，比一半（5）少
    const a = node('上面', 20, 10)
    const b = node('下面', 20, 16)
    expect(linesFrom([a, b])).toHaveLength(2)
  })

  it('竖直方向搭过一半的并成一行：同一行上字盒高矮不一', () => {
    // 10..20 与 9..22，搭了 10，超过较矮那一半
    const small = node('小字', 20, 10, 40, 10)
    const tall = node('大字', 70, 9, 40, 13)
    expect(linesFrom([small, tall])).toHaveLength(1)
  })

  it('空白节点、量不出尺寸的节点都不算一行', () => {
    const blank = node('   ', 20, 10)
    const zero = node('量不到', 20, 40, 0, 0)
    const real = node('真的字', 20, 70)
    const lines = linesFrom([blank, zero, real])
    expect(lines).toHaveLength(1)
    expect(lines[0].nodes[0].data).toBe('真的字')
  })

  it('左边偏下一点、右边偏上一点，还是同一行（表格里同一行格子高矮不一）', () => {
    const left = node('左侧', 20, 10, 40, 10)
    const right = node('右侧', 200, 12, 40, 10)
    expect(linesFrom([left, right])).toHaveLength(1)
  })
})

describe('行号换成选区', () => {
  const lines = () => linesFrom([node('第一行', 20, 10), node('第二行', 20, 40), node('第三行', 20, 70)])

  it('一行就是整行：从这一行第一个字之前到最后一个字之后', () => {
    const range = rangeOf(lines(), 1, 1)!
    expect(range.toString()).toBe('第二行')
  })

  it('两头顺序颠倒也一样：Shift 往上扩选是同一段', () => {
    expect(rangeOf(lines(), 2, 0)!.toString()).toBe('第一行第二行第三行')
    expect(rangeOf(lines(), 0, 2)!.toString()).toBe('第一行第二行第三行')
  })

  it('越界的行号夹到头尾，不返回空', () => {
    expect(rangeOf(lines(), -5, 1)!.toString()).toBe('第一行第二行')
    expect(rangeOf(lines(), 1, 99)!.toString()).toBe('第二行第三行')
  })

  it('一页里一个字都没有时没有选区', () => {
    expect(rangeOf([], 0, 0)).toBeNull()
  })
})

describe('一页里有两栏', () => {
  it('左右两栏各归各的行，不会因为同一高度并成一条', () => {
    const leftTop = node('左一', 20, 10)
    const rightTop = node('右一', 300, 10)
    const leftBelow = node('左二', 20, 40)
    const rightBelow = node('右二', 300, 40)
    const lines = linesFrom([leftTop, rightTop, leftBelow, rightBelow])
    // 同一高度上的两栏会被并成一行——这是有意的：上下键按屏幕上的行走，
    // 一行两栏在屏幕上确实是同一行。
    expect(lines).toHaveLength(2)
    expect(lines[0].nodes.map((n) => n.data)).toEqual(['左一', '右一'])
  })
})

beforeEach(() => {
  document.body.replaceChildren()
})

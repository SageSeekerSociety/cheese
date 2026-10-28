/**
 * 选中底色的那一层。
 *
 * 这一条守三件事：
 *
 * 一、底色要的是**一整块**，所以选区的每一块得收进**同一条** path、只填一次——两块搭着
 * 的地方也只落一层，颜色才和别处一样深。每块各画一个元素、各填一次，搭着的那几个像素
 * 就又叠出深的一块，正好是这层要解决的问题。
 *
 * 二、**一行一条**。pdf.js 的文字层里，同一行相邻两段的盒子高矮不一（9.78、11、11.56、
 * 14 都有），按块原样画出来是一排参差的方块，不是一道荧光笔。同一行上的块要先并成一条，
 * 上下取最高那块、左右把挨着的接上。
 *
 * 三、底色比字盒高一点（`BAND`），上下都盖住字形再留一点边：荧光笔划出来的是「一道」，
 * 贴着字画一个框读起来是「字被框住了」。
 *
 * 另外守两件小事：浏览器在行首行尾给的零尺寸块不该画（画出来是几个像素的黑点）；
 * 一路拖过几页时，另一个 range 上的块会混进来，按块心落在不在这一页上分开。
 */
import type { SelectionRect } from './selectionHighlight'

import { describe, expect, it } from 'vitest'

import { selectionPath } from './selectionHighlight'

/** 画布：视口坐标里 (100, 50) 起、200×300 大的一块纸。 */
const box: SelectionRect = { left: 100, top: 50, width: 200, height: 300 }

function rect(left: number, top: number, width: number, height: number): SelectionRect {
  return { left, top, width, height }
}

/** 一条带子：上下各放 `(h × 0.65) / 2`，所以总高是 `h × 1.65`。 */
function band(left: number, top: number, width: number, height: number): string {
  const pad = (height * 0.65) / 2
  const x = Math.round(left * 100) / 100
  const y = Math.round((top - pad) * 100) / 100
  const h = Math.round(height * 1.65 * 100) / 100
  const w = Math.round(width * 100) / 100
  return `M${x} ${y}h${w}v${h}h${-w}Z`
}

describe('选中底色的那条 path', () => {
  it('一块选区画成一条带，比字盒上下各多一点', () => {
    // 一行 12px 高的字：带子 19.8px 高、上沿比字盒的 10 高 3.9。
    expect(selectionPath([rect(110, 60, 50, 12)], box)).toBe(band(10, 10, 50, 12))
  })

  it('同一行上的两块并成一条：文字层自己给的接缝不留在底色上', () => {
    const one = rect(110, 60, 50, 12)
    const two = rect(160, 60, 30, 12)
    expect(selectionPath([one, two], box)).toBe(band(10, 10, 80, 12))
  })

  it('同一行上隔着一个空格的也接上', () => {
    // 中间空 4px（不到字高的 0.6 倍），接成一条。
    expect(selectionPath([rect(110, 60, 50, 12), rect(164, 60, 30, 12)], box)).toBe(band(10, 10, 84, 12))
  })

  it('同一行上隔得远的两团各留各的：那多半是两栏', () => {
    // 中间空 60px（是字高的五倍），不接。
    expect(selectionPath([rect(110, 60, 50, 12), rect(220, 60, 30, 12)], box)).toBe(
      `${band(10, 10, 50, 12)} ${band(120, 10, 30, 12)}`
    )
  })

  it('不同的行各画一条，不会连成一片', () => {
    expect(selectionPath([rect(110, 60, 50, 12), rect(110, 80, 80, 12)], box)).toBe(
      `${band(10, 10, 50, 12)} ${band(10, 30, 80, 12)}`
    )
  })

  it('同一行里高矮不一的两块，上下取最高的那一块：带子才是直的', () => {
    // 中文段 12 高、英文段 9 高，同一行：一条 12 高的带子，不是两块一高一矮的。
    expect(selectionPath([rect(110, 60, 50, 12), rect(160, 61.5, 30, 9)], box)).toBe(band(10, 10, 80, 12))
  })

  it('坐标是相对画布的，两位小数', () => {
    expect(selectionPath([rect(110.334, 60.666, 50.5, 12)], box)).toBe(band(10.33, 10.67, 50.5, 12))
  })

  it('零尺寸的块不画', () => {
    expect(selectionPath([rect(110, 60, 0, 12)], box)).toBe('')
    expect(selectionPath([rect(110, 60, 50, 0)], box)).toBe('')
  })

  it('块心落在这张纸外面的不画：那多半是拖过几页时另一页的块', () => {
    expect(selectionPath([rect(500, 60, 50, 12)], box)).toBe('')
    expect(selectionPath([rect(110, 400, 50, 12)], box)).toBe('')
    // 混在一起时只留下这一张上的那块。
    expect(selectionPath([rect(110, 60, 50, 12), rect(300, 60, 50, 12)], box)).toBe(band(10, 10, 50, 12))
  })

  it('没选中任何一块时给空串，不是一句空的话', () => {
    expect(selectionPath([], box)).toBe('')
  })

  describe('给了这一页的文字行表时，上下留白收着放', () => {
    it('行距宽时照常放 1.65 倍', () => {
      // 这一行 10–22，上一行到 0、下一行从 32 起：上下各放得下 10 和 10，够 1.65 倍。
      const roomy: [number, number][] = [
        [-10, 0],
        [10, 22],
        [32, 44],
      ]
      expect(selectionPath([rect(110, 60, 50, 12)], box, roomy)).toBe(band(10, 10, 50, 12))
    })

    it('行距紧时只放到不碰隔壁行', () => {
      // 下一行紧贴着从 23 起：下面只放得下 1px，再留 1px 缝就是 0 → 不放。
      const tight: [number, number][] = [
        [10, 22],
        [23, 35],
      ]
      expect(selectionPath([rect(110, 60, 50, 12)], box, tight)).toBe('M10 10h50v12h-50Z')
    })

    it('没给行表时按 1.65 倍放，不受影响', () => {
      expect(selectionPath([rect(110, 60, 50, 12)], box)).toBe(band(10, 10, 50, 12))
    })
  })
})

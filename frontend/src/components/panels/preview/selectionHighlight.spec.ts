/**
 * 选中底色的那一层。
 *
 * 这一条守的是「一块画成一个框、几个框拼起来还是不对」：底色要的是**一整块**，所以
 * 选区的每一块得收进**同一条** path、只填一次——两块搭着的地方也只落一层，颜色才和
 * 别处一样深。每块各画一个元素、各填一次，搭着的那几个像素就又叠出深的一块，正好是
 * 这层要解决的问题。
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

describe('选中底色的那条 path', () => {
  it('每一块画一句、连成一条 path', () => {
    // 两块一起给，用空格连起来——不是两个元素，是一条 path 里两句。
    expect(selectionPath([rect(110, 60, 50, 12), rect(110, 80, 80, 12)], box)).toBe(
      'M10 10h50v12h-50Z M10 30h80v12h-80Z'
    )
  })

  it('坐标是相对画布的，两位小数', () => {
    expect(selectionPath([rect(110.334, 60.666, 50.5, 12.25)], box)).toBe('M10.33 10.67h50.5v12.25h-50.5Z')
  })

  it('零尺寸的块不画', () => {
    expect(selectionPath([rect(110, 60, 0, 12)], box)).toBe('')
    expect(selectionPath([rect(110, 60, 50, 0)], box)).toBe('')
  })

  it('块心落在这张纸外面的不画：那多半是拖过几页时另一页的块', () => {
    expect(selectionPath([rect(500, 60, 50, 12)], box)).toBe('')
    expect(selectionPath([rect(110, 400, 50, 12)], box)).toBe('')
    // 混在一起时只留下这一张上的那块。
    expect(selectionPath([rect(110, 60, 50, 12), rect(300, 60, 50, 12)], box)).toBe('M10 10h50v12h-50Z')
  })

  it('没选中任何一块时给空串，不是一句空的话', () => {
    expect(selectionPath([], box)).toBe('')
  })
})

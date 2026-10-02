import type { RasterRegion } from './designRegion'

import { describe, expect, it } from 'vitest'

import {
  HANDLE_HIT_RADIUS,
  HANDLE_RADIUS,
  handlePoints,
  isSelectableStroke,
  moveRegion,
  nearestHandle,
  resizeRegion,
  SELECT_DASH,
  SELECT_STROKE_WIDTH,
  strokeBox,
} from './designSketchSelection'

const box: RasterRegion = { x: 100, y: 100, width: 200, height: 100 }

describe('选中框的参数', () => {
  it('照参考物：虚线 5,4、把手半径 3.5、抓取半径 48、描边 4', () => {
    expect(SELECT_DASH).toEqual([5, 4])
    expect(HANDLE_RADIUS).toBe(3.5)
    expect(HANDLE_HIT_RADIUS).toBe(48)
    expect(SELECT_STROKE_WIDTH).toBe(4)
  })
})

describe('handles', () => {
  it('八个把手落在四角与四边中点上', () => {
    const points = handlePoints(box)
    expect(points).toHaveLength(8)
    expect(points.find((p) => p.role === 'nw')).toMatchObject({ x: 100, y: 100 })
    expect(points.find((p) => p.role === 'se')).toMatchObject({ x: 300, y: 200 })
    expect(points.find((p) => p.role === 'n')).toMatchObject({ x: 200, y: 100 })
    expect(points.find((p) => p.role === 'w')).toMatchObject({ x: 100, y: 150 })
  })

  it('抓取半径之内才认，之外就当没抓到', () => {
    // 距 nw (100,100) 47 像素：命中。
    expect(nearestHandle(box, { x: 100 + 47, y: 100 })).toBe('nw')
    // 距最近的把手也超过 48：不命中。
    expect(nearestHandle(box, { x: 200, y: 150 })).toBeNull()
  })
})

describe('resizeRegion', () => {
  it('拖 se 角把右下角拉出去，左上角不动', () => {
    expect(resizeRegion(box, 'se', { x: 400, y: 250 })).toEqual({ x: 100, y: 100, width: 300, height: 150 })
  })

  it('拖 w 边只动左边', () => {
    expect(resizeRegion(box, 'w', { x: 150, y: 999 })).toEqual({ x: 150, y: 100, width: 150, height: 100 })
  })

  it('拖过头不翻面：被拖的那条边顶到最小，框不会跳到对面去', () => {
    const squashed = resizeRegion(box, 'e', { x: 50, y: 150 })
    expect(squashed).toEqual({ x: 100, y: 100, width: 2, height: 100 })
  })
})

describe('moveRegion', () => {
  it('整体平移，尺寸不变', () => {
    expect(moveRegion(box, { x: 10, y: 10 }, { x: 40, y: 25 })).toEqual({ x: 130, y: 115, width: 200, height: 100 })
  })
})

describe('能被选中的标注', () => {
  it('块状标注和文字能选，线/箭头/自由笔不能', () => {
    expect(isSelectableStroke({ tool: 'rect', color: '#000', width: 2, region: box })).toBe(true)
    expect(isSelectableStroke({ tool: 'ellipse', color: '#000', width: 2, region: box })).toBe(true)
    expect(isSelectableStroke({ tool: 'redact', color: '#000', width: 2, region: box })).toBe(true)
    expect(isSelectableStroke({ tool: 'text', color: '#000', width: 2, at: { x: 1, y: 2 }, text: 'hi' })).toBe(true)
    expect(isSelectableStroke({ tool: 'pen', color: '#000', width: 2, points: [{ x: 0, y: 0 }] })).toBe(false)
    expect(
      isSelectableStroke({ tool: 'line', color: '#000', width: 2, from: { x: 0, y: 0 }, to: { x: 1, y: 1 } })
    ).toBe(false)
  })

  it('占块的标注取自己的 region；文字在锚点处照字号量一个', () => {
    expect(strokeBox({ tool: 'rect', color: '#000', width: 2, region: box }, 1000)).toEqual(box)
    const text = strokeBox({ tool: 'text', color: '#000', width: 2, at: { x: 50, y: 60 }, text: 'abc' }, 1000)
    expect(text.x).toBe(50)
    expect(text.y).toBe(60)
    expect(text.height).toBeGreaterThan(0)
    expect(text.width).toBeGreaterThan(0)
  })
})

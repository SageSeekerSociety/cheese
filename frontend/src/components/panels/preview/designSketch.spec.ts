import { describe, expect, it } from 'vitest'

import { arrowHead, arrowHeadPoints, SKETCH_COLORS } from './designSketch'

describe('SKETCH_COLORS', () => {
  it('是参考物那一套：红、蓝、绿、近黑、白，第一颗是默认选中的红', () => {
    expect(SKETCH_COLORS).toEqual(['#E03131', '#1971C2', '#2F9E44', '#1F1E1D', '#FFFFFF'])
    expect(SKETCH_COLORS[0]).toBe('#E03131')
  })
})

/**
 * 三点围出的三角形面积。屏幕上的箭头是一块 `<polygon>`：两个点围不出面，
 * 面积为零的「箭头」在浏览器里一个字都不会画出来。
 */
function area(points: { x: number; y: number }[]): number {
  let sum = 0
  for (let i = 0; i < points.length; i += 1) {
    const a = points[i]
    const b = points[(i + 1) % points.length]
    sum += a.x * b.y - b.x * a.y
  }
  return Math.abs(sum) / 2
}

describe('arrowHeadPoints', () => {
  // 回归：这里曾经只返回两翼，屏幕上的箭头于是退化成一条直线（导出那条路
  // 自己补了箭尖，所以只有屏幕上缺）。箭尖必须在返回的这一串里。
  it('returns the tip between the two wings', () => {
    const from = { x: 0, y: 0 }
    const to = { x: 120, y: 40 }
    const points = arrowHeadPoints(from, to, 2)

    expect(points).toHaveLength(3)
    expect(points[1]).toEqual(to)
    const [left, right] = arrowHead(from, to, 2)
    expect(points[0]).toEqual(left)
    expect(points[2]).toEqual(right)
  })

  it('spans a real triangle, not a line', () => {
    const points = arrowHeadPoints({ x: 0, y: 0 }, { x: 100, y: 0 }, 2)
    expect(area(points)).toBeGreaterThan(0)
  })

  it('holds three points even when the drag has no length', () => {
    // 松手前抖一下就会走到这里。退化图形画不出来是正常的，崩掉不是。
    const at = { x: 30, y: 30 }
    expect(arrowHeadPoints(at, at, 2)).toHaveLength(3)
  })
})

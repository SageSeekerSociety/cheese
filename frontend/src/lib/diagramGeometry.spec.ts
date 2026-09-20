import { describe, expect, it } from 'vitest'

import { arrowPath, bezierPoint, connect, curvePath } from './diagramGeometry'

describe('bezierPoint', () => {
  it('两端点就是 p0 和 p3', () => {
    const p0 = { x: 0, y: 0 }
    const p1 = { x: 10, y: 0 }
    const p2 = { x: 90, y: 100 }
    const p3 = { x: 100, y: 100 }
    expect(bezierPoint(p0, p1, p2, p3, 0)).toEqual(p0)
    expect(bezierPoint(p0, p1, p2, p3, 1)).toEqual(p3)
  })

  it('中点落在两端之间的合理位置，不是被某个控制点拽过去', () => {
    // 控制点离得远，但 t=0.5 的权重只有 3/8+3/8，中点应当在两端的中间附近。
    const mid = bezierPoint({ x: 0, y: 0 }, { x: 100, y: 0 }, { x: 0, y: 100 }, { x: 100, y: 100 }, 0.5)
    expect(mid.x).toBeCloseTo(50)
    expect(mid.y).toBeCloseTo(50)
  })

  it('控制点与端点共线时退化成直线', () => {
    const mid = bezierPoint({ x: 0, y: 0 }, { x: 30, y: 0 }, { x: 70, y: 0 }, { x: 100, y: 0 }, 0.5)
    expect(mid).toEqual({ x: 50, y: 0 })
  })
})

describe('curvePath', () => {
  it('输出 SVG 认的三次贝塞尔命令', () => {
    const d = curvePath({ x: 0, y: 1 }, { x: 2, y: 3 }, { x: 4, y: 5 }, { x: 6, y: 7 })
    expect(d).toBe('M 0 1 C 2 3, 4 5, 6 7')
  })
})

describe('arrowPath', () => {
  it('尖端就落在目标点上', () => {
    expect(arrowPath({ x: 0, y: 0 }, { x: 10, y: 0 }).startsWith('M 10 0')).toBe(true)
  })

  it('箭头从右侧指向左时，两个后角一样远', () => {
    const d = arrowPath({ x: 100, y: 0 }, { x: 0, y: 0 })
    const corners = [...d.matchAll(/L ([\d.-]+) ([\d.-]+)/g)].map((m) => ({ x: Number(m[1]), y: Number(m[2]) }))
    expect(corners).toHaveLength(2)
    expect(corners[0].x).toBeCloseTo(8)
    expect(corners[1].x).toBeCloseTo(8)
    // 两个后角朝相反方向张开
    expect(Math.sign(corners[0].y)).toBe(-Math.sign(corners[1].y))
  })

  it('竖直方向也画得出来（atan2 没被写死成 0 或 π）', () => {
    const d = arrowPath({ x: 0, y: 0 }, { x: 0, y: 100 })
    const corners = [...d.matchAll(/L ([\d.-]+) ([\d.-]+)/g)].map((m) => ({ x: Number(m[1]), y: Number(m[2]) }))
    expect(corners).toHaveLength(2)
    for (const corner of corners) expect(corner.x).not.toBe(0)
  })
})

describe('connect', () => {
  const box = (x: number, y: number, w = 100, h = 40) => ({ x, y, w, h })

  it('上下距离更大时走竖边：出点在源盒底边、入点在目标盒顶边', () => {
    const [p0, , , p3] = connect(box(0, 0), box(0, 200))
    expect(p0).toEqual({ x: 50, y: 40 })
    expect(p3).toEqual({ x: 50, y: 200 })
  })

  it('目标在上方时走顶边和底边，方向反过来', () => {
    const [p0, , , p3] = connect(box(0, 200), box(0, 0))
    expect(p0).toEqual({ x: 50, y: 200 })
    expect(p3).toEqual({ x: 50, y: 40 })
  })

  it('左右距离更大时走横边：出点在源盒右边、入点在目标盒左边', () => {
    const [p0, , , p3] = connect(box(0, 0), box(400, 10))
    expect(p0).toEqual({ x: 100, y: 20 })
    expect(p3).toEqual({ x: 400, y: 30 })
  })

  it('目标在左边时出左边、入右边', () => {
    const [p0, , , p3] = connect(box(400, 0), box(0, 0))
    expect(p0).toEqual({ x: 400, y: 20 })
    expect(p3).toEqual({ x: 100, y: 20 })
  })

  it('控制点在出点外侧：这样曲线是绕出去再接上，不是穿过盒子', () => {
    const [p0, p1, p2, p3] = connect(box(0, 0), box(400, 0))
    expect(p1.x).toBeGreaterThan(p0.x)
    expect(p2.x).toBeLessThan(p3.x)
  })

  it('两个盒子挨得近时控制点也留出最小距离，否则线会缩成一条直棍', () => {
    const [p0, p1, p2, p3] = connect(box(0, 0), box(104, 0))
    expect(p1.x - p0.x).toBeGreaterThanOrEqual(24)
    expect(p3.x - p2.x).toBeGreaterThanOrEqual(24)
  })

  it('重合的两个盒子不会算出 NaN（同一点上 div 会返回 0，atan2 也还算得出来）', () => {
    const [, p1, p2, p3] = connect(box(0, 0), box(0, 0))
    for (const point of [p1, p2, p3]) {
      expect(Number.isFinite(point.x)).toBe(true)
      expect(Number.isFinite(point.y)).toBe(true)
    }
  })
})

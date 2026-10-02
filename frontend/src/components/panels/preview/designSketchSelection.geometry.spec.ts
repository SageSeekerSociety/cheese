import type { RasterRegion } from './designRegion'
import type { SketchStroke } from './designSketch'

import { describe, expect, it } from 'vitest'

import {
  boxHandles,
  HANDLE_HIT_RADIUS,
  HANDLE_RADIUS,
  HANDLE_SPLIT,
  HANDLE_STROKE_WIDTH,
  handleGrabRadius,
  isEmptyStroke,
  isSelectableStroke,
  MIN_HANDLE_GRAB,
  moveRegion,
  moveStroke,
  nearestHandle,
  resizeBox,
  resizeRegion,
  SELECT_DASH,
  snapAngle45,
  strokeBox,
  strokeHandles,
  TEXT_BOX_PADDING,
  TEXT_BOX_STROKE_WIDTH,
} from './designSketchSelection'

const box: RasterRegion = { x: 100, y: 100, width: 200, height: 100 }
/** 抓取半径只对可编辑的标注有意义；自由笔不在这套里。 */
type SketchShape = Exclude<SketchStroke, { tool: 'pen' }>

describe('选中框的参数', () => {
  it('照参考物：虚线 5,4、把手半径 3.5、抓取半径 48、把手描边 1、文字框描边 1.5', () => {
    expect(SELECT_DASH).toEqual([5, 4])
    expect(HANDLE_RADIUS).toBe(3.5)
    expect(HANDLE_HIT_RADIUS).toBe(48)
    expect(HANDLE_STROKE_WIDTH).toBe(1)
    expect(TEXT_BOX_STROKE_WIDTH).toBe(1.5)
    expect(TEXT_BOX_PADDING).toBe(4)
    expect(HANDLE_SPLIT).toBe(48)
  })
})

describe('boxHandles（角恒有，48 规则补中点）', () => {
  it('够大的框八个把手都在四角与四边中点上', () => {
    const points = boxHandles(box)
    expect(points).toHaveLength(8)
    expect(points.find((p) => p.role === 'nw')).toMatchObject({ x: 100, y: 100 })
    expect(points.find((p) => p.role === 'se')).toMatchObject({ x: 300, y: 200 })
    expect(points.find((p) => p.role === 'n')).toMatchObject({ x: 200, y: 100 })
    expect(points.find((p) => p.role === 'w')).toMatchObject({ x: 100, y: 150 })
  })

  it('小框只留四角', () => {
    const small = boxHandles({ x: 0, y: 0, width: 40, height: 40 })
    expect(small).toHaveLength(4)
    expect(small.map((p) => p.role).sort()).toEqual(['ne', 'nw', 'se', 'sw'])
  })

  it('宽够高不够只补左右，高够宽不够只补上下', () => {
    const wide = boxHandles({ x: 0, y: 0, width: 60, height: 40 }).map((p) => p.role)
    expect(wide).toContain('e')
    expect(wide).toContain('w')
    expect(wide).not.toContain('n')
    const tall = boxHandles({ x: 0, y: 0, width: 40, height: 60 }).map((p) => p.role)
    expect(tall).toContain('n')
    expect(tall).toContain('s')
    expect(tall).not.toContain('e')
  })

  it('文字（split=Infinity）永远只有四角', () => {
    expect(boxHandles(box, Infinity)).toHaveLength(4)
  })
})

describe('strokeHandles', () => {
  it('线/箭头给两个端点，落在缩放后的位置上', () => {
    const line: SketchStroke = { tool: 'line', color: '#000', width: 2, from: { x: 0, y: 0 }, to: { x: 100, y: 50 } }
    const handles = strokeHandles(line, 2, 1000)
    expect(handles).toHaveLength(2)
    expect(handles.find((h) => h.role === 'start')).toMatchObject({ x: 0, y: 0 })
    expect(handles.find((h) => h.role === 'end')).toMatchObject({ x: 200, y: 100 })
  })

  it('矩形按 48 规则给把手', () => {
    const rect: SketchStroke = { tool: 'rect', color: '#000', width: 2, region: box }
    // scale 1、200x100 够大 → 8 个。
    expect(strokeHandles(rect, 1, 1000)).toHaveLength(8)
  })

  it('文字永远只有四个角', () => {
    const text: SketchStroke = { tool: 'text', color: '#000', width: 2, at: { x: 0, y: 0 }, text: '很长很长的一段文字' }
    expect(strokeHandles(text, 4, 1000)).toHaveLength(4)
  })
})

describe('nearestHandle', () => {
  it('抓取半径之内才认，之外就当没抓到', () => {
    const handles = boxHandles(box)
    expect(nearestHandle(handles, { x: 100 + 47, y: 100 })).toBe('nw')
    expect(nearestHandle(handles, { x: 200, y: 150 })).toBeNull()
  })
})

describe('resizeRegion / resizeBox', () => {
  it('拖 se 角把右下角拉出去，左上角不动', () => {
    expect(resizeRegion(box, 'se', { x: 400, y: 250 })).toEqual({ x: 100, y: 100, width: 300, height: 150 })
  })

  it('拖 w 边只动左边', () => {
    expect(resizeRegion(box, 'w', { x: 150, y: 999 })).toEqual({ x: 150, y: 100, width: 150, height: 100 })
  })

  it('拖过头不翻面：被拖的那条边顶到最小（0），框不会跳到对面去', () => {
    expect(resizeRegion(box, 'e', { x: 50, y: 150 })).toEqual({ x: 100, y: 100, width: 0, height: 100 })
  })

  it('角 + Shift 变正方形：取 |dx|、|dy| 较大者', () => {
    // 从 nw(100,100) 拖到 (400,150)：dx=300、dy=50 → 取 300。
    expect(resizeBox(box, 'se', { x: 400, y: 150 }, true)).toEqual({ x: 100, y: 100, width: 300, height: 300 })
    // 不按 Shift 就各算各的。
    expect(resizeBox(box, 'se', { x: 400, y: 150 }, false)).toEqual({ x: 100, y: 100, width: 300, height: 50 })
  })

  it('角 + Shift 拖过对角：框翻到锚点另一侧，不塌成零', () => {
    // 锚点是右下角的对角 (100,100)，横向拖过了头（点跑到锚点左边）。只取 |dx| 重建，
    // 边长 300 照旧，但框要翻到锚点左边去。
    expect(resizeBox({ x: 100, y: 100, width: 100, height: 100 }, 'se', { x: 50, y: 400 }, true)).toEqual({
      x: -200,
      y: 100,
      width: 300,
      height: 300,
    })
  })

  it('角 + Shift 从零点起步：边长至少 1，别锁死成 0', () => {
    // 这个下限只在整块位移不足 1 像素时才咬得住——位移够大时 `max(|dx|,|dy|)` 本来
    // 就大于 1，改不改都一样。所以取一个亚像素的落点。
    expect(resizeBox({ x: 0, y: 0, width: 0, height: 0 }, 'se', { x: 0.2, y: 0.1 }, true)).toEqual({
      x: 0,
      y: 0,
      width: 1,
      height: 1,
    })
  })
})

describe('handleGrabRadius（抓取半径跟着对象收）', () => {
  const rect = (width: number, height: number): SketchShape => ({
    tool: 'rect',
    color: '#000',
    width: 2,
    region: { x: 0, y: 0, width, height },
  })

  it('大到一定程度就是 48，不缩', () => {
    expect(handleGrabRadius(rect(200, 200), 1, 1000)).toBe(HANDLE_HIT_RADIUS)
  })

  it('小对象收到半个短边，整支还抓得动', () => {
    // 40x40：min(48, 20-1) = 19 —— 上边中点离角 20，判成拖动而不是缩放。
    expect(handleGrabRadius(rect(40, 40), 1, 1000)).toBe(19)
    // 小到 2x2 也不能变成零或负数。
    expect(handleGrabRadius(rect(2, 2), 1, 1000)).toBe(MIN_HANDLE_GRAB)
  })

  it('线按长度收，两端点的圈不互相咬住', () => {
    const line: SketchShape = { tool: 'line', color: '#000', width: 2, from: { x: 0, y: 0 }, to: { x: 60, y: 0 } }
    expect(handleGrabRadius(line, 1, 1000)).toBe(29)
  })

  it('按屏上尺寸算：缩小显示时整支更好抓', () => {
    // 原图 200x200，显示成 0.25 倍即屏上 50x50 → min(48, 25-1) = 24。
    expect(handleGrabRadius(rect(200, 200), 0.25, 1000)).toBe(24)
  })
})

describe('snapAngle45', () => {
  it('把角度吸附到 45° 的整数倍，长度不变', () => {
    const snapped = snapAngle45({ x: 0, y: 0 }, { x: 100, y: 10 })
    expect(snapped.y).toBeCloseTo(0)
    expect(snapped.x).toBeCloseTo(Math.hypot(100, 10))
    const diag = snapAngle45({ x: 0, y: 0 }, { x: 100, y: 90 })
    expect(diag.x).toBeCloseTo(diag.y)
  })
})

describe('moveRegion / moveStroke', () => {
  it('整体平移，尺寸不变', () => {
    expect(moveRegion(box, { x: 10, y: 10 }, { x: 40, y: 25 })).toEqual({ x: 130, y: 115, width: 200, height: 100 })
  })

  it('moveStroke 把中心钳制在画布内', () => {
    const rect: SketchStroke = {
      tool: 'rect',
      color: '#000',
      width: 2,
      region: { x: 0, y: 0, width: 100, height: 100 },
    }
    // 往右下拖很远：中心最高只能到画布右下角 (1000,500)。
    const moved = moveStroke(rect, 5000, 5000, 1000, 500)
    expect(moved.tool).toBe('rect')
    if (moved.tool === 'rect') {
      expect(moved.region.x + moved.region.width / 2).toBeCloseTo(1000)
      expect(moved.region.y + moved.region.height / 2).toBeCloseTo(500)
    }
  })
})

describe('isEmptyStroke', () => {
  it('除涂黑外，退化成一点才算空', () => {
    expect(isEmptyStroke({ tool: 'rect', color: '#000', width: 2, region: { x: 5, y: 5, width: 0, height: 0 } })).toBe(
      true
    )
    // 只塌一边不算空。
    expect(isEmptyStroke({ tool: 'rect', color: '#000', width: 2, region: { x: 5, y: 5, width: 10, height: 0 } })).toBe(
      false
    )
    expect(isEmptyStroke({ tool: 'line', color: '#000', width: 2, from: { x: 5, y: 5 }, to: { x: 5, y: 5 } })).toBe(
      true
    )
    expect(isEmptyStroke({ tool: 'line', color: '#000', width: 2, from: { x: 5, y: 5 }, to: { x: 6, y: 5 } })).toBe(
      false
    )
  })

  it('涂黑对角线不足 4 像素也算空', () => {
    expect(
      isEmptyStroke({ tool: 'redact', color: '#000', width: 2, region: { x: 0, y: 0, width: 2, height: 2 } })
    ).toBe(true)
    expect(
      isEmptyStroke({ tool: 'redact', color: '#000', width: 2, region: { x: 0, y: 0, width: 10, height: 10 } })
    ).toBe(false)
  })

  it('自由笔少于两个点算空', () => {
    expect(isEmptyStroke({ tool: 'pen', color: '#000', width: 2, points: [{ x: 0, y: 0 }] })).toBe(true)
  })
})

describe('能被选中的标注', () => {
  it('除自由笔外都能选：线、箭头、形状、文字', () => {
    expect(isSelectableStroke({ tool: 'rect', color: '#000', width: 2, region: box })).toBe(true)
    expect(isSelectableStroke({ tool: 'ellipse', color: '#000', width: 2, region: box })).toBe(true)
    expect(isSelectableStroke({ tool: 'redact', color: '#000', width: 2, region: box })).toBe(true)
    expect(isSelectableStroke({ tool: 'text', color: '#000', width: 2, at: { x: 1, y: 2 }, text: 'hi' })).toBe(true)
    expect(
      isSelectableStroke({ tool: 'line', color: '#000', width: 2, from: { x: 0, y: 0 }, to: { x: 1, y: 1 } })
    ).toBe(true)
    expect(
      isSelectableStroke({ tool: 'arrow', color: '#000', width: 2, from: { x: 0, y: 0 }, to: { x: 1, y: 1 } })
    ).toBe(true)
    expect(isSelectableStroke({ tool: 'pen', color: '#000', width: 2, points: [{ x: 0, y: 0 }] })).toBe(false)
  })

  it('占块的标注取自己的 region；线取两端点包围盒；文字在锚点处照字号量一个', () => {
    expect(strokeBox({ tool: 'rect', color: '#000', width: 2, region: box }, 1000)).toEqual(box)
    const line = strokeBox(
      { tool: 'line', color: '#000', width: 2, from: { x: 60, y: 20 }, to: { x: 10, y: 70 } },
      1000
    )
    expect(line).toEqual({ x: 10, y: 20, width: 50, height: 50 })
    const text = strokeBox({ tool: 'text', color: '#000', width: 2, at: { x: 50, y: 60 }, text: 'abc' }, 1000)
    expect(text.x).toBe(50)
    expect(text.y).toBe(60)
    expect(text.height).toBeGreaterThan(0)
    expect(text.width).toBeGreaterThan(0)
  })
})

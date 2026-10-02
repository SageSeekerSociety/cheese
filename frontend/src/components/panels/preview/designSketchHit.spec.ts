// 对象级命中测试：点中一笔已画好的标注没有。规则照参考物（Claude 桌面版那套标注器）
// 逐条钉住——容差按线宽浮动、矩形只认边、椭圆只认周长、涂黑认实心包围盒、文字不加容差、
// 自由笔永远不可选、从后往前（最上面先命中）。
import type { SketchStroke } from './designSketch'

import { describe, expect, it } from 'vitest'

import {
  distanceToEllipse,
  ELLIPSE_SAMPLES,
  hitStroke,
  hitTest,
  hitTolerance,
  MOUSE_TOLERANCE,
  TOUCH_TOLERANCE,
} from './designSketchHit'
import { strokeBox } from './designSketchSelection'

const rect: SketchStroke = {
  tool: 'rect',
  color: '#000',
  width: 2,
  region: { x: 100, y: 100, width: 200, height: 100 },
}
const ellipse: SketchStroke = {
  tool: 'ellipse',
  color: '#000',
  width: 2,
  region: { x: 0, y: 0, width: 200, height: 100 },
}
const redact: SketchStroke = {
  tool: 'redact',
  color: '#000',
  width: 2,
  region: { x: 100, y: 100, width: 50, height: 50 },
}
const line: SketchStroke = { tool: 'line', color: '#000', width: 2, from: { x: 0, y: 0 }, to: { x: 100, y: 0 } }
const pen: SketchStroke = {
  tool: 'pen',
  color: '#000',
  width: 2,
  points: [
    { x: 0, y: 0 },
    { x: 100, y: 0 },
  ],
}

describe('hitTolerance', () => {
  it('鼠标 6、触摸 14，且不小于笔画半宽加 3', () => {
    expect(MOUSE_TOLERANCE).toBe(6)
    expect(TOUCH_TOLERANCE).toBe(14)
    expect(hitTolerance(0, false)).toBe(6)
    expect(hitTolerance(0, true)).toBe(14)
    // 线宽 20：20/2 + 3 = 13 > 6。
    expect(hitTolerance(20, false)).toBe(13)
  })

  it('箭头再放大到线宽的两倍', () => {
    // 线宽 10：基础 max(6, 8) = 8，箭头放大到 20。
    expect(hitTolerance(10, false, true)).toBe(20)
    // 放大不会小于基础值。
    expect(hitTolerance(2, false, true)).toBe(6)
  })
})

describe('线段', () => {
  it('点到线段距离 ≤ 容差才算命中', () => {
    expect(hitStroke(line, { x: 50, y: 5 })).toBe(true)
    expect(hitStroke(line, { x: 50, y: 40 })).toBe(false)
    // 端点之外也算「到线段」的距离，不是无限延伸。
    expect(hitStroke(line, { x: 104, y: 0 })).toBe(true)
    expect(hitStroke(line, { x: 140, y: 0 })).toBe(false)
  })

  it('箭头容差更大：同一点线不命中、箭头命中', () => {
    // 线宽 2：线容差 6、箭头容差 6（max(6,4)=6）→ 换大线宽看差异。
    const thick: SketchStroke = { tool: 'arrow', color: '#000', width: 8, from: { x: 0, y: 0 }, to: { x: 100, y: 0 } }
    const thin: SketchStroke = { tool: 'line', color: '#000', width: 8, from: { x: 0, y: 0 }, to: { x: 100, y: 0 } }
    // 线容差 max(6,7)=7；箭头容差 max(7,16)=16。y=12 处只有箭头命中。
    expect(hitStroke(thin, { x: 50, y: 12 })).toBe(false)
    expect(hitStroke(thick, { x: 50, y: 12 })).toBe(true)
  })
})

describe('矩形：只认四条边，不认内部', () => {
  it('框内中心不命中，边上命中', () => {
    expect(hitStroke(rect, { x: 200, y: 150 })).toBe(false)
    expect(hitStroke(rect, { x: 200, y: 102 })).toBe(true)
    expect(hitStroke(rect, { x: 102, y: 150 })).toBe(true)
  })

  it('离边超过容差就不命中', () => {
    expect(hitStroke(rect, { x: 200, y: 100 - 7 })).toBe(false)
    expect(hitStroke(rect, { x: 200, y: 100 - 5 })).toBe(true)
  })
})

describe('椭圆：沿周长采样，只认轮廓', () => {
  it('采样点数是 64', () => {
    expect(ELLIPSE_SAMPLES).toBe(64)
  })

  it('周长上命中，圆内空心处不命中', () => {
    // 右端点 (200,50) 在周长上。
    expect(hitStroke(ellipse, { x: 200, y: 50 })).toBe(true)
    // 中心空心。
    expect(hitStroke(ellipse, { x: 100, y: 50 })).toBe(false)
  })
})

describe('涂黑：实心，认外扩了容差的包围盒', () => {
  it('块内任意一点命中', () => {
    expect(hitStroke(redact, { x: 120, y: 120 })).toBe(true)
  })

  it('块外但仍在容差之内命中，超出就不命中', () => {
    // 线宽 2 → 容差 6：外扩后左边界 94。
    expect(hitStroke(redact, { x: 95, y: 95 })).toBe(true)
    expect(hitStroke(redact, { x: 90, y: 90 })).toBe(false)
  })
})

describe('文字：实测包围盒，盒外不加容差', () => {
  const text: SketchStroke = { tool: 'text', color: '#000', width: 2, at: { x: 100, y: 100 }, text: '改成蓝色' }

  it('盒内命中', () => {
    const box = strokeBox(text, 1000)
    expect(hitStroke(text, { x: box.x + 1, y: box.y + 1 }, { naturalWidth: 1000 })).toBe(true)
  })

  it('盒外一点点也不命中（无容差）', () => {
    const box = strokeBox(text, 1000)
    expect(hitStroke(text, { x: box.x - 2, y: box.y + 1 }, { naturalWidth: 1000 })).toBe(false)
    expect(hitStroke(text, { x: box.x + 1, y: box.y + box.height + 2 }, { naturalWidth: 1000 })).toBe(false)
  })
})

describe('自由笔永远不可选中', () => {
  it('落到采样点上也不命中', () => {
    expect(hitStroke(pen, { x: 50, y: 0 })).toBe(false)
    expect(hitStroke(pen, { x: 0, y: 0 })).toBe(false)
  })
})

describe('从后往前：最上面先命中', () => {
  it('重叠时返回后画的（上面的）那一笔', () => {
    const lower: SketchStroke = {
      tool: 'rect',
      color: '#000',
      width: 2,
      region: { x: 0, y: 0, width: 100, height: 100 },
    }
    const upper: SketchStroke = {
      tool: 'rect',
      color: '#000',
      width: 2,
      region: { x: 0, y: 0, width: 100, height: 100 },
    }
    // 列表最后一个是最上面画的：两个都命中时它先被选中。
    expect(hitTest([lower, upper], { x: 50, y: 0 })).toBe(1)
    expect(hitTest([upper, lower], { x: 50, y: 0 })).toBe(1)
  })

  it('都没打中就 null', () => {
    expect(hitTest([rect], { x: 500, y: 500 })).toBeNull()
  })
})

describe('显示缩放', () => {
  it('scale 把笔画换算到显示坐标再判', () => {
    // 原图笔画在 (0,0)-(100,0)，放大两倍后屏上是 (0,0)-(200,0)。
    expect(hitStroke(line, { x: 100, y: 3 }, { scale: 2 })).toBe(true)
    expect(hitStroke(line, { x: 100, y: 30 }, { scale: 2 })).toBe(false)
  })
})

describe('distanceToEllipse', () => {
  it('两采样点之间的真边界也算近，靠解析那条兜底，不是靠采样密度', () => {
    // 400x100 的椭圆、64 点采样：长半轴那一端的相邻采样点相隔近 20px，中间那段
    // 采样点一个都够不着（(209.8, 0.5) 到最近的采样点约 9.8）。只比采样点的话这个
    // 点会被判成「离轮廓很远」；解析兜底把它拉回真距离。
    const wide = { x: 0, y: 0, width: 400, height: 100 }
    expect(distanceToEllipse({ x: 209.8, y: 0.5 }, wide)).toBeLessThan(1)
    // 出轮廓 20 像素的点解析值也是 20，取 min 不会把远处点拉近。
    expect(distanceToEllipse({ x: 200, y: 120 }, wide)).toBeCloseTo(20, 0)
  })

  it('退化成线段（某一半轴为 0）时，按到那条线段的距离算', () => {
    // width=0、height>0 是能提交的（`isEmptyStroke` 只在宽高都为 0 时才拦）。
    // 这时椭圆就是 (100,100)-(100,200) 这条竖线，落在它延长线上的点离轮廓很近。
    const flat = { x: 100, y: 100, width: 0, height: 100 }
    expect(distanceToEllipse({ x: 100, y: 150 }, flat)).toBe(0)
    expect(distanceToEllipse({ x: 104, y: 150 }, flat)).toBe(4)
    // 端点外面 5 像素：到线段的距离，而不是到中心 (100,150) 的 55。
    expect(distanceToEllipse({ x: 100, y: 205 }, flat)).toBe(5)
  })
})

import type { Point, RasterRegion } from './designRegion'
import type { SketchStroke } from './designSketch'

import { strokeBox } from './designSketchSelection'

/**
 * 点中一笔已画好的标注没有——**对象级命中测试**。规则照参考物（Claude 桌面版那套
 * 标注器）逐条抄：
 *
 * - 容差 `R = max(鼠标 ? 6 : 14（触摸）, 笔画线宽 / 2 + 3)`；箭头再放大到 `max(R, 线宽 * 2)`。
 * - 直线/箭头：点到线段的距离 ≤ R。
 * - 矩形：到 4 条边的最小距离 ≤ R——只认边框，不认内部。
 * - 椭圆：沿周长采样 64 个点取最小距离 ≤ R——同样只认轮廓。
 * - 涂黑：实心的，外扩 R 的整个包围盒判命中。
 * - 文字：文字实测包围盒判命中，盒外不加容差。
 * - 自由笔（pen）永远不可选中。
 * - 遍历从后往前（最上面先命中）。
 *
 * 坐标一律**屏幕像素**：`point` 是光标相对图片的显示坐标，`scale` 用来把笔画换算过来。
 * `scale` 给 1 时就是原图像素，写命中用例时按原图坐标喂即可。
 */
export const MOUSE_TOLERANCE = 6
export const TOUCH_TOLERANCE = 14
export const ELLIPSE_SAMPLES = 64

export type HitOptions = {
  touch?: boolean
  /** 一行/一列屏幕像素对应多少原图像素；默认 1。 */
  scale?: number
  /** 文字量字号用；缺省时按 `fontSize(0)` 的最小字号走。 */
  naturalWidth?: number
}

/** 命中容差：鼠标 6、触摸 14，且不小于笔画半宽加 3；箭头再放宽到线宽的两倍。 */
export function hitTolerance(width: number, touch: boolean, arrow = false): number {
  const base = Math.max(touch ? TOUCH_TOLERANCE : MOUSE_TOLERANCE, width / 2 + 3)
  return arrow ? Math.max(base, width * 2) : base
}

/** 点到线段的最短距离。 */
export function distanceToSegment(point: Point, a: Point, b: Point): number {
  const dx = b.x - a.x
  const dy = b.y - a.y
  const lengthSquared = dx * dx + dy * dy
  if (lengthSquared === 0) return Math.hypot(point.x - a.x, point.y - a.y)
  let t = ((point.x - a.x) * dx + (point.y - a.y) * dy) / lengthSquared
  t = Math.max(0, Math.min(1, t))
  return Math.hypot(point.x - (a.x + t * dx), point.y - (a.y + t * dy))
}

/** 点到矩形四条边的最短距离（不认内部）。 */
export function distanceToRectBorder(point: Point, region: RasterRegion): number {
  const left = region.x
  const right = region.x + region.width
  const top = region.y
  const bottom = region.y + region.height
  const corners: Point[] = [
    { x: left, y: top },
    { x: right, y: top },
    { x: right, y: bottom },
    { x: left, y: bottom },
  ]
  let best = Infinity
  for (let index = 0; index < corners.length; index += 1) {
    const a = corners[index]
    const b = corners[(index + 1) % corners.length]
    best = Math.min(best, distanceToSegment(point, a, b))
  }
  return best
}

/**
 * 点到椭圆周长的最短距离：沿周长采样固定点数取最小（和参考物同法），再用一条解析
 * 上界兜底。
 *
 * 采样是会漏的：相邻两点在周长上的距离最大到 `2π·max(rx,ry)/samples`，超过容差时，
 * 轮廓上两点之间的地方就点不中（400×100 的椭圆、64 点、容差 6，长半轴那一端能差出
 * 近 10 像素）。兜底那条取「点与圆心连线交椭圆于一点」的距离——它是一条到轮廓的真实
 * 距离，只会比最短距离大，所以补得上漏掉的命中，又造不出假的。
 */
export function distanceToEllipse(point: Point, region: RasterRegion, samples = ELLIPSE_SAMPLES): number {
  const cx = region.x + region.width / 2
  const cy = region.y + region.height / 2
  const rx = region.width / 2
  const ry = region.height / 2
  let best = Infinity
  for (let index = 0; index < samples; index += 1) {
    const angle = (index / samples) * Math.PI * 2
    const x = cx + rx * Math.cos(angle)
    const y = cy + ry * Math.sin(angle)
    best = Math.min(best, Math.hypot(point.x - x, point.y - y))
  }
  // 某一半轴为 0 时椭圆退化成一条线段（`isEmptyStroke` 只在宽高**都**为 0 时才拦，
  // 所以这种形状到得了这里）。采样点正好落在那条线段上，`best` 就是到它的距离；解析
  // 那一支会把 0 当除数，跳过。
  if (rx <= 0 || ry <= 0) return best
  const qx = (point.x - cx) / rx
  const qy = (point.y - cy) / ry
  const radius = Math.hypot(qx, qy)
  // 正好落在圆心时连线没有方向，最近的一处在短半轴上。
  if (radius === 0) return Math.min(best, Math.min(rx, ry))
  const crossX = cx + (rx * qx) / radius
  const crossY = cy + (ry * qy) / radius
  return Math.min(best, Math.hypot(point.x - crossX, point.y - crossY))
}

function pointInRegion(point: Point, region: RasterRegion): boolean {
  return (
    point.x >= region.x &&
    point.x <= region.x + region.width &&
    point.y >= region.y &&
    point.y <= region.y + region.height
  )
}

/**
 * 一笔在显示坐标里占的命中区域（已按 scale 换算）。文字用实测盒子（无容差），
 * 涂黑用包围盒（调用方再外扩 R）。
 */
function scaled(stroke: Exclude<SketchStroke, { tool: 'pen' }>, scale: number, naturalWidth: number): RasterRegion {
  const box = strokeBox(stroke, naturalWidth)
  return { x: box.x * scale, y: box.y * scale, width: box.width * scale, height: box.height * scale }
}

/** 单独判一笔。`point` 是显示坐标。 */
export function hitStroke(stroke: SketchStroke, point: Point, options: HitOptions = {}): boolean {
  if (stroke.tool === 'pen') return false
  const scale = options.scale ?? 1
  const touch = options.touch ?? false
  const naturalWidth = options.naturalWidth ?? 0
  const width = stroke.width * scale
  if (stroke.tool === 'line' || stroke.tool === 'arrow') {
    const a = { x: stroke.from.x * scale, y: stroke.from.y * scale }
    const b = { x: stroke.to.x * scale, y: stroke.to.y * scale }
    return distanceToSegment(point, a, b) <= hitTolerance(width, touch, stroke.tool === 'arrow')
  }
  if (stroke.tool === 'redact') {
    const region = scaled(stroke, scale, naturalWidth)
    const tolerance = hitTolerance(width, touch)
    return pointInRegion(point, {
      x: region.x - tolerance,
      y: region.y - tolerance,
      width: region.width + tolerance * 2,
      height: region.height + tolerance * 2,
    })
  }
  if (stroke.tool === 'rect') {
    return distanceToRectBorder(point, scaled(stroke, scale, naturalWidth)) <= hitTolerance(width, touch)
  }
  if (stroke.tool === 'ellipse') {
    return distanceToEllipse(point, scaled(stroke, scale, naturalWidth)) <= hitTolerance(width, touch)
  }
  // 文字：实测盒子，盒外不加容差。
  return pointInRegion(point, scaled(stroke, scale, naturalWidth))
}

/** 最上面那一笔的索引；都没打中就 null。 */
export function hitTest(strokes: readonly SketchStroke[], point: Point, options: HitOptions = {}): number | null {
  for (let index = strokes.length - 1; index >= 0; index -= 1) {
    if (hitStroke(strokes[index], point, options)) return index
  }
  return null
}

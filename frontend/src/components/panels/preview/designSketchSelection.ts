import type { Point, RasterRegion } from './designRegion'
import type { ShapeStroke, SketchStroke, TextStroke } from './designSketch'

import { fontSize } from './designSketch'

/**
 * 选中一块已画好的标注之后，那圈虚线框和它的把手。参数照参考物（Claude 桌面版那套
 * 标注器）抄：虚线 5,4、把手半径 3.5、抓取命中半径 48、描边宽度 4。都是屏幕像素——
 * 框是给人抓的，缩放时不该跟着图一起变大变小。
 */
export const SELECT_DASH: readonly [number, number] = [5, 4]
export const HANDLE_RADIUS = 3.5
export const HANDLE_HIT_RADIUS = 48
export const SELECT_STROKE_WIDTH = 4

/** 八个把手：四角加四边中点。 */
export type HandleRole = 'nw' | 'n' | 'ne' | 'e' | 'se' | 's' | 'sw' | 'w'

const ROLES: readonly { role: HandleRole; fx: number; fy: number }[] = [
  { role: 'nw', fx: 0, fy: 0 },
  { role: 'n', fx: 0.5, fy: 0 },
  { role: 'ne', fx: 1, fy: 0 },
  { role: 'e', fx: 1, fy: 0.5 },
  { role: 'se', fx: 1, fy: 1 },
  { role: 's', fx: 0.5, fy: 1 },
  { role: 'sw', fx: 0, fy: 1 },
  { role: 'w', fx: 0, fy: 0.5 },
]

/** 八个把手在框上的位置（和框同一套坐标）。 */
export function handlePoints(region: RasterRegion): { role: HandleRole; x: number; y: number }[] {
  return ROLES.map(({ role, fx, fy }) => ({
    role,
    x: region.x + region.width * fx,
    y: region.y + region.height * fy,
  }))
}

/** 离 `point` 最近、且在抓取半径内的那个把手；没有就是 null。 */
export function nearestHandle(region: RasterRegion, point: Point, radius = HANDLE_HIT_RADIUS): HandleRole | null {
  let best: HandleRole | null = null
  let closest = radius
  for (const handle of handlePoints(region)) {
    const distance = Math.hypot(handle.x - point.x, handle.y - point.y)
    if (distance <= closest) {
      closest = distance
      best = handle.role
    }
  }
  return best
}

/**
 * 拖某个把手之后的新框。被拖的那两条边跟着指针对走，对边不动；拖过头了就把宽高
 * 收到最小（不翻面，翻面会让「抓住的那个角」在拖的过程中跳到对面）。
 */
export function resizeRegion(region: RasterRegion, role: HandleRole, point: Point, min = 2): RasterRegion {
  let left = region.x
  let top = region.y
  let right = region.x + region.width
  let bottom = region.y + region.height
  // 拖的那条边不许越过对边：越过了就把这一边顶到最小，而不是让框整个跳到对面去
  // （跳过去的话，正被抓住的那个角会在拖动途中忽然换手）。
  if (role.includes('w')) left = Math.min(point.x, right - min)
  if (role.includes('e')) right = Math.max(point.x, left + min)
  if (role.includes('n')) top = Math.min(point.y, bottom - min)
  if (role.includes('s')) bottom = Math.max(point.y, top + min)
  return { x: left, y: top, width: right - left, height: bottom - top }
}

/** 整个框跟着指针平移的量（文字那种「只有锚点、改不了大小」的标注用它）。 */
export function moveRegion(region: RasterRegion, from: Point, to: Point): RasterRegion {
  return { ...region, x: region.x + (to.x - from.x), y: region.y + (to.y - from.y) }
}

/**
 * 能被选中、改框的标注：带 `region` 的块状标注（矩形、椭圆、涂黑），加上文字。
 *
 * 参考物把这四种都当块；我们这边文字只存了一个锚点，没有框，所以给它量一个
 * 临时盒子（见 `strokeBox`），拖的时候只能挪、不能缩放。
 */
export function isSelectableStroke(stroke: SketchStroke): stroke is ShapeStroke | TextStroke {
  return stroke.tool === 'rect' || stroke.tool === 'ellipse' || stroke.tool === 'redact' || stroke.tool === 'text'
}

/** 只有带 `region` 的标注能缩放；文字只能平移。 */
export function canResize(stroke: ShapeStroke | TextStroke): boolean {
  return stroke.tool !== 'text'
}

/** 选中框用的盒子（原图像素）。文字没有框，就照字号在锚点处量一个。 */
export function strokeBox(stroke: ShapeStroke | TextStroke, naturalWidth: number): RasterRegion {
  if (stroke.tool === 'text') {
    const size = fontSize(naturalWidth)
    return { x: stroke.at.x, y: stroke.at.y, width: Math.max(size, stroke.text.length * size * 0.6), height: size }
  }
  return stroke.region
}

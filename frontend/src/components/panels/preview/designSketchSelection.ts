import type { Point, RasterRegion } from './designRegion'
import type { SketchStroke, TextStroke } from './designSketch'

import { fontSize, isShapeStroke } from './designSketch'

/**
 * 选中一笔已画好的标注之后的把手与几何。参数照参考物（Claude 桌面版那套标注器）：
 *
 * - 选中态是**对象级编辑**：点中一笔就能拖、伸缩、改色、删掉。
 * - 只有**文字**画一圈虚线框（每边外扩 4），别的形状只画把手圆点，不画框。
 * - 把手半径 3.5、描边 1；抓取命中半径 48。
 * - 角把手恒有；宽 ≥ 48 再补左右两个（e/w），高 ≥ 48 再补上下两个（n/s）。
 * - 没有旋转。
 *
 * 坐标：把手位置按**屏幕像素**算（框是给人抓的，缩放时不该跟着图一起变大变小）；
 * 变换函数一律在原图像素里算（缩放是均匀的，两者等价）。
 */
export const SELECT_DASH: readonly [number, number] = [5, 4]
export const HANDLE_RADIUS = 3.5
export const HANDLE_HIT_RADIUS = 48
export const HANDLE_STROKE_WIDTH = 1
/** 抓取半径收到底也不再小于这个值，否则把手会小到抓不住。 */
export const MIN_HANDLE_GRAB = 8
/** 文字选中框的虚线描边；比把手粗一点，看得清。 */
export const TEXT_BOX_STROKE_WIDTH = 1.5
/** 文字选中框每边向外扩这么多。 */
export const TEXT_BOX_PADDING = 4
/** 宽/高到这个数才补两侧的中点把手。 */
export const HANDLE_SPLIT = 48

/** 四角与四边中点这八个位置；线/箭头另有两个端点。 */
export type BoxHandleRole = 'nw' | 'n' | 'ne' | 'e' | 'se' | 's' | 'sw' | 'w'
export type LineHandleRole = 'start' | 'end'
export type HandleRole = BoxHandleRole | LineHandleRole

const CORNERS: readonly { role: BoxHandleRole; fx: number; fy: number }[] = [
  { role: 'nw', fx: 0, fy: 0 },
  { role: 'ne', fx: 1, fy: 0 },
  { role: 'se', fx: 1, fy: 1 },
  { role: 'sw', fx: 0, fy: 1 },
]
const MIDS: readonly { role: BoxHandleRole; axis: 'x' | 'y'; fx: number; fy: number }[] = [
  { role: 'e', axis: 'x', fx: 1, fy: 0.5 },
  { role: 'w', axis: 'x', fx: 0, fy: 0.5 },
  { role: 'n', axis: 'y', fx: 0.5, fy: 0 },
  { role: 's', axis: 'y', fx: 0.5, fy: 1 },
]

/**
 * 一个框的把手点位。四角恒有；`split` 之上才补两侧中点（文字用不到，传 Infinity）。
 * `split` 按框当前的尺寸量，所以它跟着屏幕上的大小走，不跟原图大小走。
 */
export function boxHandles(
  region: RasterRegion,
  split = HANDLE_SPLIT
): { role: BoxHandleRole; x: number; y: number }[] {
  const points: { role: BoxHandleRole; x: number; y: number }[] = CORNERS.map(({ role, fx, fy }) => ({
    role,
    x: region.x + region.width * fx,
    y: region.y + region.height * fy,
  }))
  for (const mid of MIDS) {
    if (mid.axis === 'x' ? region.width >= split : region.height >= split) {
      points.push({ role: mid.role, x: region.x + region.width * mid.fx, y: region.y + region.height * mid.fy })
    }
  }
  return points
}

/**
 * 能被选中、编辑的笔画：除自由笔（pen）之外都算。
 *
 * 参照物明确「涂鸦不可编辑」——pen 一把一把的采样点既难命中也不值得拖，画完就定死。
 */
export function isSelectableStroke(stroke: SketchStroke): stroke is Exclude<SketchStroke, { tool: 'pen' }> {
  return stroke.tool !== 'pen'
}

/** 只有带 `region` 的块状标注能伸缩；文字只能平移。 */
export function canResize(stroke: Exclude<SketchStroke, { tool: 'pen' }>): boolean {
  return stroke.tool !== 'text'
}

/**
 * 笔画占的盒子（原图像素）。块状取自己的 region，线/箭头取两端点的包围盒，文字照
 * 字号在锚点处量一个。
 */
export function strokeBox(stroke: Exclude<SketchStroke, { tool: 'pen' }>, naturalWidth: number): RasterRegion {
  if (stroke.tool === 'text') return textBox(stroke, naturalWidth)
  // 块状那一支走断言函数收窄：`tool === 'line' || tool === 'arrow'` 这种把某个成员的
  // 判别式全列出来的写法，在不成立的支上并不会把那个成员排除掉（见 designSketch.ts）。
  if (isShapeStroke(stroke)) return stroke.region
  const x = Math.min(stroke.from.x, stroke.to.x)
  const y = Math.min(stroke.from.y, stroke.to.y)
  return { x, y, width: Math.abs(stroke.to.x - stroke.from.x), height: Math.abs(stroke.to.y - stroke.from.y) }
}

/**
 * 文字占的盒子：宽度用画布实测，量不到（测试环境没有 2D 上下文）就按字数估。
 * 高度就是字号。
 */
export function textBox(stroke: TextStroke, naturalWidth: number): RasterRegion {
  const size = fontSize(naturalWidth)
  return { x: stroke.at.x, y: stroke.at.y, width: measureTextWidth(stroke.text, size), height: size }
}

/** 画布实测一段文字的宽度；没有画布时退回「每字 0.6 个字号」的估算。 */
let measureContext: CanvasRenderingContext2D | null | undefined
export function measureTextWidth(text: string, size: number): number {
  if (measureContext === undefined) {
    try {
      const canvas = document.createElement('canvas')
      const context = typeof canvas.getContext === 'function' ? canvas.getContext('2d') : null
      measureContext = context && typeof context.measureText === 'function' ? context : null
    } catch {
      measureContext = null
    }
  }
  if (measureContext) {
    try {
      measureContext.font = `${size}px sans-serif`
      const width = measureContext.measureText(text).width
      if (width > 0) return width
    } catch {
      // 量不出来就退回估算，命中框只是将就，不是崩掉的理由。
    }
  }
  return text.length * size * 0.6
}

function scaleRegion(region: RasterRegion, scale: number): RasterRegion {
  return { x: region.x * scale, y: region.y * scale, width: region.width * scale, height: region.height * scale }
}

/** 一笔在**屏幕像素**里的把手（文字永远只有四角，形状按 48 规则补中点）。 */
export function strokeHandles(
  stroke: Exclude<SketchStroke, { tool: 'pen' }>,
  scale: number,
  naturalWidth: number
): { role: HandleRole; x: number; y: number }[] {
  if (stroke.tool === 'line' || stroke.tool === 'arrow') {
    return [
      { role: 'start', x: stroke.from.x * scale, y: stroke.from.y * scale },
      { role: 'end', x: stroke.to.x * scale, y: stroke.to.y * scale },
    ]
  }
  const box = scaleRegion(strokeBox(stroke, naturalWidth), scale)
  return boxHandles(box, stroke.tool === 'text' ? Infinity : HANDLE_SPLIT)
}

/**
 * 抓一个把手时可以离多远（屏幕像素）。默认 48 是给正常大小的对象留的余量，但小对象
 * 整支都落在这 48 里面：一个 40×40 的框，四个角都在中心 28.3 之内，按中心就变成缩放，
 * 平移不了。所以半径跟着对象收：取「短边的一半退一点」，短线则看长度的一半（中点离
 * 端点最远）。收到底也不小于 `MIN_HANDLE_GRAB`。
 */
export function handleGrabRadius(
  stroke: Exclude<SketchStroke, { tool: 'pen' }>,
  scale: number,
  naturalWidth: number
): number {
  const box = scaleRegion(strokeBox(stroke, naturalWidth), scale)
  const reach =
    stroke.tool === 'line' || stroke.tool === 'arrow'
      ? Math.max(box.width, box.height) / 2
      : Math.min(box.width, box.height) / 2
  return Math.max(MIN_HANDLE_GRAB, Math.min(HANDLE_HIT_RADIUS, reach - 1))
}

/** 离 `point` 最近、且在抓取半径内的那个把手；没有就是 null。 */
export function nearestHandle(
  handles: readonly { role: HandleRole; x: number; y: number }[],
  point: Point,
  radius = HANDLE_HIT_RADIUS
): HandleRole | null {
  let best: HandleRole | null = null
  let closest = radius
  for (const handle of handles) {
    const distance = Math.hypot(handle.x - point.x, handle.y - point.y)
    if (distance <= closest) {
      closest = distance
      best = handle.role
    }
  }
  return best
}

/** 角把手的对角锚点（缩放时不动的那一角）。 */
function cornerAnchor(region: RasterRegion, role: BoxHandleRole): Point {
  const right = region.x + region.width
  const bottom = region.y + region.height
  return { x: role.includes('w') ? right : region.x, y: role.includes('n') ? bottom : region.y }
}

/** 把角上的点拽成正方形：取 |dx|、|dy| 里大的那个，方向各按原符号；边长不小于 `min`。 */
export function squarePoint(anchor: Point, point: Point, min = 0): Point {
  const dx = point.x - anchor.x
  const dy = point.y - anchor.y
  const size = Math.max(Math.abs(dx), Math.abs(dy), min)
  return { x: anchor.x + (dx < 0 ? -size : size), y: anchor.y + (dy < 0 ? -size : size) }
}

/**
 * 拖某个把手之后的新框。被拖的那两条边跟着指针对走，对边不动；拖过头了就把宽高收到
 * 最小（`min` 默认为 0，即任其退化，翻面会让「抓住的那个角」在拖动途中忽然换手）。
 */
export function resizeRegion(region: RasterRegion, role: BoxHandleRole, point: Point, min = 0): RasterRegion {
  let left = region.x
  let top = region.y
  let right = region.x + region.width
  let bottom = region.y + region.height
  if (role.includes('w')) left = Math.min(point.x, right - min)
  if (role.includes('e')) right = Math.max(point.x, left + min)
  if (role.includes('n')) top = Math.min(point.y, bottom - min)
  if (role.includes('s')) bottom = Math.max(point.y, top + min)
  return { x: left, y: top, width: right - left, height: bottom - top }
}

/**
 * 拖一个框把手：角把手 + Shift 时先拽成正方形再缩放；边把手不受 Shift 影响。
 *
 * 正方形那条路要**从锚点把整个框重建出来**，不能只挪被拖的那两条边：把角拖到锚点
 * 另一侧时正方形朝反方向长，而「只挪两条边」会把宽或高截成 0，正方形就没了。
 */
export function resizeBox(
  region: RasterRegion,
  role: BoxHandleRole,
  point: Point,
  shift = false,
  min = 0
): RasterRegion {
  if (shift && role.length === 2) {
    const anchor = cornerAnchor(region, role)
    const corner = squarePoint(anchor, point, Math.max(min, 1))
    return {
      x: Math.min(anchor.x, corner.x),
      y: Math.min(anchor.y, corner.y),
      width: Math.abs(corner.x - anchor.x),
      height: Math.abs(corner.y - anchor.y),
    }
  }
  return resizeRegion(region, role, point, min)
}

/** 整个框跟着指针平移的量（文字那种「只有锚点、改不了大小」的标注用它）。 */
export function moveRegion(region: RasterRegion, from: Point, to: Point): RasterRegion {
  return { ...region, x: region.x + (to.x - from.x), y: region.y + (to.y - from.y) }
}

/** 与水平方向夹角吸附到 45° 的整数倍；保持长度不变。 */
export function snapAngle45(anchor: Point, point: Point): Point {
  const dx = point.x - anchor.x
  const dy = point.y - anchor.y
  const length = Math.hypot(dx, dy)
  if (length === 0) return { ...point }
  const step = Math.PI / 4
  const angle = Math.round(Math.atan2(dy, dx) / step) * step
  return { x: anchor.x + length * Math.cos(angle), y: anchor.y + length * Math.sin(angle) }
}

/**
 * 拖一条线/箭头某个端点之后的新两端点。Shift 把角度吸附到 45°。
 * `point` 与返回值都在原图像素里。
 */
export function resizeLine(
  stroke: { from: Point; to: Point },
  role: LineHandleRole,
  point: Point,
  shift: boolean
): { from: Point; to: Point } {
  const anchor = role === 'start' ? stroke.to : stroke.from
  const target = shift ? snapAngle45(anchor, point) : point
  return role === 'start' ? { from: target, to: stroke.to } : { from: stroke.from, to: target }
}

/** 一个点相对整个框的中心偏移，用来判「拖完中心有没有跑出画布」。 */
function centerOfBox(region: RasterRegion): Point {
  return { x: region.x + region.width / 2, y: region.y + region.height / 2 }
}

/**
 * 平移一整笔：中心点钳制在画布内。`dx/dy` 是原图像素里的位移。
 *
 * 钳制看的是中心，不是边框——大图形允许有一部分探出去，只要中心还在图里。
 */
export function moveStroke(
  stroke: Exclude<SketchStroke, { tool: 'pen' }>,
  dx: number,
  dy: number,
  naturalWidth: number,
  naturalHeight: number
): Exclude<SketchStroke, { tool: 'pen' }> {
  const box = strokeBox(stroke, naturalWidth)
  const center = centerOfBox(box)
  const clampedX = Math.max(0, Math.min(naturalWidth, center.x + dx))
  const clampedY = Math.max(0, Math.min(naturalHeight, center.y + dy))
  const shiftX = clampedX - center.x
  const shiftY = clampedY - center.y
  return shiftStroke(stroke, shiftX, shiftY)
}

/** 把一整笔平移 (dx,dy) 个原图像素，不做钳制。 */
export function shiftStroke(
  stroke: Exclude<SketchStroke, { tool: 'pen' }>,
  dx: number,
  dy: number
): Exclude<SketchStroke, { tool: 'pen' }> {
  if (isShapeStroke(stroke)) {
    return { ...stroke, region: { ...stroke.region, x: stroke.region.x + dx, y: stroke.region.y + dy } }
  }
  if (stroke.tool === 'text') return { ...stroke, at: { x: stroke.at.x + dx, y: stroke.at.y + dy } }
  return {
    ...stroke,
    from: { x: stroke.from.x + dx, y: stroke.from.y + dy },
    to: { x: stroke.to.x + dx, y: stroke.to.y + dy },
  }
}

/**
 * 一笔「空」了没有——空的图形不进历史，也不留在屏上。
 *
 * 除涂黑（redact）外，退化成一点（宽高都为 0）才算空；redact 是实心块，小到对角线
 * 不足 4 像素就盖不住东西，也当空。
 */
export function isEmptyStroke(stroke: SketchStroke): boolean {
  if (stroke.tool === 'pen') return stroke.points.length < 2
  if (stroke.tool === 'text') return stroke.text.trim().length === 0
  if (!isShapeStroke(stroke)) return stroke.from.x === stroke.to.x && stroke.from.y === stroke.to.y
  const { width, height } = stroke.region
  if (stroke.tool === 'redact') return (width === 0 && height === 0) || Math.hypot(width, height) < 4
  return width === 0 && height === 0
}

/** 只有文字画虚线框；形状只画把手。 */
export function drawsFrame(stroke: Exclude<SketchStroke, { tool: 'pen' }> | null): boolean {
  return !!stroke && stroke.tool === 'text'
}

/** 拖一个角/边把手之后的新笔画，按角色分派到 `resizeBox`（形状）或端点（线/箭头）。 */
export function applyResize(
  stroke: Exclude<SketchStroke, { tool: 'pen' }>,
  role: HandleRole,
  point: Point,
  shift: boolean
): Exclude<SketchStroke, { tool: 'pen' }> {
  if (isShapeStroke(stroke)) {
    const boxRole = (role === 'start' || role === 'end' ? 'se' : role) as BoxHandleRole
    return { ...stroke, region: resizeBox(stroke.region, boxRole, point, shift) }
  }
  if (stroke.tool === 'text') {
    // 文字没有可缩的框，把手拖动归到平移（见 `moveStroke`）。
    return stroke
  }
  const end: LineHandleRole = role === 'start' ? 'start' : 'end'
  return { ...stroke, ...resizeLine(stroke, end, point, shift) }
}

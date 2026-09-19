/**
 * diagramGeometry.ts — 画连线用的纯几何。
 *
 * 从组件里拆出来是为了能单测：这几个函数错一点点，图还是画得出来，只是线连到
 * 了不该连的地方 —— 那种错肉眼一眼看不出来，但对着图做设计的人会被带偏。
 * 这里不碰 DOM、不碰 Vue，输入坐标输出路径字符串。
 */

export interface DiagramBox {
  x: number
  y: number
  w: number
  h: number
}

export interface Point {
  x: number
  y: number
}

/** 三次贝塞尔上 t 处的点。t=0 是起点，t=1 是终点。 */
export function bezierPoint(p0: Point, p1: Point, p2: Point, p3: Point, t: number): Point {
  const mt = 1 - t
  const a = mt * mt * mt
  const b = 3 * mt * mt * t
  const c = 3 * mt * t * t
  const d = t * t * t
  return {
    x: a * p0.x + b * p1.x + c * p2.x + d * p3.x,
    y: a * p0.y + b * p1.y + c * p2.y + d * p3.y,
  }
}

export function curvePath(p0: Point, p1: Point, p2: Point, p3: Point): string {
  return `M ${p0.x} ${p0.y} C ${p1.x} ${p1.y}, ${p2.x} ${p2.y}, ${p3.x} ${p3.y}`
}

/**
 * 指向 `to` 的实心箭头，尖端落在 `to`。
 *
 * 方向由「上一段控制点 → 端点」决定，也就是曲线末端的切线方向。
 */
export function arrowPath(from: Point, to: Point, length = 8, spread = 4.5): string {
  const angle = Math.atan2(to.y - from.y, to.x - from.x)
  const backX = to.x - Math.cos(angle) * length
  const backY = to.y - Math.sin(angle) * length
  const offX = Math.cos(angle + Math.PI / 2) * spread
  const offY = Math.sin(angle + Math.PI / 2) * spread
  return `M ${to.x} ${to.y} L ${backX + offX} ${backY + offY} L ${backX - offX} ${backY - offY} Z`
}

/**
 * 给两个盒子挑一对锚点和两个控制点。
 *
 * 上下距离更大就走竖边，否则走横边 —— 分层画出来的图里，跨层连线走竖边、
 * 同层连线走横边，视觉上才跟布局一致。
 *
 * 返回值就是 `curvePath` 的四个参数。
 */
export function connect(a: DiagramBox, b: DiagramBox): [Point, Point, Point, Point] {
  const ax = a.x + a.w / 2
  const ay = a.y + a.h / 2
  const bx = b.x + b.w / 2
  const by = b.y + b.h / 2
  const dx = bx - ax
  const dy = by - ay

  if (Math.abs(dy) >= Math.abs(dx)) {
    const dir = dy >= 0 ? 1 : -1
    const p0 = { x: ax, y: dir > 0 ? a.y + a.h : a.y }
    const p3 = { x: bx, y: dir > 0 ? b.y : b.y + b.h }
    const reach = Math.max(24, Math.min(120, Math.abs(dy) * 0.4))
    return [p0, { x: p0.x, y: p0.y + dir * reach }, { x: p3.x, y: p3.y - dir * reach }, p3]
  }

  const dir = dx >= 0 ? 1 : -1
  const p0 = { x: dir > 0 ? a.x + a.w : a.x, y: ay }
  const p3 = { x: dir > 0 ? b.x : b.x + b.w, y: by }
  const reach = Math.max(24, Math.min(140, Math.abs(dx) * 0.4))
  return [p0, { x: p0.x + dir * reach, y: p0.y }, { x: p3.x - dir * reach, y: p3.y }, p3]
}

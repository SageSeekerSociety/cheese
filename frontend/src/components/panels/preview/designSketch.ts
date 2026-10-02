import type { Point, RasterRegion } from './designRegion'
import type { SampledImage } from './designSnap'

/** 画布上的工具。`select` 不画东西，它框选要交给芝士的那一块。 */
export type SketchTool = 'select' | 'pen' | 'line' | 'arrow' | 'rect' | 'ellipse' | 'text' | 'redact'

/** 会往图上画东西的工具；`select` 不在其中，它由工具栏最左边那颗单独的按钮表示。 */
export const DRAW_TOOLS: readonly SketchTool[] = ['pen', 'line', 'arrow', 'rect', 'ellipse', 'text', 'redact']

/**
 * 五种颜色：四个显眼的加上一个当涂黑用的近黑。
 *
 * 不是参考物那一套——参考物是 #E03131 #1971C2 #2F9E44 #1F1E1D 再加一个白。这里
 * 没跟着换成白色：白在浅色底上看不见，要它看得见得先配一圈描边，那是另一件事。
 */
export const SKETCH_COLORS: readonly string[] = ['#e5484d', '#f5a524', '#30a46c', '#0091ff', '#16181d']

export type PenStroke = { tool: 'pen'; color: string; width: number; points: Point[] }
export type LineStroke = { tool: 'line' | 'arrow'; color: string; width: number; from: Point; to: Point }
export type ShapeStroke = {
  tool: 'rect' | 'ellipse' | 'redact'
  color: string
  width: number
  region: RasterRegion
  /** 文字标注：画在框左上角的字。 */
  text?: string
}
export type TextStroke = { tool: 'text'; color: string; width: number; at: Point; text: string }
export type SketchStroke = PenStroke | LineStroke | ShapeStroke | TextStroke

/**
 * 笔宽跟着图的大小走：一律按原图像素记，屏幕上再乘缩放。
 *
 * 固定像素的笔宽在大图上细成一根头发、在小图上粗成一条杠，而标注要么是
 * 在人眼看到的屏幕上画的，不该因为原图分辨率不同而变形。
 */
export function strokeWidth(naturalWidth: number): number {
  if (!Number.isFinite(naturalWidth) || naturalWidth <= 0) return 3
  return Math.max(2, Math.min(24, Math.round(naturalWidth / 320)))
}

/**
 * 成块的标注：矩形、椭圆、涂黑。它们都带 `region`，导出时都编号，也是唯一几种
 * 「点一下自动框出来」能落到的东西。
 *
 * 单写成断言函数是为了让调用处收窄得了：`tool === 'line' || tool === 'arrow'`
 * 这种把某个成员的判别式全部列出来的写法，在不成立的支上并不会把它排除掉。
 */
export function isShapeStroke(stroke: SketchStroke): stroke is ShapeStroke {
  return stroke.tool === 'rect' || stroke.tool === 'ellipse' || stroke.tool === 'redact'
}

/** 矩形/涂黑这类成块的标注带序号，1 起；其它笔画不编号。 */
export function numberedStrokes(strokes: readonly SketchStroke[]): { index: number; stroke: ShapeStroke }[] {
  const numbered: { index: number; stroke: ShapeStroke }[] = []
  for (const stroke of strokes) {
    if (isShapeStroke(stroke)) numbered.push({ index: numbered.length + 1, stroke })
  }
  return numbered
}

/**
 * 箭头的三个角：左翼、箭尖、右翼。屏幕和导出都照这三个点画，两边不会长得不一样。
 *
 * 光有两翼是个退化图形——两个点围不出面，屏幕上的 `<polygon>` 会一个字都不画，
 * 于是「箭头」画出来只是一条直线（导出那条路自己补了箭尖，所以只有屏幕上缺）。
 * 箭尖必须在这一串里，别把两翼拆开单独用。
 */
export function arrowHeadPoints(from: Point, to: Point, width: number): Point[] {
  const [left, right] = arrowHead(from, to, width)
  return [left, to, right]
}

/** 箭头两翼，屏幕和导出共用一套几何，两边不会长得不一样。 */
export function arrowHead(from: Point, to: Point, width: number): [Point, Point] {
  const length = Math.hypot(to.x - from.x, to.y - from.y)
  if (length < 1) return [to, to]
  const size = Math.max(width * 3.5, length * 0.25)
  const angle = Math.atan2(to.y - from.y, to.x - from.x)
  const spread = 0.42
  return [
    {
      x: to.x - size * Math.cos(angle - spread),
      y: to.y - size * Math.sin(angle - spread),
    },
    {
      x: to.x - size * Math.cos(angle + spread),
      y: to.y - size * Math.sin(angle + spread),
    },
  ]
}

/** 一行文字在大图上也要看得见，字号与笔宽同一套比例。 */
export function fontSize(naturalWidth: number): number {
  return Math.max(12, strokeWidth(naturalWidth) * 6)
}

/**
 * 把图缩到 ≤max 像素再取像素，供内容分界分析用。
 *
 * 分析是逐像素的，原图动辄上千万像素；缩到 512 做投影，边界位置换算回去
 * 只差一两个原图像素，对吸附来说足够。
 */
export function sampleImage(image: HTMLImageElement, max = 512): SampledImage | null {
  const naturalWidth = image.naturalWidth
  const naturalHeight = image.naturalHeight
  if (!naturalWidth || !naturalHeight) return null
  const scale = Math.min(1, max / Math.max(naturalWidth, naturalHeight))
  const width = Math.max(1, Math.round(naturalWidth * scale))
  const height = Math.max(1, Math.round(naturalHeight * scale))
  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height
  // 没有 2D 上下文的宿主（jsdom/happy-dom、被禁用的画布）直接放弃：分界线是锦上添花，
  // 没有它拖动就退化成自由框选。
  if (typeof canvas.getContext !== 'function') return null
  const context = canvas.getContext('2d', { willReadFrequently: true })
  if (!context) return null
  context.drawImage(image, 0, 0, width, height)
  try {
    const pixels = context.getImageData(0, 0, width, height)
    return { data: pixels.data, width, height }
  } catch {
    // 跨域图会污染画布，读像素直接抛错；没有分界线，功能退化成自由拖动。
    return null
  }
}

function paintStroke(context: CanvasRenderingContext2D, stroke: SketchStroke, scale: number, naturalWidth: number) {
  const width = Math.max(1, stroke.width * scale)
  const line = (region: RasterRegion) => ({
    x: region.x * scale,
    y: region.y * scale,
    width: region.width * scale,
    height: region.height * scale,
  })
  context.save()
  context.lineCap = 'round'
  context.lineJoin = 'round'
  context.strokeStyle = stroke.color
  context.fillStyle = stroke.color
  context.lineWidth = width
  if (stroke.tool === 'pen') {
    context.beginPath()
    for (const [index, point] of stroke.points.entries()) {
      const x = point.x * scale
      const y = point.y * scale
      if (index === 0) context.moveTo(x, y)
      else context.lineTo(x, y)
    }
    if (stroke.points.length === 1) {
      const only = stroke.points[0]
      context.arc(only.x * scale, only.y * scale, width / 2, 0, Math.PI * 2)
      context.fill()
    } else {
      context.stroke()
    }
  } else if (stroke.tool === 'line' || stroke.tool === 'arrow') {
    const from = { x: stroke.from.x * scale, y: stroke.from.y * scale }
    const to = { x: stroke.to.x * scale, y: stroke.to.y * scale }
    context.beginPath()
    context.moveTo(from.x, from.y)
    context.lineTo(to.x, to.y)
    context.stroke()
    if (stroke.tool === 'arrow') {
      const [left, tip, right] = arrowHeadPoints(stroke.from, stroke.to, stroke.width)
      context.beginPath()
      context.moveTo(left.x * scale, left.y * scale)
      context.lineTo(tip.x * scale, tip.y * scale)
      context.lineTo(right.x * scale, right.y * scale)
      context.closePath()
      context.fill()
    }
  } else if (stroke.tool === 'redact') {
    // 涂黑是实心的：它要盖住东西，不是勾出东西。
    const box = line(stroke.region)
    context.fillRect(box.x, box.y, box.width, box.height)
  } else if (stroke.tool === 'rect') {
    const box = line(stroke.region)
    context.strokeRect(box.x, box.y, box.width, box.height)
  } else if (stroke.tool === 'ellipse') {
    const box = line(stroke.region)
    context.beginPath()
    context.ellipse(box.x + box.width / 2, box.y + box.height / 2, box.width / 2, box.height / 2, 0, 0, Math.PI * 2)
    context.stroke()
  } else if (stroke.tool === 'text') {
    context.font = `${fontSize(naturalWidth) * scale}px sans-serif`
    context.textBaseline = 'top'
    context.fillText(stroke.text, stroke.at.x * scale, stroke.at.y * scale)
  }
  context.restore()
}

function paintBadge(context: CanvasRenderingContext2D, at: Point, index: number, scale: number, naturalWidth: number) {
  const radius = Math.max(8, fontSize(naturalWidth) * 0.7) * scale
  const x = at.x * scale
  const y = at.y * scale
  context.save()
  context.beginPath()
  context.arc(x, y, radius, 0, Math.PI * 2)
  context.fillStyle = '#16181d'
  context.fill()
  context.strokeStyle = '#ffffff'
  context.lineWidth = Math.max(1, radius * 0.16)
  context.stroke()
  context.fillStyle = '#ffffff'
  context.font = `600 ${radius * 1.2}px sans-serif`
  context.textAlign = 'center'
  context.textBaseline = 'middle'
  context.fillText(String(index), x, y)
  context.restore()
}

/** 导出时封顶的边长；太大就按比例缩，宁小勿缺。 */
const MAX_EXPORT_EDGE = 2400
/** 上传通道的单文件上限是 10 MiB，这里留一点余量。 */
export const MAX_COMPOSITE_BYTES = 9 * 1024 * 1024

function encode(canvas: HTMLCanvasElement): Promise<Blob> {
  return new Promise((resolve, reject) => {
    if (typeof canvas.toBlob !== 'function') return reject(new Error('canvas.toBlob unavailable'))
    canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error('canvas.toBlob failed'))), 'image/png')
  })
}

/**
 * 原图 + 所有笔画合成一张 PNG。
 *
 * 出来的图就是发给芝士看的那一张；屏幕上怎么画的，导出就怎么画——两处共用
 * 同一套坐标（原图像素）和同一套几何。
 */
export async function composeSketch(image: HTMLImageElement, strokes: readonly SketchStroke[]): Promise<Blob | null> {
  const naturalWidth = image.naturalWidth
  const naturalHeight = image.naturalHeight
  if (!naturalWidth || !naturalHeight) return null
  const fit = Math.min(1, MAX_EXPORT_EDGE / Math.max(naturalWidth, naturalHeight))
  let scale = fit
  for (let attempt = 0; attempt < 4; attempt += 1) {
    const canvas = document.createElement('canvas')
    canvas.width = Math.max(1, Math.round(naturalWidth * scale))
    canvas.height = Math.max(1, Math.round(naturalHeight * scale))
    if (typeof canvas.getContext !== 'function') return null
    const context = canvas.getContext('2d')
    if (!context) return null
    context.drawImage(image, 0, 0, canvas.width, canvas.height)
    for (const { index, stroke } of numberedStrokes(strokes)) {
      paintStroke(context, stroke, scale, naturalWidth)
      paintBadge(context, { x: stroke.region.x, y: stroke.region.y }, index, scale, naturalWidth)
    }
    for (const stroke of strokes) {
      if (isShapeStroke(stroke)) continue
      paintStroke(context, stroke, scale, naturalWidth)
    }
    const blob = await encode(canvas)
    if (blob.size <= MAX_COMPOSITE_BYTES || scale <= 0.25) return blob
    scale *= 0.7
  }
  return null
}

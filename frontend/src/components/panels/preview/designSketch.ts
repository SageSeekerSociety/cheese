import type { Point, RasterRegion } from './designRegion'
import type { SampledImage } from './designSnap'

/** 画布上的工具。`select` 不画东西，它框选要交给芝士的那一块。 */
export type SketchTool = 'select' | 'pen' | 'line' | 'arrow' | 'rect' | 'ellipse' | 'text' | 'redact'

/** 会往图上画东西的工具；`select` 不在其中，它由工具栏最左边那颗单独的按钮表示。 */
export const DRAW_TOOLS: readonly SketchTool[] = ['pen', 'line', 'arrow', 'rect', 'ellipse', 'text', 'redact']

/**
 * 涂黑（redact）的三种样式。参考物是**一个工具 + 一个样式字段**，不是三个工具：
 * 形状仍是 `{ tool: 'redact', region, redactStyle }`，缺省即实心。
 */
export type RedactStyle = 'solid' | 'mosaic' | 'noise'

/** 样式行里的顺序：实心、马赛克、噪点。 */
export const REDACT_STYLES: readonly RedactStyle[] = ['solid', 'mosaic', 'noise']

/** 涂黑底下那层纯黑。涂黑不看颜色，只铺这个。 */
export const REDACT_INK = '#000000'

/**
 * 和参考物同一套：红、蓝、绿、近黑，外加一个白。第一颗是默认选中的红。
 *
 * 参考物（Claude 桌面版那套标注器）就是这五个，白也在里面——白是画在黑底、深色
 * 照片上的那支笔，参考物没有为它单独配描边，这里也照抄，不给白加边。
 */
export const SKETCH_COLORS: readonly string[] = ['#E03131', '#1971C2', '#2F9E44', '#1F1E1D', '#FFFFFF']

export type PenStroke = { tool: 'pen'; color: string; width: number; points: Point[] }
export type LineStroke = { tool: 'line' | 'arrow'; color: string; width: number; from: Point; to: Point }
export type ShapeStroke = {
  tool: 'rect' | 'ellipse' | 'redact'
  color: string
  width: number
  region: RasterRegion
  /** 文字标注：画在框左上角的字。 */
  text?: string
  /**
   * 只对涂黑有意义：涂抹的样式，缺省即实心。涂黑虽然也带 `color`，但画的时候一律
   * 只认这个字段——颜色对涂黑是不可选的。
   */
  redactStyle?: RedactStyle
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

/** 图案 tile 的边长（像素）。参考物恒定 96，与区域大小无关。 */
export const REDACT_TILE = 96

/** 每种样式的方块边长；是常量，不随区域大小缩放。solid 不铺图案。 */
export const REDACT_BLOCK_STEP: Record<RedactStyle, number> = { solid: 0, mosaic: 12, noise: 2 }

/** 每种样式一方块取一个随机灰值的范围（闭区间）。 */
const REDACT_GREY: Record<'mosaic' | 'noise', readonly [number, number]> = {
  mosaic: [56, 200],
  noise: [16, 240],
}

/**
 * 一块 tile 里每个方块的灰值，按行铺开。抽出来是为了可测：tile 边长、方块步长、
 * 灰值范围都是参考物量出来的常量。
 */
export function redactBlocks(style: 'mosaic' | 'noise', random: () => number = Math.random): number[] {
  const step = REDACT_BLOCK_STEP[style]
  const [low, high] = REDACT_GREY[style]
  const per = Math.round(REDACT_TILE / step)
  const blocks: number[] = []
  for (let index = 0; index < per * per; index += 1) {
    blocks.push(low + Math.floor(random() * (high - low + 1)))
  }
  return blocks
}

/** 每种样式一块 tile，画一次留着复用（参考物也缓存）。 */
const tiles = new Map<RedactStyle, HTMLCanvasElement | null>()

/** 取（或造）某种样式的 96×96 图案 tile；solid 没有 tile。没有画布的宿主给 null。 */
export function redactTile(style: RedactStyle): HTMLCanvasElement | null {
  if (style === 'solid') return null
  if (tiles.has(style)) return tiles.get(style) ?? null
  let tile: HTMLCanvasElement | null = null
  try {
    const canvas = document.createElement('canvas')
    canvas.width = REDACT_TILE
    canvas.height = REDACT_TILE
    const context = typeof canvas.getContext === 'function' ? canvas.getContext('2d') : null
    if (context) {
      const step = REDACT_BLOCK_STEP[style]
      const blocks = redactBlocks(style)
      const per = Math.round(REDACT_TILE / step)
      for (let index = 0; index < blocks.length; index += 1) {
        const grey = blocks[index]
        context.fillStyle = `rgb(${grey}, ${grey}, ${grey})`
        context.fillRect((index % per) * step, Math.floor(index / per) * step, step, step)
      }
      tile = canvas
    }
  } catch {
    // 没有画布的宿主（jsdom、被禁用的画布）：没有图案，退回纯黑，不崩。
    tile = null
  }
  tiles.set(style, tile)
  return tile
}

/** CanvasPattern 按上下文缓存（参考物：tile 进 Map，pattern 进 WeakMap）。 */
const patterns = new WeakMap<CanvasRenderingContext2D, Map<RedactStyle, CanvasPattern>>()

function redactPattern(context: CanvasRenderingContext2D, style: RedactStyle): CanvasPattern | null {
  if (style === 'solid') return null
  let byStyle = patterns.get(context)
  if (!byStyle) {
    byStyle = new Map()
    patterns.set(context, byStyle)
  }
  const cached = byStyle.get(style)
  if (cached) return cached
  const tile = redactTile(style)
  if (!tile || typeof context.createPattern !== 'function') return null
  const pattern = context.createPattern(tile, 'repeat')
  if (pattern) byStyle.set(style, pattern)
  return pattern
}

/**
 * 图案相位：把区域左上角对 tile 取模，画图案时把上下文先平移这么多。
 *
 * 同一个区域重画（抖动、缩放后重绘）时相位不变，纹理不会一格一格地滑。
 */
export function redactAnchor(x: number, y: number, tile = REDACT_TILE): Point {
  const mod = (value: number) => ((Math.round(value) % tile) + tile) % tile
  return { x: mod(x), y: mod(y) }
}

export function paintStroke(
  context: CanvasRenderingContext2D,
  stroke: SketchStroke,
  scale: number,
  naturalWidth: number
) {
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
    // 涂黑先铺满纯黑：它要盖住东西，不是勾出东西。
    const box = line(stroke.region)
    context.fillStyle = REDACT_INK
    context.fillRect(box.x, box.y, box.width, box.height)
    const pattern = redactPattern(context, stroke.redactStyle ?? 'solid')
    if (pattern) {
      // 方块边缘要硬：关掉平滑；相位按左上角对 tile 取模，同一区域重画不滑。
      const anchor = redactAnchor(box.x, box.y)
      context.save()
      context.imageSmoothingEnabled = false
      context.translate(anchor.x, anchor.y)
      context.fillStyle = pattern
      context.fillRect(box.x - anchor.x, box.y - anchor.y, box.width, box.height)
      context.restore()
    }
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

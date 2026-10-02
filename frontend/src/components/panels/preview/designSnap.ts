import type { Point, RasterRegion } from './designRegion'

/** 一张缩到 ≤512 像素的取样图，投影分析只跑它，不跑原图。 */
export type SampledImage = { data: Uint8ClampedArray; width: number; height: number }

/** 内容与空白的分界线，坐标已换算回原图像素。 */
export type ContentProfile = { columns: number[]; rows: number[] }

/** 与背景色的距离超过它才算「墨」。 */
const INK_TOLERANCE = 32
/** 一条边吸附的默认容差（原图像素），约手掌拖动时的手抖幅度。 */
export const SNAP_TOLERANCE = 12

function at(data: Uint8ClampedArray, width: number, x: number, y: number) {
  const i = (y * width + x) * 4
  return [data[i], data[i + 1], data[i + 2]] as const
}

function distance(a: readonly number[], b: readonly number[]) {
  return Math.abs(a[0] - b[0]) + Math.abs(a[1] - b[1]) + Math.abs(a[2] - b[2])
}

/** 背景色取四角与四边中点的众数：一张图里页边空白通常比内容多。 */
function background(sample: SampledImage): [number, number, number] {
  const { data, width, height } = sample
  const spots: Point[] = [
    { x: 0, y: 0 },
    { x: width - 1, y: 0 },
    { x: 0, y: height - 1 },
    { x: width - 1, y: height - 1 },
    { x: (width - 1) >> 1, y: 0 },
    { x: (width - 1) >> 1, y: height - 1 },
    { x: 0, y: (height - 1) >> 1 },
    { x: width - 1, y: (height - 1) >> 1 },
  ]
  const votes = new Map<string, { count: number; color: [number, number, number] }>()
  for (const spot of spots) {
    const color = at(data, width, spot.x, spot.y)
    const key = color.join(',')
    const seen = votes.get(key)
    if (seen) seen.count += 1
    else votes.set(key, { count: 1, color: [color[0], color[1], color[2]] })
  }
  let best = { count: 0, color: [255, 255, 255] as [number, number, number] }
  for (const vote of votes.values()) if (vote.count > best.count) best = vote
  return best.color
}

/** 连续为「墨」的一段 [start, end)，转成两条分界线。 */
function boundaries(flags: readonly boolean[], scale: number, limit: number): number[] {
  const lines = new Set<number>()
  let runStart = -1
  for (let i = 0; i <= flags.length; i += 1) {
    const ink = i < flags.length && flags[i]
    if (ink && runStart < 0) runStart = i
    if (!ink && runStart >= 0) {
      lines.add(Math.round(runStart * scale))
      lines.add(Math.min(limit, Math.round(i * scale)))
      runStart = -1
    }
  }
  return [...lines].sort((a, b) => a - b)
}

/**
 * 逐列、逐行问「这里有没有内容」，把内容段的起止换算回原图坐标。
 *
 * 得到的是**内容块的边**：文字行、图片、表格各自成段，段与段之间是空白。
 * 拖动时吸附到这些线上，框出来的一般就是人眼想框的那一块。
 */
export function contentProfile(sample: SampledImage, naturalWidth: number, naturalHeight: number): ContentProfile {
  const { data, width, height } = sample
  if (!width || !height || naturalWidth <= 0 || naturalHeight <= 0) return { columns: [], rows: [] }
  const bg = background(sample)
  const columns = new Array<boolean>(width).fill(false)
  const rows = new Array<boolean>(height).fill(false)
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      // 边缘一圈常是扫描黑边或圆角阴影，不当作内容，免得每张图都吸到四边。
      const edge = x === 0 || y === 0 || x === width - 1 || y === height - 1
      if (edge) continue
      if (distance(at(data, width, x, y), bg) > INK_TOLERANCE) {
        columns[x] = true
        rows[y] = true
      }
    }
  }
  return {
    columns: boundaries(columns, naturalWidth / width, naturalWidth),
    rows: boundaries(rows, naturalHeight / height, naturalHeight),
  }
}

function nearest(value: number, lines: readonly number[], tolerance: number): number {
  let best = value
  let bestDistance = tolerance
  for (const line of lines) {
    const d = Math.abs(line - value)
    if (d <= bestDistance) {
      bestDistance = d
      best = line
    }
  }
  return best
}

/**
 * 把矩形四条边吸到最近的内容分界线上。
 *
 * 只有容差以内才吸；吸完不到一个像素就保持原样，免得把手画的细框吸没了。
 */
export function snapRegion(region: RasterRegion, profile: ContentProfile, tolerance = SNAP_TOLERANCE): RasterRegion {
  const left = nearest(region.x, profile.columns, tolerance)
  const right = nearest(region.x + region.width, profile.columns, tolerance)
  const top = nearest(region.y, profile.rows, tolerance)
  const bottom = nearest(region.y + region.height, profile.rows, tolerance)
  if (right - left < 1 || bottom - top < 1) return region
  return { x: left, y: top, width: right - left, height: bottom - top }
}

/**
 * 点落在哪个内容块里；点在空白处（或没有分界线）时给出整张图。
 *
 * 「点一下就把这块框住」靠的就是它——正文段落、单张插图都会各自成块。
 */
export function blockAt(
  point: Point,
  profile: ContentProfile,
  naturalWidth: number,
  naturalHeight: number
): RasterRegion {
  const whole: RasterRegion = { x: 0, y: 0, width: naturalWidth, height: naturalHeight }
  const span = (lines: readonly number[], value: number, limit: number): [number, number] => {
    if (lines.length < 2) return [0, limit]
    for (let i = 0; i + 1 < lines.length; i += 2) {
      const start = lines[i]
      const end = lines[i + 1]
      if (value >= start && value <= end) return [start, end]
    }
    return [0, limit]
  }
  const [left, right] = span(profile.columns, point.x, naturalWidth)
  const [top, bottom] = span(profile.rows, point.y, naturalHeight)
  if (right - left < 1 || bottom - top < 1) return whole
  return { x: left, y: top, width: right - left, height: bottom - top }
}

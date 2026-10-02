import type { Point, RasterRegion } from './designRegion'

/** 一张缩到 ≤512 像素的取样图，检测只跑它，不跑原图。 */
export type SampledImage = { data: Uint8ClampedArray; width: number; height: number }

/** 一块内容（一个词、一行）在原图上的框。 */
export type ContentBox = RasterRegion

/**
 * 内容框（点一下、悬停用）和内容分界线（拖动吸附用），坐标都是原图像素。
 *
 * `boxes` 按面积从小到大排：点一下先命中里面最小的那个框，也就是一个词；
 * 词与词之间的空隙会命中包含它的那一行。
 */
export type ContentProfile = { columns: number[]; rows: number[]; boxes: ContentBox[] }

/**
 * 「这里有边」的判据：相邻像素灰度差的绝对值之和。
 *
 * 早先那版是拿八个边框采样点的众数当背景色、再问每个像素离背景多远。
 * 满幅截图（深色顶栏/侧栏/底栏贴边）里众数就是深色，于是白底内容区反被
 * 判成墨、深色边框反被判成背景，三行文字在算法里整块消失。梯度不看颜色
 * 属于谁，只要有边就有墨，那种图不再有这个问题。
 */
const GRADIENT = 28
/** 连通域小于这么多像素（采样图尺度）就当噪声丢掉。 */
const MIN_AREA = 4
/** 两块合成为一个词：水平缺口不超过它，且竖直方向重叠够多。 */
const WORD_GAP = 3
/** 两个词合成为一行：水平缺口不超过它。 */
const LINE_GAP = 10
/** 上下两条同宽的细边合成一块时的最大间距（采样图像素）。 */
const STACK_GAP = 24
/** 判「同宽」允许的偏差（采样图像素）。 */
const STACK_EXTENT = 3
/** 连通域多到离谱时（照片、噪点图）只保留最大的这些个，免得框到天荒地老。 */
const MAX_COMPONENTS = 600
/** 命中框四周留出来的余量（原图像素）。 */
export const BOX_PADDING = 2
/** 一条边吸附的默认容差（原图像素），约手掌拖动时的抖动幅度。 */
export const SNAP_TOLERANCE = 12

function luma(data: Uint8ClampedArray, width: number, x: number, y: number) {
  const i = (y * width + x) * 4
  return 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2]
}

/**
 * 有边的像素置 1：相邻两个像素的灰度差超过阈值时，把**暗的那一个**标上。
 *
 * 标暗的那一侧而不是固定标左边那个，是为了让一圈边落在字形/色块的轮廓上，
 * 而不是整体偏出一个像素——分界线要能直接拿去吸附。
 */
function edgeMask(data: Uint8ClampedArray, width: number, height: number): Uint8Array {
  const mask = new Uint8Array(width * height)
  const dark = (a: number, b: number, ia: number, ib: number) => {
    if (Math.abs(a - b) < GRADIENT) return
    mask[a <= b ? ia : ib] = 1
  }
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const here = luma(data, width, x, y)
      const i = y * width + x
      if (x + 1 < width) dark(here, luma(data, width, x + 1, y), i, i + 1)
      if (y + 1 < height) dark(here, luma(data, width, x, y + 1), i, i + width)
    }
  }
  return mask
}

/** 连通域的外接框，按面积从大到小；面积太小的先丢掉。 */
function components(mask: Uint8Array, width: number, height: number): RasterRegion[] {
  const parent = new Int32Array(width * height).fill(-1)
  const find = (index: number): number => {
    let root = index
    while (parent[root] !== root) root = parent[root]
    let walk = index
    while (parent[walk] !== root) {
      const next = parent[walk]
      parent[walk] = root
      walk = next
    }
    return root
  }
  const union = (a: number, b: number) => {
    const ra = find(a)
    const rb = find(b)
    if (ra !== rb) parent[rb] = ra
  }
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const i = y * width + x
      if (!mask[i]) continue
      parent[i] = i
      if (x > 0 && mask[i - 1]) union(i, i - 1)
      if (y > 0 && mask[i - width]) union(i, i - width)
      // 斜对角也算连着：细笔画在一格网格上会断成一节一节。
      if (x > 0 && y > 0 && mask[i - width - 1]) union(i, i - width - 1)
      if (x + 1 < width && y > 0 && mask[i - width + 1]) union(i, i - width + 1)
    }
  }
  const boxes = new Map<number, { x0: number; y0: number; x1: number; y1: number; area: number }>()
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const i = y * width + x
      if (!mask[i]) continue
      const root = find(i)
      const seen = boxes.get(root)
      if (!seen) boxes.set(root, { x0: x, y0: y, x1: x + 1, y1: y + 1, area: 1 })
      else {
        seen.x0 = Math.min(seen.x0, x)
        seen.y0 = Math.min(seen.y0, y)
        seen.x1 = Math.max(seen.x1, x + 1)
        seen.y1 = Math.max(seen.y1, y + 1)
        seen.area += 1
      }
    }
  }
  return [...boxes.values()]
    .filter((b) => b.area >= MIN_AREA)
    .sort((a, b) => b.area - a.area)
    .slice(0, MAX_COMPONENTS)
    .map((b) => ({ x: b.x0, y: b.y0, width: b.x1 - b.x0, height: b.y1 - b.y0 }))
}

/** 细到这个高度（采样图像素）的框当成一条「边」，而不是一块内容。 */
const STRIP = 2

/**
 * 把上下两条同宽的细边两两合成一块。
 *
 * 一条横贯整幅图的色带（顶栏、页脚、分隔行）左右都顶到图边，没有竖边可连，
 * 于是只剩上下两条 1 像素的线。不合成的话点它只会框出那条线。
 * 只配一次对，不连锁——不然两条相邻的色带会被并成一整块。
 */
function pairStrips(boxes: readonly RasterRegion[], gap: number, extent: number): RasterRegion[] {
  const thick = boxes.filter((box) => box.height > STRIP)
  const strips = boxes.filter((box) => box.height <= STRIP).sort((a, b) => a.y - b.y)
  const paired = new Set<number>()
  const out = [...thick]
  for (let i = 0; i < strips.length; i += 1) {
    if (paired.has(i)) continue
    const top = strips[i]
    let mate = -1
    for (let j = i + 1; j < strips.length; j += 1) {
      if (paired.has(j)) continue
      const rest = strips[j]
      const room = rest.y - (top.y + top.height)
      if (room > gap) break
      if (room >= 0 && Math.abs(top.x - rest.x) <= extent && Math.abs(top.width - rest.width) <= extent) {
        mate = j
        break
      }
    }
    if (mate < 0) out.push(top)
    else {
      paired.add(mate)
      out.push(cover(top, strips[mate]))
    }
  }
  return out
}

function cover(a: RasterRegion, b: RasterRegion): RasterRegion {
  const x = Math.min(a.x, b.x)
  const y = Math.min(a.y, b.y)
  return {
    x,
    y,
    width: Math.max(a.x + a.width, b.x + b.width) - x,
    height: Math.max(a.y + a.height, b.y + b.height) - y,
  }
}

/**
 * 把水平方向挨着的框合成一个：竖直重叠够多（同一行）、水平只有一个窄缺口。
 *
 * 跑两遍——先按小的缺口合出「词」，再用大的缺口把词合成「行」。
 * 缺口必须为正：一个框包住另一个时不许合，否则整块底板会把里面每一行都吞掉。
 */
function mergeAcross(boxes: readonly RasterRegion[], gap: number): RasterRegion[] {
  const rest = [...boxes]
  const merged: RasterRegion[] = []
  let current = rest.shift()
  while (current) {
    let grew = true
    while (grew) {
      grew = false
      for (let i = 0; i < rest.length; i += 1) {
        const other = rest[i]
        const overlap = Math.min(current.y + current.height, other.y + other.height) - Math.max(current.y, other.y)
        const gapX = Math.max(current.x, other.x) - Math.min(current.x + current.width, other.x + other.width)
        if (overlap >= Math.min(current.height, other.height) * 0.5 && gapX >= 0 && gapX <= gap) {
          current = cover(current, other)
          rest.splice(i, 1)
          grew = true
          break
        }
      }
    }
    merged.push(current)
    current = rest.shift()
  }
  return merged
}

/** 所有框的同一条边，去重排序后就是可以吸上去的分界线。 */
function edges(boxes: readonly RasterRegion[], limit: number, vertical: boolean): number[] {
  const lines = new Set<number>()
  for (const box of boxes) {
    const start = vertical ? box.x : box.y
    const size = vertical ? box.width : box.height
    lines.add(Math.max(0, Math.min(limit, Math.round(start))))
    lines.add(Math.max(0, Math.min(limit, Math.round(start + size))))
  }
  return [...lines].sort((a, b) => a - b)
}

/**
 * 找出图里的内容框：梯度 → 连通域 → 词 → 行。
 *
 * `columns` / `rows` 取自行框（粗一些，拖动吸附用它），`boxes` 是词框和行框
 * （点一下、悬停用），都换算回原图像素。
 */
export function contentProfile(sample: SampledImage, naturalWidth: number, naturalHeight: number): ContentProfile {
  const empty: ContentProfile = { columns: [], rows: [], boxes: [] }
  const { data, width, height } = sample
  if (!width || !height || naturalWidth <= 0 || naturalHeight <= 0) return empty
  const found = pairStrips(components(edgeMask(data, width, height), width, height), STACK_GAP, STACK_EXTENT)
  if (!found.length) return empty
  const words = mergeAcross(found, WORD_GAP)
  const lines = mergeAcross(words, LINE_GAP)
  const sx = naturalWidth / width
  const sy = naturalHeight / height
  const toNatural = (box: RasterRegion, pad: number): RasterRegion => {
    const x = Math.max(0, Math.round(box.x * sx) - pad)
    const y = Math.max(0, Math.round(box.y * sy) - pad)
    return {
      x,
      y,
      width: Math.min(naturalWidth - x, Math.round(box.width * sx) + pad * 2),
      height: Math.min(naturalHeight - y, Math.round(box.height * sy) + pad * 2),
    }
  }
  const lineBoxes = lines.map((box) => toNatural(box, 0))
  return {
    columns: edges(lineBoxes, naturalWidth, true),
    rows: edges(lineBoxes, naturalHeight, false),
    boxes: hitBoxes(words, lines, toNatural),
  }
}

/** 去重（词框和行框常常是同一块），并按面积从小到大排。 */
function hitBoxes(
  words: readonly RasterRegion[],
  lines: readonly RasterRegion[],
  toNatural: (box: RasterRegion, pad: number) => RasterRegion
): ContentBox[] {
  const seen = new Map<string, ContentBox>()
  for (const box of [...words, ...lines].map((box) => toNatural(box, BOX_PADDING))) {
    if (box.width <= 0 || box.height <= 0) continue
    seen.set(`${box.x},${box.y},${box.width},${box.height}`, box)
  }
  return [...seen.values()].sort((a, b) => a.width * a.height - b.width * b.height)
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
 * 点落在哪个内容框里——面积最小的那个，也就是一个词；词与词之间的空隙
 * 落到包含它的那一行。点在空白处（或没有框）时给出整张图。
 */
export function blockAt(
  point: Point,
  profile: ContentProfile,
  naturalWidth: number,
  naturalHeight: number
): RasterRegion {
  for (const box of profile.boxes) {
    if (point.x >= box.x && point.x <= box.x + box.width && point.y >= box.y && point.y <= box.y + box.height)
      return box
  }
  return { x: 0, y: 0, width: naturalWidth, height: naturalHeight }
}

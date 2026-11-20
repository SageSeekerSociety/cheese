// 看图浮层里对一张图做的调整：旋转、翻转、亮度、饱和度、裁剪。
//
// 屏幕上用 CSS 画（transform、filter、clip-path），导出时在画布上按同一套数重画一遍，
// 读者看到的就是存下来的。亮度和饱和度导出时逐像素算，不用画布的 `filter`：Safari
// 不认它，用了的话同一张图在两个浏览器里存出来不一样。

/** 裁剪框：在**转好、翻好之后**看到的那张图上，按宽高的比例记（0–1）。 */
export interface CropBox {
  x: number
  y: number
  w: number
  h: number
}

export interface ImageAdjust {
  /** 顺时针转了几度：0、90、180、270。 */
  rotate: number
  /** 左右翻过来没有。 */
  flip: boolean
  /** 百分比，100 是原样。 */
  brightness: number
  saturation: number
  crop: CropBox | null
}

export const ORIGINAL: Readonly<ImageAdjust> = { rotate: 0, flip: false, brightness: 100, saturation: 100, crop: null }

export function isEdited(adjust: ImageAdjust): boolean {
  return (
    adjust.rotate % 360 !== 0 ||
    adjust.flip ||
    adjust.brightness !== 100 ||
    adjust.saturation !== 100 ||
    adjust.crop !== null
  )
}

/** 转了 90° 或 270°：看到的宽高和原图对调。 */
export function sideways(adjust: Pick<ImageAdjust, 'rotate'>): boolean {
  return adjust.rotate % 180 !== 0
}

export function cssFilter(adjust: ImageAdjust): string | undefined {
  if (adjust.brightness === 100 && adjust.saturation === 100) return undefined
  return `brightness(${adjust.brightness}%) saturate(${adjust.saturation}%)`
}

type Pt = { x: number; y: number }

/** 换算只用得上这两样：亮度、饱和度、裁剪都不挪点位。`sideways` 也收这一个。 */
type Orientation = Pick<ImageAdjust, 'rotate' | 'flip'>

/** 原图上的一点（比例坐标）在转好、翻好之后落在哪：先翻，再顺时针转。 */
function forward(p: Pt, adjust: Orientation): Pt {
  let q = adjust.flip ? { x: 1 - p.x, y: p.y } : p
  for (let turns = (((adjust.rotate / 90) % 4) + 4) % 4; turns > 0; turns -= 1) q = { x: 1 - q.y, y: q.x }
  return q
}

/** 反过来：看到的那一点在原图上是哪一点。 */
function backward(p: Pt, adjust: Orientation): Pt {
  let q = p
  for (let turns = (((adjust.rotate / 90) % 4) + 4) % 4; turns > 0; turns -= 1) q = { x: q.y, y: 1 - q.x }
  return adjust.flip ? { x: 1 - q.x, y: q.y } : q
}

/** 原图上的一点（按宽高的比例）落在**转好、翻好之后**看到的哪儿。 */
export function displayPoint(p: Pt, adjust: Orientation): Pt {
  return forward(p, adjust)
}

/** 看到的这一点在原图上是哪一点——画布上按原图像素记的笔画和框选，都靠它换算。 */
export function sourcePoint(p: Pt, adjust: Orientation): Pt {
  return backward(p, adjust)
}

/** 裁剪框换到原图（没转没翻的那张）上：clip-path 量的是元素自己，不是转过之后的样子。 */
export function cropOnSource(crop: CropBox, adjust: ImageAdjust): CropBox {
  const a = backward({ x: crop.x, y: crop.y }, adjust)
  const b = backward({ x: crop.x + crop.w, y: crop.y + crop.h }, adjust)
  const x = Math.min(a.x, b.x)
  const y = Math.min(a.y, b.y)
  return { x, y, w: Math.abs(a.x - b.x), h: Math.abs(a.y - b.y) }
}

/** 旋转或翻转之后，原来那个裁剪框跟着图走（框住的还是同一块）。 */
export function carryCrop(crop: CropBox, from: ImageAdjust, to: ImageAdjust): CropBox {
  const source = cropOnSource(crop, from)
  const a = forward({ x: source.x, y: source.y }, to)
  const b = forward({ x: source.x + source.w, y: source.y + source.h }, to)
  return { x: Math.min(a.x, b.x), y: Math.min(a.y, b.y), w: Math.abs(a.x - b.x), h: Math.abs(a.y - b.y) }
}

export function cssClip(adjust: ImageAdjust): string | undefined {
  if (!adjust.crop) return undefined
  const c = cropOnSource(adjust.crop, adjust)
  const pct = (v: number) => `${Math.max(0, Math.round(v * 10000) / 100)}%`
  return `inset(${pct(c.y)} ${pct(1 - c.x - c.w)} ${pct(1 - c.y - c.h)} ${pct(c.x)})`
}

/**
 * 亮度、饱和度逐像素改，系数照 CSS Filter Effects 规范里 brightness()、saturate()
 * 的那两个矩阵——屏幕上是浏览器按它画的，存出来要一样。
 */
export function tone(data: Uint8ClampedArray, brightness: number, saturation: number): void {
  const b = brightness / 100
  const s = saturation / 100
  const m = [
    0.213 + 0.787 * s,
    0.715 - 0.715 * s,
    0.072 - 0.072 * s,
    0.213 - 0.213 * s,
    0.715 + 0.285 * s,
    0.072 - 0.072 * s,
    0.213 - 0.213 * s,
    0.715 - 0.715 * s,
    0.072 + 0.928 * s,
  ]
  for (let i = 0; i < data.length; i += 4) {
    const r = data[i] * b
    const g = data[i + 1] * b
    const bl = data[i + 2] * b
    data[i] = m[0] * r + m[1] * g + m[2] * bl
    data[i + 1] = m[3] * r + m[4] * g + m[5] * bl
    data[i + 2] = m[6] * r + m[7] * g + m[8] * bl
  }
}

/** 导出的边长上限：和面板里涂画合成图同一条线，上传通道收得下。 */
const MAX_EDGE = 4096

/** 按调整重画出一张新图（PNG）。原图不动。 */
export async function renderAdjusted(image: HTMLImageElement, adjust: ImageAdjust): Promise<Blob> {
  const fit = Math.min(1, MAX_EDGE / Math.max(image.naturalWidth, image.naturalHeight))
  const w = Math.round(image.naturalWidth * fit)
  const h = Math.round(image.naturalHeight * fit)
  const turned = sideways(adjust)
  const full = document.createElement('canvas')
  full.width = turned ? h : w
  full.height = turned ? w : h
  const ctx = full.getContext('2d')
  if (!ctx) throw new Error('canvas unavailable')
  // CSS 的 `rotate() scaleX()` 是先翻再转；画布上的变换也按这个顺序作用到点上。
  ctx.translate(full.width / 2, full.height / 2)
  ctx.rotate((adjust.rotate * Math.PI) / 180)
  ctx.scale(adjust.flip ? -1 : 1, 1)
  ctx.drawImage(image, -w / 2, -h / 2, w, h)

  const crop = adjust.crop ?? { x: 0, y: 0, w: 1, h: 1 }
  const out = document.createElement('canvas')
  out.width = Math.max(1, Math.round(crop.w * full.width))
  out.height = Math.max(1, Math.round(crop.h * full.height))
  const octx = out.getContext('2d')
  if (!octx) throw new Error('canvas unavailable')
  octx.drawImage(full, -Math.round(crop.x * full.width), -Math.round(crop.y * full.height))
  if (adjust.brightness !== 100 || adjust.saturation !== 100) {
    const pixels = octx.getImageData(0, 0, out.width, out.height)
    tone(pixels.data, adjust.brightness, adjust.saturation)
    octx.putImageData(pixels, 0, 0)
  }
  return new Promise((resolve, reject) =>
    out.toBlob((blob) => (blob ? resolve(blob) : reject(new Error('toBlob failed'))), 'image/png')
  )
}

/** 存下来的新图叫什么：原名后面接一个说明（如「编辑」），扩展名换成 png。 */
export function editedName(name: string, suffix: string): string {
  const dot = name.lastIndexOf('.')
  const base = dot > 0 ? name.slice(0, dot) : name
  return `${base}-${suffix}.png`
}

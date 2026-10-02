import type { SampledImage } from './designSnap'

import { describe, expect, it } from 'vitest'

import { blockAt, contentProfile, SNAP_TOLERANCE, snapRegion } from './designSnap'

/** 一块黑区在采样图上占哪里；其余是白的。 */
type Ink = (x: number, y: number) => boolean

/** 造一张纯白取样图，`ink` 说哪一处是内容。像素只有黑白两色，背景就是白的。 */
function sample(width: number, height: number, ink?: Ink): SampledImage {
  const data = new Uint8ClampedArray(width * height * 4)
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const i = (y * width + x) * 4
      const value = ink?.(x, y) ? 0 : 255
      data[i] = value
      data[i + 1] = value
      data[i + 2] = value
      data[i + 3] = 255
    }
  }
  return { data, width, height }
}

/** 半开区间 [x0, x1) × [y0, y1) 上的内容块。 */
function rectangle(x0: number, y0: number, x1: number, y1: number): Ink {
  return (x, y) => x >= x0 && x < x1 && y >= y0 && y < y1
}

function everything(...shapes: Ink[]): Ink {
  return (x, y) => shapes.some((shape) => shape(x, y))
}

describe('contentProfile', () => {
  it('boxes a lone block and puts its edges on the content lines', () => {
    const profile = contentProfile(sample(100, 100, rectangle(20, 30, 40, 50)), 100, 100)
    expect(profile.columns).toEqual([20, 40])
    expect(profile.rows).toEqual([30, 50])
    // 命中框四周留 2px，所以是 18,28 起、24 见方。
    expect(profile.boxes).toEqual([{ x: 18, y: 28, width: 24, height: 24 }])
  })

  it('maps a downscaled sample back onto the natural image', () => {
    // 取样是 50×50，原图 100×100：分界线要乘回 2，落在原图坐标上。
    const profile = contentProfile(sample(50, 50, rectangle(10, 15, 20, 25)), 100, 100)
    expect(profile.columns).toEqual([20, 40])
    expect(profile.rows).toEqual([30, 50])
    expect(profile.boxes).toEqual([{ x: 18, y: 28, width: 24, height: 24 }])
  })

  it('returns nothing for a blank image', () => {
    expect(contentProfile(sample(40, 40), 40, 40)).toEqual({ columns: [], rows: [], boxes: [] })
  })

  it('returns nothing for a degenerate sample or a zero natural size', () => {
    expect(contentProfile(sample(0, 0), 100, 100)).toEqual({ columns: [], rows: [], boxes: [] })
    expect(contentProfile(sample(20, 20, rectangle(5, 5, 15, 15)), 0, 100)).toEqual({
      columns: [],
      rows: [],
      boxes: [],
    })
  })

  it('keeps two stacked bands apart', () => {
    // 两条横贯整幅图的色带：左右都顶到图边、没有竖边可连，只能靠上下两条线配对。
    const ink: Ink = (x, y) => (y >= 10 && y < 20) || (y >= 30 && y < 40)
    const profile = contentProfile(sample(40, 50, ink), 40, 50)
    expect(profile.rows).toEqual([10, 20, 30, 40])
    expect(profile.boxes.every((box) => box.height <= 14)).toBe(true)
  })

  /**
   * 满幅截图：深色顶栏、左右侧栏、底栏贴边，中间白底上三行字。
   *
   * 旧算法在这里整块失效——它拿八个边框采样点的众数当背景色，八个点全落在
   * 深色边框上，于是白底内容区反被判成「墨」，三行字在算法里根本不存在，
   * 点哪儿都只框到整个内容区。梯度不看颜色属于谁，所以不再有这个问题。
   */
  describe('a screenshot whose dark chrome touches every border', () => {
    const chrome = everything(
      rectangle(0, 0, 80, 6),
      rectangle(0, 74, 80, 80),
      rectangle(0, 0, 6, 80),
      rectangle(74, 0, 80, 80)
    )
    const lines = everything(rectangle(12, 20, 60, 26), rectangle(12, 34, 60, 40), rectangle(12, 48, 60, 54))
    const profile = contentProfile(sample(80, 80, everything(chrome, lines)), 80, 80)

    it('finds one box per line of text', () => {
      expect(profile.boxes).toContainEqual({ x: 10, y: 18, width: 52, height: 10 })
      expect(profile.boxes).toContainEqual({ x: 10, y: 32, width: 52, height: 10 })
      expect(profile.boxes).toContainEqual({ x: 10, y: 46, width: 52, height: 10 })
    })

    it('frames the line under the pointer, not the whole content area', () => {
      // 旧算法在这里给的是 [6,74] 那一大片，点哪一行都一个样。
      expect(blockAt({ x: 30, y: 36 }, profile, 80, 80)).toEqual({ x: 10, y: 32, width: 52, height: 10 })
    })
  })
})

describe('snapRegion', () => {
  const profile = { columns: [100, 300], rows: [100, 300], boxes: [] }

  it('pulls a near-miss edge onto the content line', () => {
    expect(snapRegion({ x: 110, y: 94, width: 186, height: 212 }, profile)).toEqual({
      x: 100,
      y: 100,
      width: 200,
      height: 200,
    })
  })

  it('leaves an edge alone when no line is within tolerance', () => {
    const region = { x: 0, y: 0, width: 50, height: 50 }
    expect(snapRegion(region, profile)).toEqual(region)
  })

  it('never collapses a region by snapping both edges onto one line', () => {
    const region = { x: 100, y: 100, width: 6, height: 6 }
    expect(snapRegion(region, profile)).toEqual(region)
  })

  it('uses a hand-sized default tolerance', () => {
    expect(SNAP_TOLERANCE).toBe(12)
  })
})

describe('blockAt', () => {
  it('returns the block the point falls in', () => {
    const profile = { columns: [], rows: [], boxes: [{ x: 100, y: 100, width: 200, height: 60 }] }
    expect(blockAt({ x: 150, y: 130 }, profile, 1000, 500)).toEqual({ x: 100, y: 100, width: 200, height: 60 })
  })

  it('picks the smallest box when they nest', () => {
    // 词在行里面：点到一个词就框那个词，不框整行。
    const profile = {
      columns: [],
      rows: [],
      boxes: [
        { x: 120, y: 110, width: 40, height: 20 },
        { x: 100, y: 100, width: 400, height: 60 },
      ],
    }
    expect(blockAt({ x: 130, y: 120 }, profile, 1000, 500)).toEqual({ x: 120, y: 110, width: 40, height: 20 })
    expect(blockAt({ x: 300, y: 120 }, profile, 1000, 500)).toEqual({ x: 100, y: 100, width: 400, height: 60 })
  })

  it('falls back to the whole image when the point is in the whitespace', () => {
    const profile = { columns: [], rows: [], boxes: [{ x: 100, y: 100, width: 200, height: 60 }] }
    expect(blockAt({ x: 20, y: 20 }, profile, 1000, 500)).toEqual({ x: 0, y: 0, width: 1000, height: 500 })
  })

  it('falls back to the whole image when there is no box at all', () => {
    expect(blockAt({ x: 200, y: 200 }, { columns: [], rows: [], boxes: [] }, 1000, 500)).toEqual({
      x: 0,
      y: 0,
      width: 1000,
      height: 500,
    })
  })
})

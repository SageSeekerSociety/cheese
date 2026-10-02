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

describe('contentProfile', () => {
  it('turns the content bands into lines in natural pixels', () => {
    const profile = contentProfile(sample(100, 100, rectangle(20, 30, 40, 50)), 100, 100)
    expect(profile).toEqual({ columns: [20, 40], rows: [30, 50] })
  })

  it('maps a downscaled sample back onto the natural image', () => {
    // 取样是 50×50，原图 100×100：分界线要乘回 2，落在原图坐标上。
    const profile = contentProfile(sample(50, 50, rectangle(10, 15, 20, 25)), 100, 100)
    expect(profile).toEqual({ columns: [20, 40], rows: [30, 50] })
  })

  it('returns no lines for a blank image', () => {
    expect(contentProfile(sample(40, 40), 40, 40)).toEqual({ columns: [], rows: [] })
  })

  it('returns no lines for a degenerate sample or a zero natural size', () => {
    expect(contentProfile(sample(0, 0), 100, 100)).toEqual({ columns: [], rows: [] })
    expect(contentProfile(sample(20, 20, rectangle(5, 5, 15, 15)), 0, 100)).toEqual({ columns: [], rows: [] })
  })

  it('keeps two stacked bands apart', () => {
    const ink: Ink = (x, y) => (y >= 10 && y < 20) || (y >= 30 && y < 40)
    const profile = contentProfile(sample(40, 50, ink), 40, 50)
    expect(profile.rows).toEqual([10, 20, 30, 40])
  })
})

describe('snapRegion', () => {
  const profile = { columns: [100, 300], rows: [100, 300] }

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
  const profile = { columns: [100, 300], rows: [100, 300] }

  it('returns the content block the point falls in', () => {
    expect(blockAt({ x: 200, y: 200 }, profile, 1000, 500)).toEqual({ x: 100, y: 100, width: 200, height: 200 })
  })

  it('picks the right block when blocks stack', () => {
    const stacked = { columns: [0, 1000], rows: [0, 100, 200, 300] }
    expect(blockAt({ x: 500, y: 250 }, stacked, 1000, 500)).toEqual({ x: 0, y: 200, width: 1000, height: 100 })
  })

  it('falls back to the whole image when the point is in the whitespace', () => {
    expect(blockAt({ x: 20, y: 20 }, profile, 1000, 500)).toEqual({ x: 0, y: 0, width: 1000, height: 500 })
  })

  it('falls back to the whole image when there is no profile at all', () => {
    expect(blockAt({ x: 200, y: 200 }, { columns: [], rows: [] }, 1000, 500)).toEqual({
      x: 0,
      y: 0,
      width: 1000,
      height: 500,
    })
  })
})

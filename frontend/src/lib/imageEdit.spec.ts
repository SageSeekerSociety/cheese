// 看图浮层的调整：屏幕上裁的那一块，换算到原图上、再跟着旋转翻转走，框住的始终是同一块；
// 亮度饱和度都是 100% 时像素一个不动。
import { describe, expect, it } from 'vitest'

import { carryCrop, cropOnSource, displayPoint, type ImageAdjust, isEdited, ORIGINAL, sourcePoint, tone } from './imageEdit'

const at = (over: Partial<ImageAdjust>): ImageAdjust => ({ ...ORIGINAL, ...over })
const close = (a: object, b: object) => {
  for (const [k, v] of Object.entries(b)) expect((a as Record<string, number>)[k]).toBeCloseTo(v as number, 6)
}

describe('crop box', () => {
  it('is the same box on the source when nothing is turned', () => {
    close(cropOnSource({ x: 0.1, y: 0.2, w: 0.3, h: 0.4 }, ORIGINAL), { x: 0.1, y: 0.2, w: 0.3, h: 0.4 })
  })

  it('after a quarter turn clockwise, the top-left of the view is the bottom-left of the source', () => {
    // 转了 90° 看到的左上角一小块，是原图左下角那一块。
    close(cropOnSource({ x: 0, y: 0, w: 0.25, h: 0.5 }, at({ rotate: 90 })), { x: 0, y: 0.75, w: 0.5, h: 0.25 })
  })

  it('a flipped view’s left edge is the source’s right edge', () => {
    close(cropOnSource({ x: 0, y: 0, w: 0.2, h: 1 }, at({ flip: true })), { x: 0.8, y: 0, w: 0.2, h: 1 })
  })

  it('keeps framing the same part of the picture when it is turned or flipped afterwards', () => {
    const box = { x: 0.1, y: 0.2, w: 0.3, h: 0.4 }
    for (const to of [at({ rotate: 90 }), at({ rotate: 270, flip: true }), at({ flip: true })]) {
      close(cropOnSource(carryCrop(box, ORIGINAL, to), to), cropOnSource(box, ORIGINAL))
    }
  })
})

it('leaves pixels alone at 100% brightness and saturation', () => {
  const data = new Uint8ClampedArray([10, 120, 250, 255, 0, 0, 0, 255])
  tone(data, 100, 100)
  expect([...data]).toEqual([10, 120, 250, 255, 0, 0, 0, 255])
})

it('turns a colour grey at 0% saturation', () => {
  const data = new Uint8ClampedArray([200, 40, 40, 255])
  tone(data, 100, 0)
  expect(data[0]).toBe(data[1])
  expect(data[1]).toBe(data[2])
})

it('only counts as edited once something differs from the original', () => {
  expect(isEdited(ORIGINAL)).toBe(false)
  expect(isEdited(at({ rotate: 360 }))).toBe(false)
  expect(isEdited(at({ brightness: 110 }))).toBe(true)
})

// 面板里框选、画笔、评论标记一律按**原图像素**记；图上转过翻过之后，屏幕上的一点要先折回
// 原图才是那个坐标。这两下互为反操作——只对一边，转 90° 之后框出来的位置会整体错开。
describe('view point ↔ source point', () => {
  const p = { x: 0.3, y: 0.8 }

  it('leaves a point where it is when nothing is turned', () => {
    close(sourcePoint(p, ORIGINAL), p)
    close(displayPoint(p, ORIGINAL), p)
  })

  it('round-trips through every turn, flip and their combination', () => {
    for (const adjust of [
      at({ rotate: 90 }),
      at({ rotate: 180 }),
      at({ rotate: 270 }),
      at({ flip: true }),
      at({ rotate: 90, flip: true }),
      at({ rotate: 270, flip: true }),
    ]) {
      close(sourcePoint(displayPoint(p, adjust), adjust), p)
      close(displayPoint(sourcePoint(p, adjust), adjust), p)
    }
  })

  it('names the same corner that cropOnSource names, after a quarter turn', () => {
    // 转 90° 之后看到的左上角，是原图的左下角——和裁剪那条用同一套坐标（见上面的 crop box 组）。
    close(sourcePoint({ x: 0, y: 0 }, at({ rotate: 90 })), { x: 0, y: 1 })
  })
})

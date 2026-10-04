/** content-visibility 的测量帧：挂上 MEASURE_CLASS 让离屏内容按真实高度铺开，
 *  量完要在**画过一帧之后**再撤（见 lib/contentVisibility 里的理由）。
 *  这里锁住三件：挂上、撤销发生在两个 rAF 之后（不是同步）、空元素不炸。 */
import { afterEach, describe, expect, it, vi } from 'vitest'

import { beginMeasuredLayout, endMeasuredLayout, MEASURE_CLASS } from './contentVisibility'

/** 手摇的 rAF 队列：把每一帧的回调收起来，由测试决定什么时候跑。 */
function frameQueue() {
  const frames: FrameRequestCallback[] = []
  vi.spyOn(globalThis, 'requestAnimationFrame').mockImplementation((cb) => {
    frames.push(cb)
    return frames.length
  })
  const runFrame = () => frames.shift()?.(0)
  return { frames, runFrame }
}

describe('contentVisibility 测量帧', () => {
  afterEach(() => vi.restoreAllMocks())

  it('begin 给滚动容器挂上测量类', () => {
    const box = document.createElement('div')
    beginMeasuredLayout(box)
    expect(box.classList.contains(MEASURE_CLASS)).toBe(true)
  })

  it('end 不立刻撤：两个 rAF 之后才撤，中间那一帧仍按真实高度画', () => {
    const box = document.createElement('div')
    beginMeasuredLayout(box)
    const { frames, runFrame } = frameQueue()

    endMeasuredLayout(box)
    // 同步这一下还在测量帧里 —— 这一帧会被布局 + 绘制，auto 才记得下真实高度。
    expect(box.classList.contains(MEASURE_CLASS)).toBe(true)
    expect(frames).toHaveLength(1)

    runFrame() // 第二帧（绘制之后）才排撤销
    expect(box.classList.contains(MEASURE_CLASS)).toBe(true)

    runFrame()
    expect(box.classList.contains(MEASURE_CLASS)).toBe(false)
  })

  it('空元素不炸', () => {
    expect(() => beginMeasuredLayout(null)).not.toThrow()
    expect(() => endMeasuredLayout(null)).not.toThrow()
  })
})

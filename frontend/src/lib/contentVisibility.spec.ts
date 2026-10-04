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

  /** 「用了测量帧的页面量到的是真实高度，不是估计值」。
   *
   *  一页里离屏跳过的内容（时间线的 .notice-row、文档视图的 .memory-card……
   *  content-visibility: auto + contain-intrinsic-size: auto <h>）没挂测量帧时只报
   *  那个估计高度；要按真实高度算落点的地方（向上翻页补偿、scrollIntoView）必须在
   *  测量帧里量。这里用一个假页面把这条锁住：行在帧里报真实高度、帧外报估计；测量帧
   *  量到真实、且两个 rAF 之后才撤（先让这一帧按真实高度画一次）。 */
  it('页面在测量帧里量到的是真实高度，不是估计值', () => {
    const classes = new Set<string>()
    const scroller = {
      classList: {
        add: (c: string) => void classes.add(c),
        remove: (c: string) => void classes.delete(c),
        contains: (c: string) => classes.has(c),
      },
    } as unknown as HTMLElement

    // 两行：离屏报 contain-intrinsic-size 里的估计，测量帧里按真实高度铺开。
    const rows = [
      { est: 28, real: 96 },
      { est: 20, real: 140 },
    ]
    // 某一行的落点 = 它前面那些行的高度之和（scrollIntoView 算的就是这个）。
    const offsetOfRow = (i: number) =>
      rows.slice(0, i).reduce((sum, r) => sum + (classes.has(MEASURE_CLASS) ? r.real : r.est), 0)

    // 帧外量到的是估计值 —— 落点会算错。
    expect(offsetOfRow(1)).toBe(28)

    beginMeasuredLayout(scroller)
    const landOnRow1 = offsetOfRow(1)
    endMeasuredLayout(scroller)

    // 帧里量到的是真实高度。
    expect(landOnRow1).toBe(96)
    // 量完还在测量帧里：撤销排在两个 rAF 之后，先让这一帧按真实高度画一次。
    expect(classes.has(MEASURE_CLASS)).toBe(true)
  })
})

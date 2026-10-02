// 涂黑（redact）三种样式的绘制：方块步长、tile 边长、灰值范围都是参考物量出来的
// 常量；实心只铺纯黑；马赛克/噪点先铺纯黑再叠图案，图案相位按左上角对 tile 取模。
// 画布本体在 jsdom 里没有 2D 上下文，所以这里喂一个记录调用的假上下文。
import type { RedactStyle } from './designSketch'

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  paintStroke,
  REDACT_BLOCK_STEP,
  REDACT_INK,
  REDACT_STYLES,
  REDACT_TILE,
  redactAnchor,
  redactBlocks,
} from './designSketch'

type Call = { type: string; args: number[]; fill?: unknown }

/** 记录调用的假 2D 上下文。`fillRect` 记下当时的 `fillStyle`，好分辨黑白底与图案。 */
function recordingContext() {
  const calls: Call[] = []
  const ctx = {
    calls,
    save() {},
    restore() {},
    beginPath() {},
    closePath() {},
    moveTo() {},
    lineTo() {},
    arc() {},
    fill() {},
    stroke() {},
    strokeRect(x: number, y: number, w: number, h: number) {
      calls.push({ type: 'strokeRect', args: [x, y, w, h] })
    },
    ellipse() {},
    fillText() {},
    strokeStyle: '',
    fillStyle: '' as unknown,
    lineWidth: 1,
    lineCap: '',
    lineJoin: '',
    font: '',
    textAlign: '',
    textBaseline: '',
    imageSmoothingEnabled: true,
    translate(x: number, y: number) {
      calls.push({ type: 'translate', args: [x, y] })
    },
    fillRect(x: number, y: number, w: number, h: number) {
      calls.push({ type: 'fillRect', args: [x, y, w, h], fill: ctx.fillStyle })
    },
    createPattern() {
      calls.push({ type: 'createPattern', args: [] })
      return { pattern: true }
    },
  }
  return ctx
}

const realCreateElement = document.createElement.bind(document)
const tileContexts: ReturnType<typeof recordingContext>[] = []

beforeEach(() => {
  tileContexts.length = 0
  vi.spyOn(document, 'createElement').mockImplementation((tag: string) => {
    if (tag !== 'canvas') return realCreateElement(tag)
    const canvas = {
      width: 0,
      height: 0,
      getContext() {
        const context = recordingContext()
        tileContexts.push(context)
        return context
      },
    }
    return canvas as unknown as HTMLCanvasElement
  })
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('涂黑样式的常量', () => {
  it('tile 边长 96，方块步长 mosaic=12、noise=2、solid 不铺', () => {
    expect(REDACT_TILE).toBe(96)
    expect(REDACT_BLOCK_STEP.mosaic).toBe(12)
    expect(REDACT_BLOCK_STEP.noise).toBe(2)
    expect(REDACT_BLOCK_STEP.solid).toBe(0)
    expect(REDACT_STYLES).toEqual(['solid', 'mosaic', 'noise'])
  })

  it('灰值范围：mosaic 56–200、noise 16–240', () => {
    const mosaic = redactBlocks('mosaic', () => 0.5)
    const low = redactBlocks('mosaic', () => 0)
    const high = redactBlocks('mosaic', () => 0.999999)
    expect(Math.min(...low)).toBe(56)
    expect(Math.max(...high)).toBe(200)
    expect(mosaic.every((v) => v >= 56 && v <= 200)).toBe(true)

    const noiseLow = redactBlocks('noise', () => 0)
    const noiseHigh = redactBlocks('noise', () => 0.999999)
    expect(Math.min(...noiseLow)).toBe(16)
    expect(Math.max(...noiseHigh)).toBe(240)
  })

  it('方块数按 (tile/step)^2 走：mosaic 64 块、noise 2304 块', () => {
    expect(redactBlocks('mosaic')).toHaveLength((REDACT_TILE / 12) ** 2)
    expect(redactBlocks('mosaic')).toHaveLength(64)
    expect(redactBlocks('noise')).toHaveLength((REDACT_TILE / 2) ** 2)
    expect(redactBlocks('noise')).toHaveLength(2304)
  })

  it('每块一个随机灰值，不是常量', () => {
    const values = new Set(redactBlocks('mosaic', Math.random))
    expect(values.size).toBeGreaterThan(1)
  })
})

describe('图案相位', () => {
  it('按左上角对 tile 取模', () => {
    expect(redactAnchor(100, 50)).toEqual({ x: 100 % 96, y: 50 % 96 })
    expect(redactAnchor(96, 192)).toEqual({ x: 0, y: 0 })
    expect(redactAnchor(97, 193)).toEqual({ x: 1, y: 1 })
  })

  it('同一个区域给出稳定的相位（重画不滑）', () => {
    // 写死具体值，不拿函数跟自己对：一个永远返回 {0,0} 的实现也是「稳定」的。
    // 360 mod 96 = 72、240 mod 96 = 48。
    expect(redactAnchor(360, 240)).toEqual({ x: 72, y: 48 })
    expect(redactAnchor(360, 240)).toEqual(redactAnchor(360, 240))
  })

  it('平移一个 tile 的整数倍，相位不变', () => {
    expect(redactAnchor(100, 50)).toEqual({ x: 4, y: 50 })
    expect(redactAnchor(100, 50)).toEqual(redactAnchor(100 + 96 * 3, 50 + 96 * 2))
  })

  it('负坐标也落在 [0, tile)：-1 → 95、-97 → 95', () => {
    expect(redactAnchor(-1, -97)).toEqual({ x: 95, y: 95 })
  })
})

describe('paintStroke：三块涂黑', () => {
  const region = { x: 100, y: 50, width: 40, height: 20 }

  it('实心只铺一层纯黑，不叠图案、不平移', () => {
    const ctx = recordingContext()
    paintStroke(ctx as never, { tool: 'redact', color: '#E03131', width: 2, region }, 1, 1000)
    const fills = ctx.calls.filter((call) => call.type === 'fillRect')
    expect(fills).toHaveLength(1)
    expect(fills[0].fill).toBe(REDACT_INK)
    expect(fills[0].args).toEqual([100, 50, 40, 20])
    expect(ctx.calls.some((call) => call.type === 'createPattern')).toBe(false)
    expect(ctx.calls.some((call) => call.type === 'translate')).toBe(false)
  })

  it('涂黑不认颜色：叠图案的样式里铺底也是纯黑，不是那条红', () => {
    const ctx = recordingContext()
    paintStroke(ctx as never, { tool: 'redact', color: '#E03131', width: 2, region, redactStyle: 'mosaic' }, 1, 1000)
    const fills = ctx.calls.filter((call) => call.type === 'fillRect')
    expect(fills[0].fill).toBe(REDACT_INK)
    expect(ctx.calls.some((call) => call.type === 'strokeRect')).toBe(false)
  })

  it('马赛克：先纯黑，再叠图案；关掉平滑；按相位平移', () => {
    const ctx = recordingContext()
    paintStroke(ctx as never, { tool: 'redact', color: '#E03131', width: 2, region, redactStyle: 'mosaic' }, 1, 1000)
    const fills = ctx.calls.filter((call) => call.type === 'fillRect')
    // 第一层纯黑铺满整个区域。
    expect(fills[0].fill).toBe(REDACT_INK)
    expect(fills[0].args).toEqual([100, 50, 40, 20])
    // 第二层是图案，从区域左上角对 tile 取模之后的位置铺（100 mod 96 = 4、50 mod 96 = 50）。
    // 期望值写死，不拿 redactAnchor 算：那样锚点这一环错了测试也不会红。
    expect(ctx.calls.some((call) => call.type === 'createPattern')).toBe(true)
    const patternFill = fills.find((call) => call.fill !== REDACT_INK)
    expect(patternFill?.args).toEqual([100 - 4, 50 - 50, 40, 20])
    expect(ctx.calls.find((call) => call.type === 'translate')?.args).toEqual([4, 50])
    expect(ctx.imageSmoothingEnabled).toBe(false)
  })

  it('噪点也铺图案（和实心分得开）', () => {
    const ctx = recordingContext()
    paintStroke(ctx as never, { tool: 'redact', color: '#E03131', width: 2, region, redactStyle: 'noise' }, 1, 1000)
    expect(ctx.calls.some((call) => call.type === 'createPattern')).toBe(true)
    expect(ctx.calls.some((call) => call.type === 'translate')).toBe(true)
  })
})

describe('tile 本体', () => {
  /** 用一份全新的模块跑 `redactTile`，好拿到它这次真正画 tile 的上下文。 */
  async function buildTile(style: RedactStyle) {
    vi.resetModules()
    let captured: ReturnType<typeof recordingContext> | null = null
    vi.spyOn(document, 'createElement').mockImplementation((tag: string) => {
      if (tag !== 'canvas') return realCreateElement(tag)
      const canvas = {
        width: 0,
        height: 0,
        getContext() {
          captured = recordingContext()
          return captured
        },
      }
      return canvas as unknown as HTMLCanvasElement
    })
    const { redactTile } = await import('./designSketch')
    const tile = redactTile(style)
    if (!captured) throw new Error('tile context was not created')
    return { tile, ctx: captured as ReturnType<typeof recordingContext> }
  }

  it('马赛克 tile 是 8×8 个 12×12 的方块，坐标落在 12 的倍数上', async () => {
    const { tile, ctx } = await buildTile('mosaic')
    expect(tile?.width).toBe(REDACT_TILE)
    expect(tile?.height).toBe(REDACT_TILE)
    const fills = ctx.calls.filter((call) => call.type === 'fillRect')
    expect(fills).toHaveLength(64)
    for (const fill of fills) {
      expect(fill.args[2]).toBe(12)
      expect(fill.args[3]).toBe(12)
      expect(fill.args[0] % 12).toBe(0)
      expect(fill.args[1] % 12).toBe(0)
    }
  })

  it('噪点 tile 是 48×48 个 2×2 的方块', async () => {
    const { ctx } = await buildTile('noise')
    const fills = ctx.calls.filter((call) => call.type === 'fillRect')
    expect(fills).toHaveLength(48 * 48)
    for (const fill of fills) {
      expect(fill.args[2]).toBe(2)
      expect(fill.args[3]).toBe(2)
    }
  })

  it('实心没有 tile', async () => {
    vi.resetModules()
    const { redactTile } = await import('./designSketch')
    expect(redactTile('solid')).toBeNull()
  })
})

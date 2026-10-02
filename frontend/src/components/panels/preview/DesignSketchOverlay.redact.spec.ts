// 屏幕上那三种涂黑样式要看得出来是三种。
//
// 导出走的是 redactTile；屏幕这边以前三种都画成纯黑（和代码里「三种样式是三种样子」的
// 承诺对不上），现在非实心的铺一块 <pattern><image> 的贴图，用的是同一块 tile。
// 这里把 redactTileDataUrl 换成一个固定的 data URL，钉住：实心没有 pattern、非实心有、
// 而且 pattern 的相位按涂黑块自己的左上角取（相邻两块不会拼成一片）。
import { cleanup, render } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import DesignSketchOverlay from './DesignSketchOverlay.vue'

const TILE = 'data:image/png;base64,VEVTVA=='

vi.mock('./designSketch', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./designSketch')>()
  return {
    ...actual,
    // 真实现要 canvas.toDataURL（jsdom 没有）；实心本来就不走贴图。
    redactTileDataUrl: (style: string) => (style === 'solid' ? null : TILE),
  }
})

afterEach(cleanup)

/** 一块涂黑：自然像素 (100,50) 起，40×20；scale=0.5 → 屏上 (50,25) 起，20×10。 */
function redact(style: 'solid' | 'mosaic' | 'noise', x = 100, y = 50) {
  return {
    tool: 'redact' as const,
    color: '#ff0000',
    width: 6,
    region: { x, y, width: 40, height: 20 },
    redactStyle: style,
  }
}

function paint(strokes: ReturnType<typeof redact>[]) {
  return render(DesignSketchOverlay, { props: { strokes, scale: 0.5, naturalWidth: 1000 } })
}

const rects = (ui: ReturnType<typeof render>) => Array.from(ui.container.querySelectorAll('rect')) as SVGRectElement[]

describe('屏幕上的涂黑样式', () => {
  it('实心：不铺贴图，纯黑填充', () => {
    const ui = paint([redact('solid')])
    expect(ui.container.querySelectorAll('pattern')).toHaveLength(0)
    const [rect] = rects(ui)
    expect(rect.getAttribute('fill')).toBe('#000000')
  })

  it('马赛克：铺一块贴图，填充指向它', () => {
    const ui = paint([redact('mosaic')])
    const patterns = Array.from(ui.container.querySelectorAll('pattern'))
    expect(patterns).toHaveLength(1)
    const [pattern] = patterns
    const id = pattern.getAttribute('id')!
    expect(pattern.querySelector('image')?.getAttribute('href')).toBe(TILE)
    expect(rects(ui)[0].getAttribute('fill')).toBe(`url(#${id})`)
  })

  it('贴图的相位按这块涂黑的左上角取，尺寸是 tile×缩放', () => {
    const ui = paint([redact('mosaic')])
    const pattern = ui.container.querySelector('pattern')!
    // anchor(100, 50) = (4, 50)；scale 0.5 → (2, 25)。REDACT_TILE(96) × 0.5 = 48。
    expect(pattern.getAttribute('x')).toBe('2')
    expect(pattern.getAttribute('y')).toBe('25')
    expect(pattern.getAttribute('width')).toBe('48')
    expect(pattern.getAttribute('height')).toBe('48')
  })

  it('同一张图上两块涂黑：各铺各的贴图，id 不撞，相位各按自己', () => {
    const ui = paint([redact('mosaic', 100, 50), redact('noise', 196, 50)])
    const patterns = Array.from(ui.container.querySelectorAll('pattern'))
    expect(patterns).toHaveLength(2)
    const ids = patterns.map((pattern) => pattern.getAttribute('id'))
    expect(new Set(ids).size).toBe(2)
    // 第一块 x=4、第二块 x=196 mod 96 = 4——平移整数个 tile，相位相同、尺寸相同。
    expect(patterns[0].getAttribute('x')).toBe('2')
    expect(patterns[1].getAttribute('x')).toBe('2')
    const fills = rects(ui).map((rect) => rect.getAttribute('fill'))
    expect(new Set(fills).size).toBe(2)
  })
})

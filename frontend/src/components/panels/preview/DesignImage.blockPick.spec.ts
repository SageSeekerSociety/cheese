// 点一下内容块（不拖），在真的 DesignImage 里跑一遍。
//
// 这条路的判定全在 profile 上：只有拿到内容分界才有「块」可点。真环境下 jsdom 取不到
// 像素（sampleImage 返回 null），所以这里把 sampleImage / contentProfile 换掉，给出
// 一张只有一块内容的图，让 pick-block 真的走通。
//
// 钉住的是三件事：涂黑只涂点到的那一块；涂黑点到空白什么都不做（整张图被涂掉是破坏性的，
// 不能靠猜）；矩形点空白仍然是框住整张图（那是画个圈，看得出来）。
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import DesignImage from './DesignImage.vue'

import { setLocale } from '@/i18n'
import { nextMillisecond } from '@/test/nextMillisecond'

/** 原图 1000×500 上唯一的一块内容：自然像素 (150,150)-(250,250)。 */
const BLOCK = { x: 150, y: 150, width: 100, height: 100 }
const PROFILE = { columns: [150, 250], rows: [150, 250], boxes: [BLOCK] }

vi.mock('./designSnap', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./designSnap')>()
  return { ...actual, contentProfile: () => PROFILE }
})
vi.mock('./designSketch', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./designSketch')>()
  return { ...actual, sampleImage: () => ({ data: new Uint8ClampedArray(4), width: 1, height: 1 }) }
})

let observed: Map<Element, ResizeObserverCallback>
beforeEach(() => {
  setLocale('zh-CN')
  observed = new Map()
  vi.stubGlobal(
    'ResizeObserver',
    class {
      constructor(private callback: ResizeObserverCallback) {}
      observe(element: Element) {
        observed.set(element, this.callback)
      }
      disconnect() {}
    }
  )
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

/** 量测落在 rAF 里（happy-dom 用 setImmediate 实现）：手势之前先让排着的那一帧跑完。 */
async function settled() {
  await new Promise((resolve) => setTimeout(resolve, 0))
  await new Promise((resolve) => setTimeout(resolve, 0))
}

/** scale = (532-32)/1000 = 0.5；图片显示在 (10,20)-(510,270)。 */
async function painted(ui: ReturnType<typeof render>) {
  await waitFor(() => expect(ui.container.querySelector('.design-image__pane img')).toBeTruthy())
  const pane = ui.container.querySelector('.design-image__pane') as HTMLElement
  const image = pane.querySelector('img')!
  Object.defineProperty(pane, 'clientWidth', { configurable: true, value: 532 })
  Object.defineProperties(image, {
    complete: { configurable: true, value: true },
    naturalWidth: { configurable: true, value: 1000 },
    naturalHeight: { configurable: true, value: 500 },
  })
  image.getBoundingClientRect = () => ({ left: 10, top: 20, width: 500, height: 250 }) as DOMRect
  await waitFor(() => expect(observed.has(pane)).toBe(true))
  observed.get(pane)!([], {} as ResizeObserver)
  await fireEvent.load(image)
  await settled()
}

function mount() {
  return render(DesignImage, { props: { src: 'blob:sketch', alt: 'design.png', identity: 'v1' } })
}

/** 挑一个工具，等它显示成选中、并跨过挂载那一毫秒再动手（见 DesignSketchObjectEdit.spec.ts）。 */
async function pickTool(ui: ReturnType<typeof render>, label: string) {
  await fireEvent.click(ui.getByRole('button', { name: label }))
  await waitFor(() =>
    expect(
      ui.container.querySelector(`.sketch-toolbar__tool[aria-label="${label}"]`)?.getAttribute('aria-pressed')
    ).toBe('true')
  )
  await nextMillisecond()
}

/** 在屏上这个点上点一下（不拖）。 */
async function tap(ui: ReturnType<typeof render>, clientX: number, clientY: number) {
  const layer = ui.getByRole('application', { name: '图片标注画布' })
  layer.setPointerCapture = vi.fn()
  await fireEvent.pointerDown(layer, { button: 0, pointerId: 81, clientX, clientY })
  await fireEvent.pointerUp(layer, { button: 0, pointerId: 81, clientX, clientY })
}

const marked = (ui: ReturnType<typeof render>) =>
  ui.container.querySelectorAll('.sketch-overlay rect:not(.sketch-overlay__frame)')

describe('点一下内容块', () => {
  it('涂黑工具点在那块内容上：只涂这一块', async () => {
    const ui = mount()
    await painted(ui)
    await pickTool(ui, '涂黑')
    // 自然像素 (200,200) 在 BLOCK 里 -> 屏上 client (110,120)。
    await tap(ui, 110, 120)
    await waitFor(() => expect(marked(ui)).toHaveLength(1))
    const rect = marked(ui)[0] as SVGRectElement
    // 涂的是那一块（显示坐标 = 自然坐标 × 0.5），不是整张图。
    expect(rect.getAttribute('x')).toBe('75')
    expect(rect.getAttribute('y')).toBe('75')
    expect(rect.getAttribute('width')).toBe('50')
    expect(rect.getAttribute('height')).toBe('50')
  })

  it('涂黑工具点在空白处：什么都不画（不猜整张图）', async () => {
    const ui = mount()
    await painted(ui)
    await pickTool(ui, '涂黑')
    // 自然像素 (780,440) 落在 BLOCK 外面 -> 屏上 client (400,240)。
    await tap(ui, 400, 240)
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(marked(ui)).toHaveLength(0)
  })

  it('矩形工具点在空白处：仍然框住整张图', async () => {
    const ui = mount()
    await painted(ui)
    await pickTool(ui, '矩形')
    await tap(ui, 400, 240)
    await waitFor(() => expect(marked(ui)).toHaveLength(1))
    const rect = marked(ui)[0] as SVGRectElement
    expect(rect.getAttribute('width')).toBe('500')
    expect(rect.getAttribute('height')).toBe('250')
    // 是「框一圈」不是「涂黑」：矩形不填充。（涂黑这一格会是纯黑。）
    expect(rect.getAttribute('fill')).toBe('none')
  })
})

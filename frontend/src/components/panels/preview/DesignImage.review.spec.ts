/**
 * 独立审查（#4970 第一轮）提的三件事，各自一条会红的用例。
 *
 * 三件都是「接线接错了」，光看测试全绿看不出来：
 *   - 量出来的那条线要跟图同坐标，就得挂在图那一层、viewBox 按原图宽高绑好；
 *   - 「调色」按下要能开、再按要能关，开合状态不能从滑杆的数值推；
 *   - 超长图右边那条定位条，窗口要跟着滚动走。
 */
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import DesignImage from './DesignImage.vue'

import { setLocale } from '@/i18n'

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
  Object.defineProperty(HTMLCanvasElement.prototype, 'getContext', {
    configurable: true,
    writable: true,
    value: () => ({ clearRect: () => {}, drawImage: () => {} }),
  })
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

/** 把图摆出来：给出自然宽高和一个够高的视口，好让「比视口长」成立。 */
async function painted(ui: ReturnType<typeof render>, opts: { naturalWidth?: number; naturalHeight?: number } = {}) {
  const naturalWidth = opts.naturalWidth ?? 1000
  const naturalHeight = opts.naturalHeight ?? 500
  await waitFor(() => expect(ui.container.querySelector('.design-image__pane img')).toBeTruthy())
  const pane = ui.container.querySelector('.design-image__pane') as HTMLElement
  const image = pane.querySelector('img')!
  Object.defineProperty(pane, 'clientWidth', { configurable: true, value: 532 })
  Object.defineProperty(pane, 'clientHeight', { configurable: true, value: 400 })
  // 内容比视口长：定位条只在「明显比视口长」时才摆，第一条量的时候就得量到。
  Object.defineProperty(pane, 'scrollHeight', { configurable: true, value: naturalHeight })
  Object.defineProperties(image, {
    complete: { configurable: true, value: true },
    naturalWidth: { configurable: true, value: naturalWidth },
    naturalHeight: { configurable: true, value: naturalHeight },
    // 图在屏幕上的框：宽高比和原图同一条，长方形。
    getBoundingClientRect: {
      configurable: true,
      value: () => ({
        left: 100,
        top: 100,
        width: naturalWidth / 2,
        height: naturalHeight / 2,
        right: 100 + naturalWidth / 2,
        bottom: 100 + naturalHeight / 2,
      }),
    },
  })
  await waitFor(() => expect(observed.has(pane)).toBe(true))
  observed.get(pane)!([], {} as ResizeObserver)
  await fireEvent.load(image)
  return { pane, image }
}

it('量出来的那条线画在图那一层，坐标按原图像素铺开', async () => {
  const ui = render(DesignImage, {
    props: { src: 'blob:pic', alt: 'shot.png', identity: 'v1', selectionEnabled: true },
  })
  const { image } = await painted(ui, { naturalWidth: 1046, naturalHeight: 3083 })
  const rect = image.getBoundingClientRect()

  // 量距离：按下、拖、抬起。
  const measure = ui.container.querySelectorAll('.design-image__inspect-btn')[1] as HTMLElement
  await fireEvent.click(measure)
  const sheet = ui.container.querySelector('.design-image__sheet') as HTMLElement
  const at = (fx: number, fy: number) => ({
    clientX: rect.left + rect.width * fx,
    clientY: rect.top + rect.height * fy,
  })
  await fireEvent.pointerDown(sheet, at(0.2, 0.25))
  await fireEvent.pointerMove(sheet, at(0.85, 0.7))
  await fireEvent.pointerUp(sheet, at(0.85, 0.7))

  const svg = await waitFor(() => {
    const found = ui.container.querySelector('.design-image__measure') as SVGElement | null
    expect(found).toBeTruthy()
    return found!
  })
  // 挂在图那一层：跟着一起转、翻、裁。
  expect(svg.parentElement!.className).toContain('design-image__frame')
  // viewBox 按原图宽高铺开（写成字符串属性就是没绑上，浏览器认不出来）。
  expect(svg.getAttribute('viewBox')).toBe('0 0 1046 3083')
  // 两端落在原图坐标里，不是屏幕坐标。
  const line = svg.querySelector('line')!
  expect(Number(line.getAttribute('x1'))).toBeGreaterThan(50)
  expect(Number(line.getAttribute('y1'))).toBeGreaterThan(500)
})

it('调色按下能开、再按能关，开着与否不看滑杆的数值', async () => {
  const ui = render(DesignImage, {
    props: { src: 'blob:pic', alt: 'shot.png', identity: 'v1', selectionEnabled: true },
  })
  await painted(ui)
  const tone = Array.from(ui.container.querySelectorAll('.design-view-tools button')).find((b) =>
    b.textContent?.includes('调色')
  ) as HTMLButtonElement

  await fireEvent.click(tone)
  await waitFor(() => expect(ui.container.querySelector('.design-image__tone')).toBeTruthy())
  expect(tone.getAttribute('aria-expanded')).toBe('true')

  // 一按就关：滑杆还在 100%，开着与否不能从数值推。
  await fireEvent.click(tone)
  await waitFor(() => expect(ui.container.querySelector('.design-image__tone')).toBeNull())
  expect(tone.getAttribute('aria-expanded')).toBe('false')

  // 关着的时候还能再开。
  await fireEvent.click(tone)
  await waitFor(() => expect(ui.container.querySelector('.design-image__tone')).toBeTruthy())
})

it('超长图的定位条：窗口跟着滚动走，不冻在最上面', async () => {
  const ui = render(DesignImage, {
    props: { src: 'blob:tall', alt: 'shot.png', identity: 'v1', selectionEnabled: true },
  })
  const { pane } = await painted(ui, { naturalWidth: 1046, naturalHeight: 3083 })

  const rail = await waitFor(() => {
    const found = ui.container.querySelector('.design-minimap__window') as HTMLElement | null
    expect(found).toBeTruthy()
    return found!
  })
  expect(rail.style.top).toBe('0%')

  // 滚到下半段。
  pane.scrollTop = 2000
  await fireEvent.scroll(pane)
  await waitFor(() => expect(rail.style.top).not.toBe('0%'))
  expect(parseFloat(rail.style.top)).toBeGreaterThan(40)
})

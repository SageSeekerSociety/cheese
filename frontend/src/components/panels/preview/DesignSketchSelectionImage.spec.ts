// 选中 + 拖把手改框，在真的 DesignImage 里跑一遍：证明选中层挂上了、改框真的落回
// 标注数据（Overlay 上的矩形跟着变），而不只是组件自己算对了。
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
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

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
}

async function drawRect(ui: ReturnType<typeof render>) {
  await fireEvent.click(ui.getByRole('button', { name: '矩形' }))
  const layer = ui.getByRole('application', { name: '图片标注画布' })
  layer.setPointerCapture = vi.fn()
  await fireEvent.pointerDown(layer, { button: 0, pointerId: 11, clientX: 60, clientY: 70 })
  await fireEvent.pointerMove(layer, { pointerId: 11, clientX: 160, clientY: 120 })
  await fireEvent.pointerUp(layer, { pointerId: 11, clientX: 160, clientY: 120 })
}

it('选中已画好的矩形：出现虚线框和把手，拖右下角把手把框改大', async () => {
  const ui = render(DesignImage, { props: { src: 'blob:sketch', alt: 'design.png', identity: 'v1' } })
  await painted(ui)
  await drawRect(ui)
  // 切回默认的框选工具，选中层才挂上。
  await fireEvent.click(ui.getByRole('button', { name: '选择图片区域' }))

  const pick = ui.container.querySelector('.sketch-selection__pick') as SVGRectElement
  expect(pick).toBeTruthy()
  await fireEvent.pointerDown(pick, { button: 0, pointerId: 3, clientX: 100, clientY: 100 })

  const box = ui.container.querySelector('.sketch-selection__box') as SVGRectElement
  expect(box).toBeTruthy()
  expect(box.getAttribute('stroke-dasharray')).toBe('5 4')
  expect(ui.container.querySelectorAll('.sketch-selection__handle')).toHaveLength(8)

  const overlayRect = () => ui.container.querySelector('.sketch-overlay rect') as SVGRectElement
  const before = Number(overlayRect().getAttribute('width'))
  expect(before).toBeGreaterThan(0)

  // 屏幕坐标：scale = (532-32)/1000 = 0.5，框在屏上是 (50,50)-(150,100)，右下角在 (150,100)。
  const selection = ui.container.querySelector('.sketch-selection') as HTMLElement
  Object.defineProperty(selection, 'getBoundingClientRect', {
    configurable: true,
    value: () => ({ left: 0, top: 0, width: 500, height: 250 }) as DOMRect,
  })
  const grab = ui.container.querySelector('.sketch-selection__grab') as HTMLElement
  grab.setPointerCapture = vi.fn()
  await fireEvent.pointerDown(grab, { button: 0, pointerId: 5, clientX: 150, clientY: 100 })
  await fireEvent.pointerMove(grab, { pointerId: 5, clientX: 250, clientY: 150 })
  await fireEvent.pointerUp(grab, { pointerId: 5, clientX: 250, clientY: 150 })

  await waitFor(() => expect(Number(overlayRect().getAttribute('width'))).toBe(before * 2))
})

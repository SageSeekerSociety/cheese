/**
 * 图上框出来的那几处评论：一块一个点，点开就是那一串。
 *
 * 守的是「评论存下来了、图上却看不见」这条缝：`image-region` 锚点发得出去也存得下
 * （#4875），但没有任何地方画它——读者在图上看不到自己留过评论，也点不回去。
 *
 * 位置是**原图像素**（和锚点、和框选发出去的引用同一套坐标），显示时按屏幕上的缩放换算：
 * scale = (532-32)/1000 = 0.5，所以原图 (200,200) 那一点落在屏上 (100,100)。
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

function mount(
  marks: { id: string; region: { x: number; y: number; width: number; height: number } }[],
  activeMark: string | null = null
) {
  return render(DesignImage, {
    props: { src: 'blob:sketch', alt: 'design.png', identity: 'v1', marks, activeMark },
  })
}

const dots = (ui: ReturnType<typeof render>) => ui.container.querySelectorAll('.design-image__mark')

it('图上那几处评论各自画一个点，落在那一块的左上角（原图像素按屏幕缩放）', async () => {
  const ui = mount([{ id: 't1', region: { x: 200, y: 200, width: 100, height: 100 } }])
  await painted(ui)
  await waitFor(() => expect(dots(ui)).toHaveLength(1))
  const dot = dots(ui)[0] as HTMLElement
  // 原图 (200,200) × 0.5 = 屏上 (100,100)。
  expect(dot.style.left).toBe('100px')
  expect(dot.style.top).toBe('100px')
})

it('没画标记时一个点都不摆', async () => {
  const ui = mount([])
  await painted(ui)
  expect(dots(ui)).toHaveLength(0)
})

it('点一下某一颗：把那一串的 id 报出去，由宿主决定开哪儿', async () => {
  const ui = mount([{ id: 't1', region: { x: 200, y: 200, width: 100, height: 100 } }])
  await painted(ui)
  await waitFor(() => expect(dots(ui)).toHaveLength(1))
  await fireEvent.click(dots(ui)[0])
  expect(ui.emitted().mark).toEqual([['t1']])
})

it('打开着的那一串画得实一点（其余不）', async () => {
  const ui = mount(
    [
      { id: 't1', region: { x: 200, y: 200, width: 100, height: 100 } },
      { id: 't2', region: { x: 400, y: 100, width: 50, height: 50 } },
    ],
    't2'
  )
  await painted(ui)
  await waitFor(() => expect(dots(ui)).toHaveLength(2))
  expect((dots(ui)[0] as HTMLElement).classList.contains('is-active')).toBe(false)
  expect((dots(ui)[1] as HTMLElement).classList.contains('is-active')).toBe(true)
})

// 关掉/离开这块正在标注的图之前先问一句「放弃这些标注？」——画了、还没发出去的
// 笔画才拦。文案照参考物（Claude 桌面版那套标注器）：Discard your annotations?。
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { confirmAnnotationDiscard, hasUnsentAnnotations } from './annotationDiscard'
import DesignImage from './DesignImage.vue'

import { setLocale } from '@/i18n'

// 合成标注图要真 canvas，测试环境里没有；只把这一步换成成功，别的照旧。
vi.mock('./designSketch', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./designSketch')>()
  return { ...actual, composeSketch: async () => new Blob(['annotated']) }
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
  return image
}

function mount() {
  return render(DesignImage, { props: { src: 'blob:sketch', alt: 'design.png', identity: 'v1' } })
}

async function drawRect(ui: ReturnType<typeof render>) {
  await fireEvent.click(ui.getByRole('button', { name: '矩形' }))
  const layer = ui.getByRole('application', { name: '图片标注画布' })
  layer.setPointerCapture = vi.fn()
  await fireEvent.pointerDown(layer, { button: 0, pointerId: 11, clientX: 60, clientY: 70 })
  await fireEvent.pointerMove(layer, { pointerId: 11, clientX: 160, clientY: 120 })
  await fireEvent.pointerUp(layer, { pointerId: 11, clientX: 160, clientY: 120 })
}
const rects = (ui: ReturnType<typeof render>) => ui.container.querySelectorAll('.sketch-overlay rect').length

describe('离开前问一句', () => {
  it('没画东西不拦：默认放行', async () => {
    const ui = mount()
    await painted(ui)
    expect(hasUnsentAnnotations()).toBe(false)
    await expect(confirmAnnotationDiscard()).resolves.toBe(true)
  })

  it('画了还没发就登记，确认框照参考物的文案', async () => {
    const ui = mount()
    await painted(ui)
    await drawRect(ui)
    await waitFor(() => expect(hasUnsentAnnotations()).toBe(true))

    const asked = confirmAnnotationDiscard()
    const dialog = await ui.findByRole('alertdialog', { name: '放弃这些标注？' })
    expect(dialog).toBeTruthy()
    // 保留：什么都不丢，返回 false。
    await fireEvent.click(ui.getByRole('button', { name: '保留' }))
    await expect(asked).resolves.toBe(false)
    expect(rects(ui)).toBe(1)
    expect(hasUnsentAnnotations()).toBe(true)
  })

  it('确认放弃：笔画清掉、登记撤下，紧接着再问一次不会再弹', async () => {
    const ui = mount()
    await painted(ui)
    await drawRect(ui)
    await waitFor(() => expect(hasUnsentAnnotations()).toBe(true))

    const asked = confirmAnnotationDiscard()
    await ui.findByRole('alertdialog', { name: '放弃这些标注？' })
    await fireEvent.click(ui.getByRole('button', { name: '放弃' }))
    await expect(asked).resolves.toBe(true)
    await waitFor(() => expect(rects(ui)).toBe(0))
    expect(hasUnsentAnnotations()).toBe(false)
    await expect(confirmAnnotationDiscard()).resolves.toBe(true)
  })

  it('发出去之后就不再拦：笔画还留在屏上，但已经不欠谁了', async () => {
    const ui = mount()
    await painted(ui)
    await drawRect(ui)
    await waitFor(() => expect(hasUnsentAnnotations()).toBe(true))
    await fireEvent.update(ui.getByPlaceholderText('说一句要改什么，回车发送'), '改成蓝色')
    await fireEvent.click(ui.getByRole('button', { name: '加入对话' }))
    await waitFor(() => expect(hasUnsentAnnotations()).toBe(false))
    // 笔画还在（发出去只是合成一张图，不改屏上的东西），拦不拦看的是「有没有没发出去的」。
    expect(rects(ui)).toBe(1)
    await expect(confirmAnnotationDiscard()).resolves.toBe(true)
  })

  it('换个工具不算离开：不弹框，没发出去的笔画照旧登记着', async () => {
    const ui = mount()
    await painted(ui)
    await drawRect(ui)
    await waitFor(() => expect(hasUnsentAnnotations()).toBe(true))
    await fireEvent.click(ui.getByRole('button', { name: '自由画笔' }))
    expect(ui.queryByRole('alertdialog')).toBeNull()
    expect(hasUnsentAnnotations()).toBe(true)
    expect(rects(ui)).toBe(1)
  })

  it('这一页没在看了就不登记：画了东西、但收起来的那一页不拦别人', async () => {
    const ui = mount()
    await painted(ui)
    await drawRect(ui)
    await waitFor(() => expect(hasUnsentAnnotations()).toBe(true))
    await ui.rerender({ src: 'blob:sketch', alt: 'design.png', identity: 'v1', active: false })
    await waitFor(() => expect(hasUnsentAnnotations()).toBe(false))
    // 笔画还在，只是这一页没在看——登记的是「正在看的、且有未发笔画」的那一块。
    expect(rects(ui)).toBe(1)
    await expect(confirmAnnotationDiscard()).resolves.toBe(true)
  })
})

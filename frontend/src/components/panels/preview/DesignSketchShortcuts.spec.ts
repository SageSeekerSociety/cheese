// 标注的键盘快捷键：撤销/重做/退出。和参考物（Claude 桌面版那套标注器）对齐：
// ⌘/Ctrl+Z 撤销，⇧⌘/Ctrl+Z 与 Ctrl+Y 重做，Esc 退出正在进行的操作。
//
// 这几颗键都是全局的（挂在 window 上），所以「焦点在输入框里就让路」必须是这里的主
// 要断言：那个「说一句要改什么」的框、图上的文字框都得能用自己那套编辑键。
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

/** 一张「已经加载好」的图：几何全靠 DOM 替身，和别的预览用例同一套做法。 */
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

/** 画一个矩形：够大、不会被当成「点一下选区」。 */
async function drawRect(ui: ReturnType<typeof render>) {
  await fireEvent.click(ui.getByRole('button', { name: '矩形' }))
  const layer = ui.getByRole('application', { name: '图片标注画布' })
  layer.setPointerCapture = vi.fn()
  await fireEvent.pointerDown(layer, { button: 0, pointerId: 11, clientX: 60, clientY: 70 })
  await fireEvent.pointerMove(layer, { pointerId: 11, clientX: 160, clientY: 120 })
  await fireEvent.pointerUp(layer, { pointerId: 11, clientX: 160, clientY: 120 })
}
/** 已画好的矩形数：Overlay 上一笔矩形一个 <rect>。 */
const rects = (ui: ReturnType<typeof render>) => ui.container.querySelectorAll('.sketch-overlay rect').length

it('⌘/Ctrl+Z 撤销，⇧⌘+Z 与 Ctrl+Y 重做', async () => {
  const ui = mount()
  await painted(ui)
  await drawRect(ui)
  await drawRect(ui)
  expect(rects(ui)).toBe(2)
  await fireEvent.keyDown(window, { key: 'z', code: 'KeyZ', metaKey: true })
  expect(rects(ui)).toBe(1)
  await fireEvent.keyDown(window, { key: 'z', code: 'KeyZ', metaKey: true, shiftKey: true })
  expect(rects(ui)).toBe(2)
  await fireEvent.keyDown(window, { key: 'z', code: 'KeyZ', ctrlKey: true })
  expect(rects(ui)).toBe(1)
  await fireEvent.keyDown(window, { key: 'y', code: 'KeyY', ctrlKey: true })
  expect(rects(ui)).toBe(2)
})

it('没有可撤销的东西时按键照旧交回浏览器', async () => {
  const ui = mount()
  await painted(ui)
  const event = new KeyboardEvent('keydown', { key: 'z', code: 'KeyZ', metaKey: true, cancelable: true })
  window.dispatchEvent(event)
  expect(event.defaultPrevented).toBe(false)
})

it('焦点在「说一句要改什么」的框里时快捷键让路，不动标注', async () => {
  const ui = mount()
  await painted(ui)
  await drawRect(ui)
  const note = ui.getByPlaceholderText('说一句要改什么，回车发送')
  await fireEvent.keyDown(note, { key: 'z', code: 'KeyZ', metaKey: true })
  expect(rects(ui)).toBe(1)
})

it('Esc 退出手里的画笔，回到默认的框选', async () => {
  const ui = mount()
  await painted(ui)
  await fireEvent.click(ui.getByRole('button', { name: '矩形' }))
  expect(ui.getByRole('application', { name: '图片标注画布' })).toBeTruthy()
  await fireEvent.keyDown(window, { key: 'Escape' })
  expect(ui.queryByRole('application', { name: '图片标注画布' })).toBeNull()
  expect(ui.getByRole('button', { name: '选择图片区域' }).getAttribute('aria-pressed')).toBe('true')
})

it('输入法组字中的那一次回车是选词，不算发送', async () => {
  const ui = mount()
  await painted(ui)
  await drawRect(ui)
  const note = ui.getByPlaceholderText('说一句要改什么，回车发送')
  await fireEvent.update(note, '把这里改成蓝色')
  const composing = new KeyboardEvent('keydown', {
    key: 'Enter',
    keyCode: 229,
    isComposing: true,
    bubbles: true,
    cancelable: true,
  })
  note.dispatchEvent(composing)
  expect(composing.defaultPrevented).toBe(false)
  const plain = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })
  note.dispatchEvent(plain)
  expect(plain.defaultPrevented).toBe(true)
})

it('图上的文字框：组字中的 Esc 不会把这段文字拆掉', async () => {
  const ui = mount()
  await painted(ui)
  await fireEvent.click(ui.getByRole('button', { name: '文字' }))
  const layer = ui.getByRole('application', { name: '图片标注画布' })
  layer.setPointerCapture = vi.fn()
  await fireEvent.pointerDown(layer, { button: 0, pointerId: 21, clientX: 120, clientY: 140 })
  const field = ui.getByPlaceholderText('输入文字，回车确认')
  await fireEvent.update(field, '蓝色')
  // 组字中按 Esc 是取消候选：这颗键归输入法，输入框（连同半截文字）得留着。
  const composing = new KeyboardEvent('keydown', {
    key: 'Escape',
    keyCode: 229,
    isComposing: true,
    bubbles: true,
    cancelable: true,
  })
  field.dispatchEvent(composing)
  expect(composing.defaultPrevented).toBe(false)
  expect(ui.queryByPlaceholderText('输入文字，回车确认')).toBeTruthy()
  const plain = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })
  field.dispatchEvent(plain)
  // 原生 dispatch 不会替我们等 Vue 刷 DOM，所以这里必须等一等再问有没有——
  // 「组字中那一颗」的断言正是靠这一步才不是恒真。
  await waitFor(() => expect(ui.queryByPlaceholderText('输入文字，回车确认')).toBeNull())
})

it('收起来的那一页不接全局快捷键：⌘Z 改不到它', async () => {
  const ui = render(DesignImage, {
    props: { src: 'blob:sketch', alt: 'design.png', identity: 'v1', active: false },
  })
  await painted(ui)
  await drawRect(ui)
  await drawRect(ui)
  expect(rects(ui)).toBe(2)
  await fireEvent.keyDown(window, { key: 'z', code: 'KeyZ', metaKey: true })
  expect(rects(ui)).toBe(2)
})

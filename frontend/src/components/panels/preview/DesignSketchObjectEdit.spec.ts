// 对象级编辑，在真的 DesignImage 里跑一遍：点中一笔就选中并进入编辑，拖动整笔、拖把手
// 缩放，删除、改色、双击改文字；点在空白处拖仍然交给区域选择。撤销粒度、画完自动选中
// 也在这里钉住。
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
}

const sheet = (ui: ReturnType<typeof render>) => {
  const element = ui.container.querySelector('.design-image__sheet') as HTMLElement
  element.setPointerCapture = vi.fn()
  return element
}
const strokeRects = (ui: ReturnType<typeof render>) =>
  ui.container.querySelectorAll('.sketch-overlay rect:not(.sketch-overlay__frame)')
const overlayRect = (ui: ReturnType<typeof render>) =>
  ui.container.querySelector('.sketch-overlay rect') as SVGRectElement
const handleCount = (ui: ReturnType<typeof render>) => ui.container.querySelectorAll('.sketch-overlay__handle').length

/** 画一个够大的矩形：client (60,70)→(310,220) 即原图 (100,100)→(600,400)。 */
async function drawRect(ui: ReturnType<typeof render>) {
  await fireEvent.click(ui.getByRole('button', { name: '矩形' }))
  const layer = ui.getByRole('application', { name: '图片标注画布' })
  layer.setPointerCapture = vi.fn()
  await fireEvent.pointerDown(layer, { button: 0, pointerId: 11, clientX: 60, clientY: 70 })
  await fireEvent.pointerMove(layer, { pointerId: 11, clientX: 310, clientY: 220 })
  await fireEvent.pointerUp(layer, { pointerId: 11, clientX: 310, clientY: 220 })
  await waitFor(() => expect(strokeRects(ui)).toHaveLength(1))
}

function mount() {
  return render(DesignImage, { props: { src: 'blob:sketch', alt: 'design.png', identity: 'v1' } })
}

it('画完一个非自由笔的图形就自动选中它、工具回到 select', async () => {
  const ui = mount()
  await painted(ui)
  await drawRect(ui)
  expect(ui.getByRole('button', { name: '选择图片区域' }).getAttribute('aria-pressed')).toBe('true')
  expect(handleCount(ui)).toBe(8)
})

it('点中已画好的矩形就选中它（出现把手）', async () => {
  const ui = mount()
  await painted(ui)
  await drawRect(ui)
  // 先切到别的工具放下选中，再回来点它。
  await fireEvent.click(ui.getByRole('button', { name: '椭圆' }))
  expect(handleCount(ui)).toBe(0)
  await fireEvent.click(ui.getByRole('button', { name: '选择图片区域' }))
  // 矩形屏上外框 (50,50)-(300,200)，点上边 (110,50)。
  await fireEvent.pointerDown(sheet(ui), { button: 0, pointerId: 21, clientX: 120, clientY: 70 })
  expect(handleCount(ui)).toBe(8)
})

it('拖动选中对象：松手才记一条历史，撤销一次就回到原位', async () => {
  const ui = mount()
  await painted(ui)
  await drawRect(ui)
  expect(overlayRect(ui).getAttribute('x')).toBe('50')

  const layer = sheet(ui)
  await fireEvent.pointerDown(layer, { button: 0, pointerId: 31, clientX: 120, clientY: 70 })
  // 移动 20px（> 3px 门槛）——显示 +20 即原图 +40。
  await fireEvent.pointerMove(layer, { pointerId: 31, clientX: 140, clientY: 70 })
  await fireEvent.pointerUp(layer, { pointerId: 31, clientX: 140, clientY: 70 })
  await waitFor(() => expect(overlayRect(ui).getAttribute('x')).toBe('70'))

  await fireEvent.keyDown(window, { key: 'z', code: 'KeyZ', metaKey: true })
  await waitFor(() => expect(overlayRect(ui).getAttribute('x')).toBe('50'))
})

it('不到门槛的那点位移不算拖动，不记历史', async () => {
  const ui = mount()
  await painted(ui)
  await drawRect(ui)
  const layer = sheet(ui)
  await fireEvent.pointerDown(layer, { button: 0, pointerId: 32, clientX: 120, clientY: 70 })
  await fireEvent.pointerMove(layer, { pointerId: 32, clientX: 121, clientY: 70 })
  await fireEvent.pointerUp(layer, { pointerId: 32, clientX: 121, clientY: 70 })
  // 撤销一次：画的那一笔没了（说明拖动没往历史里加东西）。
  await fireEvent.keyDown(window, { key: 'z', code: 'KeyZ', metaKey: true })
  await waitFor(() => expect(strokeRects(ui)).toHaveLength(0))
})

it('拖的时候撤销被拒绝', async () => {
  const ui = mount()
  await painted(ui)
  await drawRect(ui)
  const layer = sheet(ui)
  await fireEvent.pointerDown(layer, { button: 0, pointerId: 33, clientX: 120, clientY: 70 })
  await fireEvent.pointerMove(layer, { pointerId: 33, clientX: 140, clientY: 70 })
  // 还没松手：这一下撤销不该动它。
  await fireEvent.keyDown(window, { key: 'z', code: 'KeyZ', metaKey: true })
  expect(strokeRects(ui)).toHaveLength(1)
  expect(overlayRect(ui).getAttribute('x')).toBe('70')
  await fireEvent.pointerUp(layer, { pointerId: 33, clientX: 140, clientY: 70 })
})

it('Backspace / Delete 删掉选中的那一笔', async () => {
  const ui = mount()
  await painted(ui)
  await drawRect(ui)
  await fireEvent.keyDown(window, { key: 'Backspace' })
  await waitFor(() => expect(strokeRects(ui)).toHaveLength(0))
  // 撤销能把删掉的拿回来；撤销会放下选中，所以删除前要重新点中它。
  await fireEvent.keyDown(window, { key: 'z', code: 'KeyZ', metaKey: true })
  await waitFor(() => expect(strokeRects(ui)).toHaveLength(1))
  await fireEvent.pointerDown(sheet(ui), { button: 0, pointerId: 42, clientX: 120, clientY: 70 })
  expect(handleCount(ui)).toBe(8)
  await fireEvent.keyDown(window, { key: 'Delete' })
  await waitFor(() => expect(strokeRects(ui)).toHaveLength(0))
})

it('有选中对象时，色板改的是选中对象的颜色', async () => {
  const ui = mount()
  await painted(ui)
  await drawRect(ui)
  expect(overlayRect(ui).getAttribute('stroke')).toBe('#E03131')
  await fireEvent.click(ui.getByRole('button', { name: '标注颜色 #1971C2' }))
  await waitFor(() => expect(overlayRect(ui).getAttribute('stroke')).toBe('#1971C2'))
})

it('没有选中对象时，色板只改下一笔的颜色', async () => {
  const ui = mount()
  await painted(ui)
  await fireEvent.click(ui.getByRole('button', { name: '标注颜色 #2F9E44' }))
  await drawRect(ui)
  expect(overlayRect(ui).getAttribute('stroke')).toBe('#2F9E44')
})

it('select 工具下双击文字进编辑，提交为空就删掉这条文字', async () => {
  const ui = mount()
  await painted(ui)
  // 文字工具点一下 -> 输入 '标签' -> 回车。
  await fireEvent.click(ui.getByRole('button', { name: '文字' }))
  const canvas = ui.getByRole('application', { name: '图片标注画布' })
  canvas.setPointerCapture = vi.fn()
  await fireEvent.pointerDown(canvas, { button: 0, pointerId: 41, clientX: 110, clientY: 120 })
  const field = ui.getByPlaceholderText('输入文字，回车确认')
  await fireEvent.update(field, '标签')
  await fireEvent.keyDown(field, { key: 'Enter' })
  await waitFor(() => expect(ui.container.querySelector('.sketch-overlay text')).toBeTruthy())

  // 双击它进编辑：原文字不画，输入框带着原文出现。
  await fireEvent.dblClick(sheet(ui), { clientX: 110, clientY: 120 })
  const editor = ui.getByPlaceholderText('输入文字，回车确认') as HTMLInputElement
  expect(editor.value).toBe('标签')
  expect(ui.container.querySelector('.sketch-overlay text')).toBeNull()

  // 提交为空 -> 这条文字被删掉。
  await fireEvent.update(editor, '  ')
  await fireEvent.blur(editor)
  await waitFor(() => expect(ui.container.querySelector('.sketch-overlay text')).toBeNull())
  // 撤销能把这条文字找回来。
  await fireEvent.keyDown(window, { key: 'z', code: 'KeyZ', metaKey: true })
  await waitFor(() => expect(ui.container.querySelector('.sketch-overlay text')).toBeTruthy())
})

it('text 工具下单击已有文字也进编辑', async () => {
  const ui = mount()
  await painted(ui)
  await fireEvent.click(ui.getByRole('button', { name: '文字' }))
  const canvas = ui.getByRole('application', { name: '图片标注画布' })
  canvas.setPointerCapture = vi.fn()
  await fireEvent.pointerDown(canvas, { button: 0, pointerId: 51, clientX: 110, clientY: 120 })
  const field = ui.getByPlaceholderText('输入文字，回车确认')
  await fireEvent.update(field, '标签')
  await fireEvent.keyDown(field, { key: 'Enter' })
  await waitFor(() => expect(ui.container.querySelector('.sketch-overlay text')).toBeTruthy())

  // 再点文字工具，点已有文字 -> 进编辑（带出原文）。
  await fireEvent.click(ui.getByRole('button', { name: '文字' }))
  const layer = ui.getByRole('application', { name: '图片标注画布' })
  await fireEvent.pointerDown(layer, { button: 0, pointerId: 52, clientX: 110, clientY: 120 })
  const editor = ui.getByPlaceholderText('输入文字，回车确认') as HTMLInputElement
  expect(editor.value).toBe('标签')
})

it('点在空白处拖仍然是区域选择：交给芝士', async () => {
  const ui = mount()
  await painted(ui)
  const region = ui.container.querySelector('.raster-region') as HTMLElement
  region.setPointerCapture = vi.fn()
  await fireEvent.pointerDown(region, { button: 0, pointerId: 61, clientX: 350, clientY: 200 })
  await fireEvent.pointerMove(region, { pointerId: 61, clientX: 420, clientY: 240 })
  await fireEvent.pointerUp(region, { pointerId: 61, clientX: 420, clientY: 240 })
  await waitFor(() => expect(ui.emitted('region')).toHaveLength(1))
})

it('已有选中时点空白：放下选中，空白拖照旧给区域选择', async () => {
  const ui = mount()
  await painted(ui)
  await drawRect(ui)
  expect(handleCount(ui)).toBe(8)
  const region = ui.container.querySelector('.raster-region') as HTMLElement
  region.setPointerCapture = vi.fn()
  // 离把手足够远（外框在显示 (50,50)-(300,200)）。
  await fireEvent.pointerDown(region, { button: 0, pointerId: 62, clientX: 450, clientY: 240 })
  expect(handleCount(ui)).toBe(0)
  await fireEvent.pointerMove(region, { pointerId: 62, clientX: 470, clientY: 250 })
  await fireEvent.pointerUp(region, { pointerId: 62, clientX: 470, clientY: 250 })
  await waitFor(() => expect(ui.emitted('region')).toHaveLength(1))
})

it('自由笔不可选：画完不自动选中，工具也不回 select', async () => {
  const ui = mount()
  await painted(ui)
  await fireEvent.click(ui.getByRole('button', { name: '自由画笔' }))
  const layer = ui.getByRole('application', { name: '图片标注画布' })
  layer.setPointerCapture = vi.fn()
  await fireEvent.pointerDown(layer, { button: 0, pointerId: 71, clientX: 60, clientY: 70 })
  await fireEvent.pointerMove(layer, { pointerId: 71, clientX: 90, clientY: 90 })
  await fireEvent.pointerUp(layer, { pointerId: 71, clientX: 90, clientY: 90 })
  await waitFor(() => expect(ui.container.querySelector('.sketch-overlay polyline')).toBeTruthy())
  expect(handleCount(ui)).toBe(0)
  expect(ui.getByRole('button', { name: '自由画笔' }).getAttribute('aria-pressed')).toBe('true')
})

it('拖角把手缩放矩形：对边不动', async () => {
  const ui = mount()
  await painted(ui)
  await drawRect(ui)
  // 屏上外框显示 (50,50)-(300,200)，即 client (60,70)-(310,220)；右下角 client (310,220)。
  const layer = sheet(ui)
  await fireEvent.pointerDown(layer, { button: 0, pointerId: 81, clientX: 310, clientY: 220 })
  await fireEvent.pointerMove(layer, { pointerId: 81, clientX: 410, clientY: 270 })
  await fireEvent.pointerUp(layer, { pointerId: 81, clientX: 410, clientY: 270 })
  const box = overlayRect(ui)
  await waitFor(() => expect(Number(box.getAttribute('width'))).toBe(350))
  expect(Number(box.getAttribute('height'))).toBe(200)
  expect(Number(box.getAttribute('x'))).toBe(50)
})

it('拖角把手 + Shift 把矩形改成正方形', async () => {
  const ui = mount()
  await painted(ui)
  await drawRect(ui)
  const layer = sheet(ui)
  await fireEvent.pointerDown(layer, { button: 0, pointerId: 82, clientX: 310, clientY: 220 })
  // 拖到 client (410,240) 即显示 (400,220)：dx=100、dy=20 -> 正方形取 100。
  await fireEvent.pointerMove(layer, { pointerId: 82, clientX: 410, clientY: 240, shiftKey: true })
  await fireEvent.pointerUp(layer, { pointerId: 82, clientX: 410, clientY: 240, shiftKey: true })
  const box = overlayRect(ui)
  // 不按 Shift 时高会是 170；按了取更大的那个位移（横 100 > 纵 20），拉成 350×350 的正方形。
  await waitFor(() => expect(Number(box.getAttribute('width'))).toBe(350))
  expect(Number(box.getAttribute('height'))).toBe(350)
})

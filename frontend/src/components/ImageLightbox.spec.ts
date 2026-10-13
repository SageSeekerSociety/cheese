// 一张图放大看的这一层。人能在读代码之前说出来的几条：点空白、点右上角、按 Esc 都关掉；
// 拖动过的那一下不算「点了空白」——那是想看细节，不是想走；滚轮和双击放大，双击第二下
// 缩回原样；缩到头就是原样，不会再小。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import ImageLightbox from './ImageLightbox.vue'

import { setLocale } from '@/i18n'

// These assertions read the Chinese copy; the English rendering is checked in its own case.
beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

function mount() {
  const onClose = vi.fn()
  const utils = render(ImageLightbox, {
    props: { src: 'blob:image-1', alt: '图.png', onClose },
    global: { plugins: [createVuetify({ components, directives })] },
  })
  return { onClose, ...utils }
}

/** 这一层里「图有多大」全写在 transform 上，所以断言也读它。 */
function zoomOf(el: HTMLElement) {
  const match = /scale\(([\d.]+)\)/.exec(el.style.transform)
  return match ? Number(match[1]) : 1
}

function stageOf() {
  return document.querySelector('.lightbox') as HTMLElement
}

function figureOf() {
  return screen.getByAltText('图.png') as HTMLElement
}

/** 一条手指/鼠标轨迹。用真的 PointerEvent，让组件按浏览器的顺序收到。 */
function pointer(el: Element, type: string, init: Partial<PointerEventInit> = {}) {
  el.dispatchEvent(
    new PointerEvent(type, { bubbles: true, cancelable: true, pointerId: 1, pointerType: 'mouse', button: 0, ...init })
  )
}

it('draws the bytes it was handed, and neither fetches nor gives them back itself', () => {
  const createObjectURL = vi.spyOn(URL, 'createObjectURL')
  const revokeObjectURL = vi.spyOn(URL, 'revokeObjectURL')

  mount()

  // 地址是调用方（AttachmentImage）取好字节转出来的：这一层只管画，还回去也归它管。
  expect(figureOf().getAttribute('src')).toBe('blob:image-1')
  expect(createObjectURL).not.toHaveBeenCalled()
  expect(revokeObjectURL).not.toHaveBeenCalled()

  vi.restoreAllMocks()
})

it('closes when the reader clicks the empty space around the picture', async () => {
  const { onClose } = mount()

  await fireEvent.click(stageOf())

  expect(onClose).toHaveBeenCalledTimes(1)
})

it('closes from the button in the corner', async () => {
  const { onClose } = mount()

  await fireEvent.click(screen.getByRole('button', { name: '关闭' }))

  expect(onClose).toHaveBeenCalledTimes(1)
})

it('closes on Escape', async () => {
  const { onClose } = mount()

  await fireEvent.keyDown(window, { key: 'Escape' })

  expect(onClose).toHaveBeenCalledTimes(1)
})

it('leaves the picture up when the reader pokes the picture itself', async () => {
  const { onClose } = mount()

  await fireEvent.click(figureOf())

  expect(onClose).not.toHaveBeenCalled()
})

// 放大之后想挪一挪看边角，松手那一下正好落在图外面的空白上：那是拖完松手，不是要走。
it('stays open when a drag happens to end over the empty space', async () => {
  const { onClose } = mount()
  const stage = stageOf()

  pointer(stage, 'pointerdown', { clientX: 100, clientY: 100 })
  pointer(stage, 'pointermove', { clientX: 160, clientY: 140 })
  pointer(stage, 'pointerup', { clientX: 160, clientY: 140 })
  await fireEvent.click(stage)

  expect(onClose).not.toHaveBeenCalled()
})

it('zooms in on the wheel and never shrinks the picture below its whole size', async () => {
  mount()
  const stage = stageOf()
  const figure = figureOf()

  await fireEvent.wheel(stage, { deltaY: -200 })
  expect(zoomOf(figure)).toBeGreaterThan(1)

  // 反着一路滚回去：最小就是「整张图看得见」那一档，再滚也不会缩成一粒。
  await fireEvent.wheel(stage, { deltaY: 2000 })
  expect(zoomOf(figure)).toBe(1)
})

it('zooms in on a double click and puts it back on the next one', async () => {
  mount()
  const figure = figureOf()

  await fireEvent.dblClick(figure)
  expect(zoomOf(figure)).toBeGreaterThan(1)

  await fireEvent.dblClick(figure)
  expect(zoomOf(figure)).toBe(1)
})

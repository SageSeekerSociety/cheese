// 在对话里打开的看图浮层：左右切到这段对话里的别的图（那些字节由浮层自己取、关掉时
// 自己还，点开的那张不归它还）；「写字」「打码」「在右侧打开并框选批注」把这张图交给
// 右侧面板并选好工具。不在对话里（没人给这些能力）时，只看这一张，不出交接的按钮。
import { ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import ImageLightbox from './ImageLightbox.vue'

import { setLocale } from '@/i18n'
import { ATTACHMENT_SOURCE } from '@/lib/attachmentSource'
import { CHAT_IMAGE_ACTIONS, type ChatImageActions } from '@/lib/chatImages'

beforeEach(() => setLocale('zh-CN'))
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

const imageUrl = vi.fn((_topic: string, path: string) => Promise.resolve(`blob:${path}`))
const source = {
  imageUrl,
  mediaUrl: () => Promise.reject(new Error('x')),
  documentPdf: () => Promise.reject(new Error('x')),
  fileBytes: () => Promise.reject(new Error('x')),
}

function mount(actions: ChatImageActions | null) {
  const onClose = vi.fn()
  const provide: Record<symbol, unknown> = { [ATTACHMENT_SOURCE as symbol]: source }
  if (actions) provide[CHAT_IMAGE_ACTIONS as symbol] = actions
  const utils = render(ImageLightbox, {
    props: { src: 'blob:mine', alt: 'a.png', topicId: 'room', path: 'up/a.png', onClose },
    global: { plugins: [createVuetify({ components, directives })], provide },
  })
  return { onClose, ...utils }
}

function chat(): ChatImageActions {
  return {
    gallery: () => ['up/a.png', 'up/b.png', 'up/c.png'],
    openInPanel: vi.fn(),
    attach: vi.fn(),
    canAttach: () => true,
  }
}

it('steps to the next picture in the conversation with the arrow key, and back', async () => {
  mount(chat())
  expect(screen.getByText('a.png · 1 / 3')).toBeTruthy()

  await fireEvent.keyDown(window, { key: 'ArrowRight' })
  await waitFor(() => expect(screen.getByAltText('b.png').getAttribute('src')).toBe('blob:up/b.png'))
  expect(screen.getByText('b.png · 2 / 3')).toBeTruthy()

  await fireEvent.keyDown(window, { key: 'ArrowLeft' })
  expect(screen.getByAltText('a.png').getAttribute('src')).toBe('blob:mine')
})

it('gives back the pictures it fetched itself when it closes, never the one it was handed', async () => {
  const revoke = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
  const ui = mount(chat())
  await waitFor(() => expect(imageUrl).toHaveBeenCalledWith('room', 'up/b.png'))
  await Promise.resolve()

  ui.unmount()

  expect(revoke).toHaveBeenCalledWith('blob:up/b.png')
  expect(revoke).not.toHaveBeenCalledWith('blob:mine')
})

it('hands the picture to the panel with the chosen tool and closes', async () => {
  const actions = chat()
  const { onClose } = mount(actions)

  await fireEvent.click(screen.getByRole('button', { name: '打码' }))

  expect(actions.openInPanel).toHaveBeenCalledWith('up/a.png', 'redact')
  expect(onClose).toHaveBeenCalled()
})

it('outside a conversation shows only this picture and offers no hand-off', () => {
  mount(null)
  expect(screen.queryByRole('button', { name: '下一张（→）' })).toBeNull()
  expect(screen.queryByRole('button', { name: '写字' })).toBeNull()
  expect(screen.queryByText('在右侧打开并框选批注')).toBeNull()
})

it('marks the view as changed after a turn, and Reset puts it back', async () => {
  mount(chat())
  expect(screen.queryByTestId('lightbox-save')).toBeNull()

  await fireEvent.click(screen.getByRole('button', { name: '旋转 90°' }))
  expect(screen.getByTestId('lightbox-save')).toBeTruthy()

  await fireEvent.click(screen.getByText('还原'))
  expect(screen.queryByTestId('lightbox-save')).toBeNull()
})

it('leaves the arrow keys to a focused slider instead of switching pictures', async () => {
  mount(chat())
  await fireEvent.click(screen.getByRole('button', { name: '亮度和饱和度' }))
  const slider = screen.getAllByRole('slider')[0]
  await fireEvent.focus(slider)

  await fireEvent.keyDown(slider, { key: 'ArrowRight' })

  expect(screen.getByText('a.png · 1 / 3')).toBeTruthy()
})

it('applies the crop on Enter, claiming the key so the focused button gets no turn', async () => {
  const { onClose } = mount(chat())
  await fireEvent.click(screen.getByRole('button', { name: '裁剪' }))

  // 真实浏览器里焦点还在「裁剪」按钮上，回车会先把那颗按钮按下去（再进一次裁剪、
  // 或者关掉浮层）。这一下要由裁剪自己收掉——`preventDefault` 之后浏览器不再激活按钮。
  const enter = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })
  window.dispatchEvent(enter)
  await waitFor(() => expect(screen.queryByTestId('lightbox-crop-actions')).toBeNull())

  expect(enter.defaultPrevented).toBe(true)
  expect(screen.getByTestId('lightbox-save')).toBeTruthy()
  expect(onClose).not.toHaveBeenCalled()
})

it('offers “放进输入框” only when the composer can take a file', async () => {
  const actions = chat()
  actions.canAttach = () => false
  mount(actions)
  await fireEvent.click(screen.getByRole('button', { name: '旋转 90°' }))
  expect(screen.queryByText('放进输入框')).toBeNull()
})

it('freezes the transform controls while a crop box is being placed', async () => {
  mount(chat())
  await fireEvent.click(screen.getByRole('button', { name: '裁剪' }))

  // 裁剪框是按此刻图的位置量的：这期间再把图转一下，框就落在别的像素上，
  // 导出的那块和屏幕上框住的不是同一处。
  expect(screen.getByRole('button', { name: '旋转 90°' })).toHaveProperty('disabled', true)
  expect(screen.getByRole('button', { name: '放大' })).toHaveProperty('disabled', true)
})

it('still steps with the arrow keys while focus is on the toolbar (it starts there)', async () => {
  mount(chat())
  const close = screen.getByRole('button', { name: '关闭' })
  expect(document.activeElement).toBe(close)

  await fireEvent.keyDown(close, { key: 'ArrowRight' })

  await waitFor(() => expect(screen.getByText('b.png · 2 / 3')).toBeTruthy())
})

it('stays on the same picture when the conversation changes under it', async () => {
  const paths = ref(['up/a.png', 'up/b.png', 'up/c.png'])
  const actions = chat()
  actions.gallery = () => paths.value
  mount(actions)
  await fireEvent.keyDown(window, { key: 'ArrowRight' })
  await waitFor(() => expect(screen.getByText('b.png · 2 / 3')).toBeTruthy())

  // A message lands above the open picture: the list grows, and b is no longer second.
  paths.value = ['up/new.png', 'up/a.png', 'up/b.png', 'up/c.png']

  await waitFor(() => expect(screen.getByText('b.png · 3 / 4')).toBeTruthy())
})

it('stays on the picture picked from the film strip when the conversation changes under it', async () => {
  const paths = ref(['up/a.png', 'up/b.png', 'up/c.png'])
  const actions = chat()
  actions.gallery = () => paths.value
  mount(actions)

  await fireEvent.click(screen.getByRole('button', { name: '看第 3 张' }))
  await waitFor(() => expect(screen.getByText('c.png · 3 / 3')).toBeTruthy())

  paths.value = ['up/new.png', 'up/a.png', 'up/b.png', 'up/c.png']

  // 从缩略图条点过去也要算「现在看的是 c」：不然位置一变，浮层按老地址跳回 b。
  await waitFor(() => expect(screen.getByText('c.png · 4 / 4')).toBeTruthy())
})

it('keeps a picture’s own edits with the picture when the list shifts under it', async () => {
  const paths = ref(['up/a.png', 'up/b.png', 'up/c.png'])
  const actions = chat()
  actions.gallery = () => paths.value
  mount(actions)
  await fireEvent.keyDown(window, { key: 'ArrowRight' })
  await waitFor(() => expect(screen.getByText('b.png · 2 / 3')).toBeTruthy())

  await fireEvent.click(screen.getByRole('button', { name: '旋转 90°' }))
  await waitFor(() => expect(screen.getByAltText('b.png').style.transform).toContain('rotate(90deg)'))

  paths.value = ['up/new.png', 'up/a.png', 'up/b.png', 'up/c.png']
  await waitFor(() => expect(screen.getByText('b.png · 3 / 4')).toBeTruthy())

  // 调整跟着这张图走，不是跟着它原来排第几：否则它在这里变回原样，转过的样子落到 b 的位置上。
  expect(screen.getByAltText('b.png').style.transform).toContain('rotate(90deg)')
})

it('leaves crop mode when the picture it framed is deleted and another takes its place', async () => {
  const paths = ref(['up/a.png', 'up/b.png', 'up/c.png'])
  const actions = chat()
  actions.gallery = () => paths.value
  mount(actions)
  await fireEvent.keyDown(window, { key: 'ArrowRight' })
  await waitFor(() => expect(screen.getByText('b.png · 2 / 3')).toBeTruthy())

  await fireEvent.click(screen.getByRole('button', { name: '裁剪' }))
  await waitFor(() => expect(screen.queryByTestId('lightbox-crop')).toBeTruthy())

  // b 那条消息被删了：c 还在第 2 个，位置没变，看的已经是 c。
  paths.value = ['up/a.png', 'up/c.png']

  await waitFor(() => expect(screen.getByText('c.png · 2 / 2')).toBeTruthy())
  expect(screen.queryByTestId('lightbox-crop')).toBeNull()
})

it('keeps the crop layer on the picture that arrives after the crop began', async () => {
  const paths = ref(['up/a.png', 'up/b.png'])
  const actions = chat()
  actions.gallery = () => paths.value
  // b 的字节迟迟没到：裁剪开始时浮层里连 <img> 都没有。
  let release!: (url: string) => void
  imageUrl.mockImplementationOnce(() => new Promise<string>((r) => (release = r)))
  // 只靠观察者兜底的环境（观察者不回调）：新节点定下大小这件事只有「一直量」接得住。
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
  vi.spyOn(HTMLImageElement.prototype, 'naturalWidth', 'get').mockReturnValue(1200)
  vi.spyOn(HTMLImageElement.prototype, 'naturalHeight', 'get').mockReturnValue(800)

  // 图在浮层里占的那块：字节到了、load 之后才定下来，在那之前量到的是零。
  let sized = false
  const rect = (left: number, top: number, width: number, height: number) =>
    ({ left, top, width, height, x: left, y: top, right: left + width, bottom: top + height }) as DOMRect
  vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
    if (this.classList.contains('lightbox')) return rect(0, 0, 1200, 800)
    if (this.tagName === 'IMG' && sized) return rect(100, 110, 800, 600)
    return rect(0, 0, 0, 0)
  })

  mount(actions)
  await fireEvent.keyDown(window, { key: 'ArrowRight' })
  await waitFor(() => expect(screen.getByText('b.png · 2 / 2')).toBeTruthy())
  expect(screen.queryByAltText('b.png')).toBeNull()
  await fireEvent.click(screen.getByRole('button', { name: '裁剪' }))

  release('blob:up/b.png')
  await waitFor(() => expect(screen.getByAltText('b.png')).toBeTruthy())
  // 新节点刚换上时还没定下大小，过几帧才铺开。
  setTimeout(() => (sized = true), 50)

  await waitFor(() => {
    const style = (screen.getByTestId('lightbox-crop') as HTMLElement).style
    expect([style.left, style.top, style.width, style.height]).toEqual(['100px', '110px', '800px', '600px'])
  })
})

it('keeps one measuring loop when the picture arrives as the crop opens', async () => {
  const paths = ref(['up/a.png', 'up/b.png'])
  const actions = chat()
  actions.gallery = () => paths.value
  let release!: (url: string) => void
  imageUrl.mockImplementationOnce(() => new Promise<string>((r) => (release = r)))

  // 数一数同时挂着几个观察者：同一帧里两个入口都进来时，被覆盖的那个再也停不掉。
  const observers: { live: boolean }[] = []
  vi.stubGlobal(
    'ResizeObserver',
    class {
      live = true
      constructor() {
        observers.push(this)
      }
      observe() {}
      unobserve() {}
      disconnect() {
        this.live = false
      }
    }
  )

  mount(actions)
  await fireEvent.keyDown(window, { key: 'ArrowRight' })
  await waitFor(() => expect(screen.getByText('b.png · 2 / 2')).toBeTruthy())

  const clicking = fireEvent.click(screen.getByRole('button', { name: '裁剪' }))
  release('blob:up/b.png')
  await clicking
  await waitFor(() => expect(screen.getByAltText('b.png')).toBeTruthy())

  expect(observers.filter((o) => o.live).length).toBe(1)
})

it('refits the picture when the strip appears and takes room away', async () => {
  const paths = ref(['up/a.png'])
  const actions = chat()
  actions.gallery = () => paths.value
  // 这一层里「图多大就摆多大」要真尺寸：给一个比窗口大的原图和一块固定的窗口。
  vi.spyOn(HTMLImageElement.prototype, 'naturalWidth', 'get').mockReturnValue(1200)
  vi.spyOn(HTMLImageElement.prototype, 'naturalHeight', 'get').mockReturnValue(800)
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(1000)
  vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(800)

  mount(actions)
  await fireEvent.load(screen.getByAltText('a.png'))
  // 只有这一张：左右按钮和缩略图条都不在，两边只让开 24px。
  await waitFor(() => expect(screen.getByAltText('a.png').style.width).toBe('952px'))

  // 对话里又来了一张：左右按钮和缩略图条出来，两边让开 76px，底下多出一条缩略图条。
  paths.value = ['up/a.png', 'up/b.png']

  await waitFor(() => expect(screen.getByAltText('a.png').style.width).toBe('848px'))
})

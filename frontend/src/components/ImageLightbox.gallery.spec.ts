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

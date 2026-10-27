// 在对话里打开的看图浮层：左右切到这段对话里的别的图（那些字节由浮层自己取、关掉时
// 自己还，点开的那张不归它还）；「写字」「打码」「在右侧打开并框选批注」把这张图交给
// 右侧面板并选好工具。不在对话里（没人给这些能力）时，只看这一张，不出交接的按钮。
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
  return { gallery: () => ['up/a.png', 'up/b.png', 'up/c.png'], openInPanel: vi.fn(), attach: vi.fn() }
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

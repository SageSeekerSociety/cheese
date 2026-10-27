/**
 * 对话里的文档卡片：封面等卡片进了视野才画；浏览器没有 IntersectionObserver 时直接
 * 画，方格不能一直空着。封面取不到时摆类型图标（AttachmentDocThumb 那一层）。
 */
import { createVuetify } from 'vuetify'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import MessageFileCard from './MessageFileCard.vue'

import { setLocale } from '@/i18n'
import { ATTACHMENT_SOURCE } from '@/lib/attachmentSource'

const documentPdf = vi.fn()
const fileBytes = vi.fn()
const source = {
  imageUrl: () => Promise.reject(new Error('x')),
  mediaUrl: () => Promise.reject(new Error('x')),
  documentPdf,
  fileBytes,
}

let observers: { cb: IntersectionObserverCallback; el: Element | null }[] = []
class FakeObserver {
  cb: IntersectionObserverCallback
  constructor(cb: IntersectionObserverCallback) {
    this.cb = cb
    observers.push({ cb, el: null })
  }
  observe(el: Element) {
    observers[observers.length - 1].el = el
  }
  unobserve() {}
  disconnect() {}
  takeRecords() {
    return []
  }
}

beforeEach(() => {
  setLocale('zh-CN')
  observers = []
  documentPdf.mockRejectedValue(new Error('not converted'))
  fileBytes.mockRejectedValue(new Error('no bytes'))
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function mount(path = 'output/方案.docx') {
  return render(MessageFileCard, {
    props: { topicId: 'room', path, name: '方案.docx', kind: '文档' },
    global: { plugins: [createVuetify()], provide: { [ATTACHMENT_SOURCE as symbol]: source } },
  })
}

it('卡片进视野之前不去转格式，进了视野才画封面', async () => {
  vi.stubGlobal('IntersectionObserver', FakeObserver)
  const ui = mount()
  await waitFor(() => expect(observers.length).toBeGreaterThan(0))
  expect(documentPdf).not.toHaveBeenCalled()
  expect(ui.container.querySelector('.im-doc__cover .att-face')).toBeNull()

  observers.at(-1)!.cb([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver)
  await waitFor(() => expect(documentPdf).toHaveBeenCalledWith('room', 'output/方案.docx'))
  // 转不出来：方格里摆这个类型的图标，不是空着。
  await waitFor(() => expect(ui.container.querySelector('.im-doc__cover .v-icon')).toBeTruthy())
})

it('浏览器没有 IntersectionObserver：直接画，不让方格一直空着', async () => {
  // 老内核里这个名字整个不存在（不是存在但为空），按那个样子摘掉。
  const win = window as unknown as Record<string, unknown>
  const saved = win.IntersectionObserver
  delete win.IntersectionObserver
  try {
    const ui = mount('output/结题报告.pdf')
    await waitFor(() => expect(fileBytes).toHaveBeenCalledWith('room', 'output/结题报告.pdf'))
    expect(ui.container.querySelector('.im-doc__cover .att-face')).toBeTruthy()
  } finally {
    win.IntersectionObserver = saved
  }
})

it('点卡片交出去开', async () => {
  vi.stubGlobal('IntersectionObserver', FakeObserver)
  const ui = mount()
  await fireEvent.click(ui.getByTestId('message-file-card'))
  expect(ui.emitted().open).toHaveLength(1)
})

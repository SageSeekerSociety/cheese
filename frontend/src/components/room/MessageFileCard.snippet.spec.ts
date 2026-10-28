/**
 * 取不到首页摘要，卡片不能跟着没。
 *
 * 摘要是在 watch 回调里取的，而 watch 回调里抛出去的错会被对话时间线的 ErrorBoundary
 * 接走——那一整段（整条对话）会被换成「无法显示这块内容」，只为了两行可读可不读的
 * 字。修前这里会抛给边界（这一条红），修后卡片照常、只是没有摘要。
 *
 * pdf.js 在这一条里是桩：要演的是「取文字那一步失败」时这张卡片的反应，不是 pdf.js
 * 能不能解析。它能画、能报页数，只有交文字那条流是坏的。
 */
import { defineComponent, h, onErrorCaptured, type PropType } from 'vue'
import { createVuetify } from 'vuetify'
import { cleanup, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import MessageFileCard from './MessageFileCard.vue'

import { setLocale } from '@/i18n'
import { ATTACHMENT_SOURCE } from '@/lib/attachmentSource'
import { FILE_FACTS_SOURCE } from '@/lib/fileFactsSource'

vi.mock('pdfjs-dist/legacy/build/pdf.worker.min.mjs?url', () => ({ default: '/worker.mjs' }))
vi.mock('pdfjs-dist/legacy/build/pdf.mjs', () => ({
  GlobalWorkerOptions: {},
  getDocument: () => ({
    destroy: vi.fn().mockResolvedValue(undefined),
    promise: Promise.resolve({
      numPages: 1,
      destroy: vi.fn().mockResolvedValue(undefined),
      getPage: async () => ({
        getViewport: ({ scale }: { scale: number }) => ({ width: 595 * scale, height: 842 * scale }),
        render: () => ({ promise: Promise.resolve(), cancel: vi.fn() }),
        // 交文字这一条坏了：卡片该当没有摘要，不该把整段对话带走。
        streamTextContent: () => {
          throw new Error('这一页的文字取不出来')
        },
      }),
    }),
  }),
  TextLayer: class {
    constructor(private options: { container: HTMLElement }) {}
    cancel() {}
    async render() {
      const span = document.createElement('span')
      span.textContent = '首页的原话'
      this.options.container.appendChild(span)
    }
  },
}))

let observers: { cb: IntersectionObserverCallback }[] = []
class FakeObserver {
  cb: IntersectionObserverCallback
  constructor(cb: IntersectionObserverCallback) {
    this.cb = cb
    observers.push({ cb })
  }
  observe() {}
  unobserve() {}
  disconnect() {}
  takeRecords() {
    return []
  }
}

/** 卡片外面套的那一层：时间线的 ErrorBoundary 就是这么接错的。 */
const escaped: unknown[] = []
const Host = defineComponent({
  props: { card: { type: Object as PropType<typeof MessageFileCard>, required: true } },
  setup(props) {
    onErrorCaptured((error) => {
      escaped.push(error)
      return false
    })
    return () =>
      h(props.card, {
        topicId: 'room',
        path: 'output/结题报告.pdf',
        name: '结题报告.pdf',
        kind: '文档',
        postedAt: '2026-10-27T13:26:49Z',
        downloadable: true,
      })
  },
})

beforeEach(() => {
  setLocale('zh-CN')
  observers = []
  escaped.length = 0
  vi.stubGlobal('IntersectionObserver', FakeObserver)
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it('首页的文字取不出来时，卡片照常在，只是没有摘要', async () => {
  const ui = render(Host, {
    props: { card: MessageFileCard },
    global: {
      plugins: [createVuetify()],
      provide: {
        [ATTACHMENT_SOURCE as symbol]: {
          imageUrl: () => Promise.reject(new Error('x')),
          mediaUrl: () => Promise.reject(new Error('x')),
          documentPdf: () => Promise.reject(new Error('不用转')),
          fileBytes: () => Promise.resolve(new ArrayBuffer(8)),
        },
        [FILE_FACTS_SOURCE as symbol]: {
          versions: () => Promise.resolve([]),
          versionBytes: () => Promise.reject(new Error('x')),
          livePreview: () => Promise.resolve(null),
        },
      },
    },
  })
  await waitFor(() => expect(observers.length).toBeGreaterThan(0))
  observers.at(-1)!.cb([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver)
  // 等取文字那一步真的跑过：它抛在这一轮里，抛出去就会被上面那层记下来。
  await new Promise((resolve) => setTimeout(resolve, 50))

  expect(escaped).toEqual([])
  expect(screen.getByText('结题报告.pdf')).toBeTruthy()
  expect(screen.getByText('在右侧打开')).toBeTruthy()
  expect(ui.container.querySelector('.fcard__snippet')).toBeNull()
})

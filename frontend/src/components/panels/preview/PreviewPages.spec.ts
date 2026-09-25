/**
 * 分页文档的阅读视图。
 *
 * 这一条守的是一个「什么都不显示」的失败：挂载时 ResizeObserver 会立刻回调一次，
 * 200 毫秒后触发重排，而 pdf.js 解析一份文档要一秒多。如果重排推进的是「加载代际」，
 * 解析回来时这份文档就已经过期，open() 空手返回、loading 又没人关——预览永远转圈，
 * 既不报错也不超时。重排作废的只该是已经画出来的页，所以两份代际必须分开。
 *
 * 修复前这条会红：canvas 永远不出现。
 */
import { cleanup, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import PreviewPages from './PreviewPages.vue'

/** pdf.js 是假的：这条测的是「谁作废了谁」，不是 pdf.js 会不会解析。
 *  `calls` 是自己数的，不用 vi.fn：组件是动态 import（`await import('pdfjs-dist')`），
 *  这个假模块要等 library() 真的跑起来才建起来——beforeEach 里够不着它。 */
const pdf = vi.hoisted(() => ({
  resolveDoc: null as null | ((doc: unknown) => void),
  calls: 0,
}))

vi.mock('pdfjs-dist', () => {
  const getDocument = () => {
    pdf.calls += 1
    return {
      promise: new Promise((resolve) => {
        pdf.resolveDoc = resolve
      }),
      destroy: () => {},
    }
  }
  class TextLayer {
    async render() {}
  }
  return { GlobalWorkerOptions: {}, getDocument, TextLayer, version: 'test' }
})

vi.mock('pdfjs-dist/build/pdf.worker.min.mjs?url', () => ({ default: '/pdf.worker.mjs' }))

/** 一页假的：viewport 与 render 都够组件把画布造出来。 */
function page(number: number) {
  return {
    number,
    getViewport: ({ scale }: { scale: number }) => ({ width: 600 * scale, height: 800 * scale }),
    render: () => ({ promise: Promise.resolve() }),
    streamTextContent: () => ({}),
  }
}

const DOCUMENT = { numPages: 2, getPage: (n: number) => Promise.resolve(page(n)) }

/** 第二页取不出来——一页坏掉不该让整份文档一声不响地留白。 */
const ONE_BAD_PAGE = {
  numPages: 2,
  getPage: (n: number) => (n === 2 ? Promise.reject(new Error('页流损坏')) : Promise.resolve(page(n))),
}

/** 浏览器的 ResizeObserver 在 observe() 时会立刻回调一次 —— 这一条就是被它绊倒的。 */
let resize: (() => void) | null = null

beforeAll(() => {
  vi.stubGlobal(
    'ResizeObserver',
    class {
      constructor(cb: () => void) {
        resize = cb
      }
      observe() {
        setTimeout(() => resize?.(), 0)
      }
      unobserve() {}
      disconnect() {}
    }
  )
  // 页一进视野就画，跟真实浏览器的初次回调一致。
  vi.stubGlobal(
    'IntersectionObserver',
    class {
      constructor(private cb: (entries: unknown[], self: unknown) => void) {}
      observe(el: Element) {
        setTimeout(() => this.cb([{ isIntersecting: true, target: el }], this), 0)
      }
      unobserve() {}
      disconnect() {}
    }
  )
})

beforeEach(() => {
  resize = null
  pdf.resolveDoc = null
  pdf.calls = 0
})

afterEach(cleanup)

function mount() {
  return render(PreviewPages, {
    props: { data: new ArrayBuffer(8) },
    global: { stubs: { VIcon: true, VProgressCircular: true } },
  })
}

describe('分页文档的阅读视图', () => {
  it('挂载时的初次重排不会把还在解析的文档丢掉', async () => {
    const { container } = mount()

    await waitFor(() => expect(pdf.calls).toBeGreaterThan(0))
    // 重排的防抖是 200 毫秒，而这里让它在 pdf.js「还没解析完」的时候先跑掉——
    // 这正是真机上必然发生的次序（解析要一秒多）。
    await new Promise((r) => setTimeout(r, 260))
    expect(container.querySelector('canvas')).toBeNull()

    pdf.resolveDoc?.(DOCUMENT)

    await waitFor(() => expect(container.querySelectorAll('canvas')).toHaveLength(2))
  })

  it('某一页画不出来时，就在那一页说清是第几页、为什么', async () => {
    const { container } = mount()
    await waitFor(() => expect(pdf.calls).toBeGreaterThan(0))
    pdf.resolveDoc?.(ONE_BAD_PAGE)

    // 坏的那一页不许把好的那一页也带走。
    await waitFor(() => expect(container.querySelectorAll('canvas')).toHaveLength(1))
    expect(container.textContent).toContain('第 2 页无法显示')
    expect(container.textContent).toContain('页流损坏')
  })

  it('文档画出来之后真正变了宽度，页还是重画', async () => {
    const { container } = mount()
    await waitFor(() => expect(pdf.calls).toBeGreaterThan(0))
    pdf.resolveDoc?.(DOCUMENT)
    await waitFor(() => expect(container.querySelectorAll('canvas')).toHaveLength(2))

    // 一次真实的宽度变化：重排作废已画的页，随后按新宽度重画。
    resize?.()
    await new Promise((r) => setTimeout(r, 260))
    await waitFor(() => expect(container.querySelectorAll('canvas')).toHaveLength(2))
  })
})

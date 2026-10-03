import { nextTick, reactive } from 'vue'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import PreviewSlides from './PreviewSlides.vue'

import { setLocale } from '@/i18n'

const pdf = vi.hoisted(() => ({
  requests: [] as Array<{
    resolve: (doc: unknown) => void
    reject: (error: Error) => void
    destroy: ReturnType<typeof vi.fn>
  }>,
  renders: [] as Array<{ number: number; cancel: ReturnType<typeof vi.fn>; resolve: () => void }>,
  holdRender: false,
  text: 'PDF actual page text '.repeat(20),
  // 文字层里那一层可选的 span：默认一整页就一句，需要时换成几段，好让选区两侧有字。
  spans: ['Selectable original text'] as string[],
}))
vi.mock('pdfjs-dist/legacy/build/pdf.worker.min.mjs?url', () => ({ default: '/worker.mjs' }))
vi.mock('pdfjs-dist/legacy/build/pdf.mjs', () => ({
  GlobalWorkerOptions: {},
  getDocument: () => {
    const destroy = vi.fn().mockResolvedValue(undefined)
    const promise = new Promise((resolve, reject) => pdf.requests.push({ resolve, reject, destroy }))
    return { promise, destroy }
  },
  TextLayer: class {
    constructor(private options: { container: HTMLElement }) {}
    cancel() {}
    async render() {
      for (const line of pdf.spans) {
        const span = document.createElement('span')
        span.textContent = line
        this.options.container.appendChild(span)
      }
    }
  },
}))
function documentFixture(count = 20) {
  return {
    numPages: count,
    getPage: async (number: number) => ({
      getViewport: ({ scale }: { scale: number }) => ({ width: 960 * scale, height: 540 * scale }),
      render: () => {
        const cancel = vi.fn()
        let finish!: () => void
        const promise = new Promise<void>((resolve) => {
          finish = resolve
        })
        pdf.renders.push({ number, cancel, resolve: finish })
        if (!pdf.holdRender) finish()
        return { promise, cancel }
      },
      streamTextContent: () => ({}),
      getTextContent: async () => ({ items: [{ str: pdf.text, hasEOL: true }] }),
    }),
  }
}
const identity = { topicId: 'room', taskId: 'task', source: 'committed' as const, path: 'deck.pptx', version: 'v7' }
beforeEach(() => {
  setLocale('zh-CN')
  pdf.requests.length = 0
  pdf.renders.length = 0
  pdf.holdRender = false
  pdf.spans = ['Selectable original text']
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      disconnect() {}
    }
  )
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})
async function loaded(count = 20) {
  const ui = render(PreviewSlides, { props: { data: new ArrayBuffer(8), context: identity, canDownload: true } })
  await waitFor(() => expect(pdf.requests).toHaveLength(1))
  pdf.requests[0]!.resolve(documentFixture(count))
  await waitFor(() => expect(ui.getByRole('button', { name: '下一页' })).toHaveProperty('disabled', false))
  return ui
}
function pageInput(ui: ReturnType<typeof render>) {
  return ui.getByRole('spinbutton', { name: '页码' }) as HTMLInputElement
}

describe('slide reader contract (PDF.js substituted)', () => {
  it('navigates, bounds jumps, and selects thumbnails without parsing bytes again', async () => {
    const ui = await loaded()
    await fireEvent.click(ui.getByRole('button', { name: '下一页' }))
    expect(pageInput(ui).value).toBe('2')
    await fireEvent.click(ui.getByRole('button', { name: '跳转到第 4 页' }))
    expect(pageInput(ui).value).toBe('4')
    await fireEvent.update(pageInput(ui), '999')
    await fireEvent.submit(pageInput(ui).closest('form')!)
    expect(pageInput(ui).value).toBe('20')
    expect(ui.getByRole('button', { name: '下一页' })).toHaveProperty('disabled', true)
    await fireEvent.update(pageInput(ui), '-2')
    await fireEvent.submit(pageInput(ui).closest('form')!)
    expect(pageInput(ui).value).toBe('1')
    expect(ui.getByRole('button', { name: '上一页' })).toHaveProperty('disabled', true)
    expect(pdf.requests).toHaveLength(1)
  })

  it('only handles reader-owned keyboard focus, leaving the page field and other controls alone', async () => {
    const ui = await loaded()
    const reader = ui.getByRole('region', { name: '幻灯片阅读' })
    const outside = document.createElement('input')
    document.body.appendChild(outside)
    outside.focus()
    await fireEvent.keyDown(reader, { key: 'ArrowRight' })
    expect(pageInput(ui).value).toBe('1')
    pageInput(ui).focus()
    await fireEvent.keyDown(pageInput(ui), { key: 'ArrowRight' })
    expect(pageInput(ui).value).toBe('1')
    reader.focus()
    await fireEvent.keyDown(reader, { key: 'ArrowRight' })
    expect(pageInput(ui).value).toBe('2')
    await fireEvent.keyDown(reader, { key: 'End' })
    expect(pageInput(ui).value).toBe('20')
    outside.remove()
  })

  it('presents the same document and page, then restores trigger focus on Escape', async () => {
    const ui = await loaded()
    await fireEvent.click(ui.getByRole('button', { name: '下一页' }))
    const trigger = ui.getByRole('button', { name: '演示' })
    trigger.focus()
    await fireEvent.click(trigger)
    const reader = ui.getByRole('region', { name: '幻灯片阅读' })
    expect(document.activeElement).toBe(reader)
    expect(ui.queryByRole('navigation', { name: '缩略图' })).toBeNull()
    await fireEvent.keyDown(reader, { key: 'ArrowRight' })
    expect(pageInput(ui).value).toBe('3')
    await fireEvent.keyDown(reader, { key: 'Escape' })
    await waitFor(() => expect(document.activeElement).toBe(ui.getByRole('button', { name: '演示' })))
    expect(pageInput(ui).value).toBe('3')
    expect(pdf.requests).toHaveLength(1)
  })

  it('exits presentation from the page field without capturing its arrow keys', async () => {
    const ui = await loaded()
    const trigger = ui.getByRole('button', { name: '演示' })
    trigger.focus()
    await fireEvent.click(trigger)
    const field = pageInput(ui)
    field.focus()
    await fireEvent.keyDown(field, { key: 'ArrowRight' })
    expect(field.value).toBe('1')
    await fireEvent.keyDown(field, { key: 'Escape' })
    expect(ui.queryByRole('button', { name: '退出演示' })).toBeNull()
    expect(document.activeElement).toBe(trigger)
  })

  for (const replacement of [true, false]) {
    it(`retires pending page text after context ${replacement ? 'replacement' : 'in-place version change'}`, async () => {
      const context = reactive({ ...identity })
      const ui = render(PreviewSlides, { props: { data: new ArrayBuffer(8), context } })
      await waitFor(() => expect(pdf.requests).toHaveLength(1))
      let finish!: (content: { items: { str: string; hasEOL: boolean }[] }) => void
      const pending = new Promise<{ items: { str: string; hasEOL: boolean }[] }>((resolve) => {
        finish = resolve
      })
      const doc = documentFixture()
      const getPage = doc.getPage
      doc.getPage = async (number: number) => ({ ...(await getPage(number)), getTextContent: () => pending })
      pdf.requests[0]!.resolve(doc)
      await waitFor(() => expect(ui.getByRole('button', { name: '下一页' })).toHaveProperty('disabled', false))
      await fireEvent.click(ui.getByRole('button', { name: '对整页提问' }))
      expect(ui.getByRole('button', { name: '对整页提问' })).toHaveProperty('disabled', true)
      if (replacement) await ui.rerender({ context: { ...identity, version: 'v8' } })
      else {
        context.version = 'v8'
        await nextTick()
      }
      finish({ items: [{ str: 'retired v7 text', hasEOL: true }] })
      await new Promise((resolve) => setTimeout(resolve, 0))
      await nextTick()
      expect(ui.emitted().pageContext).toBeUndefined()
    })
  }

  it('bounds the thumbnail budget even for a thousand pages, and releases offscreen paints', async () => {
    const ui = await loaded(1000)
    await waitFor(() => expect(pdf.renders.length).toBeGreaterThan(0))
    expect(ui.getAllByRole('button', { name: /跳转到第/ })).toHaveLength(8)
    expect(new Set(pdf.renders.map((r) => r.number)).size).toBeLessThanOrEqual(8)
    const old = pdf.renders.slice()
    const rail = ui.getByRole('navigation', { name: '缩略图' })
    rail.scrollTop = 112 * 90
    await fireEvent.scroll(rail)
    await waitFor(() => expect(ui.getByRole('button', { name: '跳转到第 91 页' })).toBeTruthy())
    expect(ui.getAllByRole('button', { name: /跳转到第/ })).toHaveLength(8)
    await waitFor(() => expect(old.some((r) => r.cancel.mock.calls.length > 0)).toBe(true))
    expect(pdf.requests).toHaveLength(1)
  })

  it('ignores a late parse after bytes change and destroys the old loading task', async () => {
    const ui = render(PreviewSlides, { props: { data: new ArrayBuffer(8) } })
    await waitFor(() => expect(pdf.requests).toHaveLength(1))
    await ui.rerender({ data: new ArrayBuffer(16) })
    await waitFor(() => expect(pdf.requests).toHaveLength(2))
    pdf.requests[1]!.resolve(documentFixture(3))
    await waitFor(() => expect(ui.getByRole('button', { name: '下一页' })).toHaveProperty('disabled', false))
    pdf.requests[0]!.resolve(documentFixture(99))
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(ui.queryByRole('button', { name: '跳转到第 4 页' })).toBeNull()
    expect(pdf.requests[0]!.destroy).toHaveBeenCalledOnce()
    ui.unmount()
    expect(pdf.requests[1]!.destroy).toHaveBeenCalledOnce()
  })

  it('cancels late paints on page changes and never lets old text appear on the new page', async () => {
    pdf.holdRender = true
    const ui = await loaded()
    await waitFor(() => expect(pdf.renders.length).toBeGreaterThan(0))
    const old = pdf.renders.slice()
    await fireEvent.click(ui.getByRole('button', { name: '下一页' }))
    for (const render of old) render.resolve()
    await waitFor(() => expect(old.some((render) => render.cancel.mock.calls.length > 0)).toBe(true))
    const sheet = ui.container.querySelector('[data-page="2"]')!
    expect(sheet.textContent).not.toContain('Selectable original text')
    ui.unmount()
    expect(pdf.renders.every((render) => render.cancel.mock.calls.length > 0)).toBe(true)
  })

  it('emits the whole page and a selected run through the same frozen context, told apart by scope', async () => {
    const ui = await loaded()
    await fireEvent.click(ui.getByRole('button', { name: '下一页' }))
    await fireEvent.click(ui.getByRole('button', { name: '对整页提问' }))
    await waitFor(() => expect(ui.emitted().pageContext).toHaveLength(1))
    expect(ui.emitted().pageContext![0]).toEqual([{ text: pdf.text.trim(), page: 2, scope: 'page', context: identity }])
    expect(pdf.text.length).toBeGreaterThan(200)
    expect(ui.emitted().quote).toBeUndefined()
    await waitFor(() => expect(ui.container.querySelector('[data-page="2"] span')).toBeTruthy())
    const text = ui.container.querySelector('[data-page="2"] span')!.firstChild!
    const range = document.createRange()
    range.selectNodeContents(text)
    const selection = window.getSelection()!
    selection.removeAllRanges()
    selection.addRange(range)
    await fireEvent.mouseUp(ui.container.querySelector('[data-page="2"]')!)
    await waitFor(() => expect(ui.emitted().pageContext).toHaveLength(2))
    expect(ui.emitted().pageContext![1]).toEqual([
      { text: 'Selectable original text', page: 2, scope: 'selection', context: identity, prefix: '', suffix: '' },
    ])
    // 选中一句也带着身份出去了，不再走那句拼好的话。
    expect(ui.emitted().quote).toBeUndefined()
    selection.removeAllRanges()
  })

  it('carries the words on either side of a slide selection, from the page text layer', async () => {
    // 同一页上「重试 3 次」出现两次：只有两侧的字能说清选的是哪一处。
    pdf.spans = ['先看这一段', '重试 3 次', '再看那一段重试 3 次收尾。']
    const ui = await loaded()
    await waitFor(() => expect(ui.container.querySelectorAll('[data-page="1"] span')).toHaveLength(3))
    const spans = ui.container.querySelectorAll('[data-page="1"] span')
    const range = document.createRange()
    range.selectNodeContents(spans[1]!.firstChild!)
    const selection = window.getSelection()!
    selection.removeAllRanges()
    selection.addRange(range)
    await fireEvent.mouseUp(ui.container.querySelector('[data-page="1"]')!)
    await waitFor(() => expect(ui.emitted().pageContext).toHaveLength(1))
    const [payload] = ui.emitted().pageContext![0] as [{ text: string; prefix: string; suffix: string }]
    expect(payload.text).toBe('重试 3 次')
    expect(payload.prefix.endsWith('先看这一段')).toBe(true)
    expect(payload.suffix.startsWith('再看那一段重试 3 次收尾')).toBe(true)
    selection.removeAllRanges()
  })

  it('falls back to the plain sentence when no verified identity is available', async () => {
    const ui = render(PreviewSlides, { props: { data: new ArrayBuffer(8) } })
    await waitFor(() => expect(pdf.requests).toHaveLength(1))
    pdf.requests[0]!.resolve(documentFixture(3))
    await waitFor(() => expect(ui.container.querySelector('[data-page="1"] span')).toBeTruthy())
    const text = ui.container.querySelector('[data-page="1"] span')!.firstChild!
    const range = document.createRange()
    range.selectNodeContents(text)
    const selection = window.getSelection()!
    selection.removeAllRanges()
    selection.addRange(range)
    await fireEvent.mouseUp(ui.container.querySelector('[data-page="1"]')!)
    expect(ui.emitted().quote![0]).toEqual([{ text: 'Selectable original text', page: 1 }])
    expect(ui.emitted().pageContext).toBeUndefined()
    selection.removeAllRanges()
  })

  it('drops a selection too short to point at anything', async () => {
    const ui = await loaded(3)
    const select = async (start: number, end: number) => {
      const span = ui.container.querySelector('[data-page="1"] span')!.firstChild!
      const range = document.createRange()
      range.setStart(span, start)
      range.setEnd(span, end)
      const selection = window.getSelection()!
      selection.removeAllRanges()
      selection.addRange(range)
      await fireEvent.mouseUp(ui.container.querySelector('[data-page="1"]')!)
    }
    // 一个字、一个空格，都过不了「归一化之后至少两个字」这一关。带身份和不带身份是
    // 同一个函数里的两条出口，共用这一行门槛；下面那条正对照证明这条 mouseUp 路是通的，
    // 免得这两条断言只是「什么都没发生」的空过。
    await select(0, 1)
    await select(10, 11)
    expect(ui.emitted().pageContext).toBeUndefined()

    const span = ui.container.querySelector('[data-page="1"] span')!.firstChild!
    const whole = document.createRange()
    whole.selectNodeContents(span)
    const selection = window.getSelection()!
    selection.removeAllRanges()
    selection.addRange(whole)
    await fireEvent.mouseUp(ui.container.querySelector('[data-page="1"]')!)
    await waitFor(() => expect(ui.emitted().pageContext).toHaveLength(1))
    selection.removeAllRanges()
  })

  it('exposes original download in loading, missing-renderer, and error states', async () => {
    const ui = render(PreviewSlides, { props: { data: null, pending: true, canDownload: true } })
    expect(ui.getByRole('status').textContent).toContain('正在加载')
    await fireEvent.click(ui.getByRole('button', { name: '下载原文件' }))
    await ui.rerender({ pending: false, rendererMissing: true })
    expect(ui.getByRole('alert').textContent).toContain('无法转换')
    await fireEvent.click(ui.getByRole('button', { name: '下载原文件' }))
    await ui.rerender({ rendererMissing: false, error: 'Renderer unavailable' })
    expect(ui.getByRole('alert').textContent).toContain('Renderer unavailable')
    await fireEvent.click(ui.getByRole('button', { name: '下载原文件' }))
    expect(ui.emitted().download).toHaveLength(3)
  })

  it('keeps the displayed page and scroll when a refresh is pending, fails, and recovers', async () => {
    const ui = await loaded(3)
    await fireEvent.click(ui.getByRole('button', { name: '下一页' }))
    const sheet = ui.container.querySelector('[data-page="2"]')!
    const stage = sheet.parentElement!
    stage.scrollTop = 120
    stage.scrollLeft = 24

    await ui.rerender({ pending: true, context: undefined })
    expect(pageInput(ui).value).toBe('2')
    expect(pageInput(ui).disabled).toBe(false)
    expect(ui.container.contains(sheet)).toBe(true)
    expect(stage.scrollTop).toBe(120)
    expect(stage.scrollLeft).toBe(24)
    expect(ui.queryByRole('button', { name: '对整页提问' })).toBeNull()

    await ui.rerender({ pending: false, error: 'Refresh failed', context: identity })
    expect(ui.getByRole('alert').textContent).toContain('Refresh failed')
    expect(ui.container.contains(sheet)).toBe(true)
    expect(pageInput(ui).value).toBe('2')
    expect(ui.getByRole('button', { name: '对整页提问' })).toHaveProperty('disabled', true)
    await fireEvent.click(ui.getByRole('button', { name: '下载原文件' }))
    expect(ui.emitted().download).toHaveLength(1)

    await ui.rerender({ error: '', context: identity })
    expect(ui.container.contains(sheet)).toBe(true)
    expect(pageInput(ui).value).toBe('2')
    expect(stage.scrollTop).toBe(120)
    expect(stage.scrollLeft).toBe(24)
    expect(ui.getByRole('button', { name: '对整页提问' })).toHaveProperty('disabled', false)
    expect(pdf.requests).toHaveLength(1)
  })

  it('retires pending whole-page text when refreshing makes its action unavailable', async () => {
    const ui = render(PreviewSlides, { props: { data: new ArrayBuffer(8), context: identity } })
    await waitFor(() => expect(pdf.requests).toHaveLength(1))
    let finish!: (content: { items: { str: string; hasEOL: boolean }[] }) => void
    const pending = new Promise<{ items: { str: string; hasEOL: boolean }[] }>((resolve) => {
      finish = resolve
    })
    const doc = documentFixture(3)
    const getPage = doc.getPage
    doc.getPage = async (number: number) => ({ ...(await getPage(number)), getTextContent: () => pending })
    pdf.requests[0]!.resolve(doc)
    await waitFor(() => expect(ui.getByRole('button', { name: '下一页' })).toHaveProperty('disabled', false))
    await fireEvent.click(ui.getByRole('button', { name: '对整页提问' }))
    await ui.rerender({ pending: true })
    finish({ items: [{ str: 'text captured before refreshing', hasEOL: true }] })
    await new Promise((resolve) => setTimeout(resolve, 0))
    await nextTick()
    expect(ui.emitted().pageContext).toBeUndefined()
  })
})

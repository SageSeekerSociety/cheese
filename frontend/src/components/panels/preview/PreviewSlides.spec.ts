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
/**
 * 一页的文字：pdf.js 给的是那条流，「一页的文字」就是自己按段读它（见 useSlidesPdf）。
 * 这里给一条真的 ReadableStream，读法和产品里一样；`hold` 时不喂内容，读的人就一直等
 * ——用来演「取文字比换文档慢」。
 */
function textStream(hold = false) {
  let deliver!: (chunk: { items: { str: string; hasEOL: boolean }[] }) => void
  const stream = new ReadableStream<{ items: { str: string; hasEOL: boolean }[] }>({
    start(controller) {
      deliver = (chunk) => {
        controller.enqueue(chunk)
        controller.close()
      }
      if (!hold) deliver({ items: [{ str: pdf.text, hasEOL: true }] })
    },
  })
  return { stream, deliver }
}

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
      streamTextContent: () => textStream().stream,
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
/** 屏幕上是哪一页。放映时这一层被挪到 body 下（而且工具栏整个收掉了，页码框不在），
 *  所以从 document 里读那一页自己的标记。 */
function shownPage() {
  return document.querySelector('.slides__sheet')?.getAttribute('data-page')
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
    const trigger = ui.getByRole('button', { name: '放映' })
    trigger.focus()
    await fireEvent.click(trigger)
    const reader = ui.getByRole('dialog', { name: '放映中' })
    expect(document.activeElement).toBe(reader)
    expect(ui.queryByRole('navigation', { name: '缩略图' })).toBeNull()
    // 放映是整屏的幻灯片：上面那条工具栏收掉，底部换成一条细的，翻页和跳页还在手边。
    expect(ui.queryByRole('button', { name: '缩略图' })).toBeNull()
    expect(ui.getByRole('spinbutton', { name: '页码' }).closest('.slides__bar--present')).not.toBeNull()
    // 放映时挪到 body 下，不受面板外层绘制层级的限制（否则左边被侧栏盖住）。
    expect(reader.parentElement).toBe(document.body)
    await fireEvent.keyDown(reader, { key: 'ArrowRight' })
    expect(shownPage()).toBe('3')
    await fireEvent.keyDown(reader, { key: 'Escape' })
    await waitFor(() => expect(document.activeElement).toBe(ui.getByRole('button', { name: '放映' })))
    expect(ui.getByRole('region', { name: '幻灯片阅读' }).parentElement).not.toBe(document.body)
    expect(pageInput(ui).value).toBe('3')
    expect(pdf.requests).toHaveLength(1)
  })

  it('keeps the presented layer to itself: Tab circles inside it, and the exit button leaves', async () => {
    const ui = await loaded()
    const trigger = ui.getByRole('button', { name: '放映' })
    trigger.focus()
    await fireEvent.click(trigger)
    const reader = ui.getByRole('dialog', { name: '放映中' })
    const field = ui.getByRole('spinbutton', { name: '页码' })
    const next = ui.getByRole('button', { name: '下一页' })
    const exit = ui.getByRole('button', { name: '退出放映' })
    // 第 1 页时「上一页」是禁用的，不进焦点圈：这一层 → 页码框 → 下一页 → 退出键 → 回到这一层。
    await fireEvent.keyDown(reader, { key: 'Tab' })
    expect(document.activeElement).toBe(field)
    await fireEvent.keyDown(field, { key: 'Tab' })
    expect(document.activeElement).toBe(next)
    await fireEvent.keyDown(next, { key: 'Tab' })
    expect(document.activeElement).toBe(exit)
    await fireEvent.keyDown(exit, { key: 'Tab' })
    expect(document.activeElement).toBe(reader)
    // 反向绕一圈也一样：Shift+Tab 从这一层回到退出键。
    await fireEvent.keyDown(reader, { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(exit)
    await fireEvent.click(ui.getByRole('button', { name: '退出放映' }))
    await waitFor(() => expect(ui.queryByRole('dialog')).toBeNull())
    // 放映时工具栏卸掉了，「放映」那颗按钮是重新挂上来的一个新元素：还焦点要按名字再取一次。
    expect(document.activeElement).toBe(ui.getByRole('button', { name: '放映' }))
  })

  for (const replacement of [true, false]) {
    it(`retires pending page text after context ${replacement ? 'replacement' : 'in-place version change'}`, async () => {
      const context = reactive({ ...identity })
      const ui = render(PreviewSlides, { props: { data: new ArrayBuffer(8), context } })
      await waitFor(() => expect(pdf.requests).toHaveLength(1))
      const { stream, deliver } = textStream(true)
      const doc = documentFixture()
      const getPage = doc.getPage
      doc.getPage = async (number: number) => ({ ...(await getPage(number)), streamTextContent: () => stream })
      pdf.requests[0]!.resolve(doc)
      await waitFor(() => expect(ui.getByRole('button', { name: '下一页' })).toHaveProperty('disabled', false))
      await fireEvent.click(ui.getByRole('button', { name: '对整页提问' }))
      expect(ui.getByRole('button', { name: '对整页提问' })).toHaveProperty('disabled', true)
      if (replacement) await ui.rerender({ context: { ...identity, version: 'v8' } })
      else {
        context.version = 'v8'
        await nextTick()
      }
      deliver({ items: [{ str: 'retired v7 text', hasEOL: true }] })
      await new Promise((resolve) => setTimeout(resolve, 0))
      await nextTick()
      expect(ui.emitted().pageContext).toBeUndefined()
    })
  }

  it('walks the thumbnails with the arrow keys once focus is in the rail, and turns the page with it', async () => {
    const ui = await loaded(6)
    await waitFor(() => expect(ui.getAllByRole('button', { name: /跳转到第/ })).toHaveLength(6))
    const first = ui.getByRole('button', { name: '跳转到第 1 页' })
    first.focus()
    await fireEvent.keyDown(first, { key: 'ArrowDown' })
    // 焦点在轨道里：方向键在缩略图之间走，同时也换页（焦点停在第 2 页那张上）。
    expect(pageInput(ui).value).toBe('2')
    await waitFor(() => expect(document.activeElement).toBe(ui.getByRole('button', { name: '跳转到第 2 页' })))
    // 轨道里处理过的键不再落到阅读器那一条上：只前进一页，不是两页。
    expect(pageInput(ui).value).toBe('2')
    await fireEvent.keyDown(ui.getByRole('button', { name: '跳转到第 2 页' }), { key: 'End' })
    expect(pageInput(ui).value).toBe('6')
    await waitFor(() => expect(document.activeElement).toBe(ui.getByRole('button', { name: '跳转到第 6 页' })))
    // 到顶了再往下走：留在原地，不越界。
    await fireEvent.keyDown(ui.getByRole('button', { name: '跳转到第 6 页' }), { key: 'ArrowDown' })
    expect(pageInput(ui).value).toBe('6')
    // 轨道之外（阅读器自己有焦点）仍然是原来的逐页翻页。
    ui.getByRole('region', { name: '幻灯片阅读' }).focus()
    await fireEvent.keyDown(ui.getByRole('region', { name: '幻灯片阅读' }), { key: 'Home' })
    expect(pageInput(ui).value).toBe('1')
  })

  it('bounds the thumbnail budget even for a thousand pages, and releases offscreen paints', async () => {
    const ui = await loaded(1000)
    await waitFor(() => expect(pdf.renders.length).toBeGreaterThan(0))
    expect(ui.getAllByRole('button', { name: /跳转到第/ })).toHaveLength(8)
    expect(new Set(pdf.renders.map((r) => r.number)).size).toBeLessThanOrEqual(8)
    const old = pdf.renders.slice()
    const rail = ui.getByRole('navigation', { name: '缩略图' })
    rail.scrollTop = 71 * 90
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
    const { stream, deliver } = textStream(true)
    const doc = documentFixture(3)
    const getPage = doc.getPage
    doc.getPage = async (number: number) => ({ ...(await getPage(number)), streamTextContent: () => stream })
    pdf.requests[0]!.resolve(doc)
    await waitFor(() => expect(ui.getByRole('button', { name: '下一页' })).toHaveProperty('disabled', false))
    await fireEvent.click(ui.getByRole('button', { name: '对整页提问' }))
    await ui.rerender({ pending: true })
    deliver({ items: [{ str: 'text captured before refreshing', hasEOL: true }] })
    await new Promise((resolve) => setTimeout(resolve, 0))
    await nextTick()
    expect(ui.emitted().pageContext).toBeUndefined()
  })
})

describe('演讲者备注', () => {
  /** 挂一份带备注的稿子：`notes` 下标即页码。 */
  async function withNotes(notes: readonly string[], count = 3) {
    const ui = render(PreviewSlides, { props: { data: new ArrayBuffer(8), context: identity, notes } })
    await waitFor(() => expect(pdf.requests).toHaveLength(1))
    pdf.requests[0]!.resolve(documentFixture(count))
    await waitFor(() => expect(ui.getByRole('button', { name: '下一页' })).toHaveProperty('disabled', false))
    return ui
  }

  it('这一页的备注写在幻灯片下面，翻页跟着换', async () => {
    const ui = await withNotes(['第一页的话', '第二页的话'], 3)
    expect(ui.getByTestId('slides-notes').textContent).toContain('第一页的话')
    await fireEvent.click(ui.getByRole('button', { name: '下一页' }))
    expect(ui.getByTestId('slides-notes').textContent).toContain('第二页的话')
  })

  it('某一页没有备注时说一句，而不是把这一块整个抽掉', async () => {
    // 第三页没有备注：这一块还在，写的是「这一页没有备注」——抽掉了读者会以为这一档
    // 没有备注这个功能，而不是这一页恰好没有。
    const ui = await withNotes(['第一页的话'], 3)
    await fireEvent.click(ui.getByRole('button', { name: '跳转到第 3 页' }))
    expect(ui.getByTestId('slides-notes').textContent).toContain('这一页没有备注')
  })

  it('整份都没有备注时不摆这一块，也不摆工具栏上那颗开关', async () => {
    const ui = await withNotes([], 3)
    expect(ui.queryByTestId('slides-notes')).toBeNull()
    expect(ui.queryByTestId('slides-notes-toggle')).toBeNull()
  })

  it('可以收起；收起来之后工具栏那颗开关还在', async () => {
    const ui = await withNotes(['第一页的话'], 2)
    await fireEvent.click(ui.getByTestId('slides-notes-toggle'))
    expect(ui.queryByTestId('slides-notes')).toBeNull()
    await fireEvent.click(ui.getByTestId('slides-notes-toggle'))
    expect(ui.getByTestId('slides-notes').textContent).toContain('第一页的话')
  })

  it('放映时不出现：备注是给讲的人看的，不是给台下看的', async () => {
    const ui = await withNotes(['只给讲的人看'], 2)
    expect(ui.getByTestId('slides-notes')).toBeTruthy()
    await fireEvent.click(ui.getByRole('button', { name: '放映' }))
    expect(document.querySelector('[data-testid="slides-notes"]')).toBeNull()
  })
})

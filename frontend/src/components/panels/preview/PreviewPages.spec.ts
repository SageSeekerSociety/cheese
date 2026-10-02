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
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import PreviewPages from './PreviewPages.vue'

import { setLocale } from '@/i18n'

// 断言读的是中文界面上的那一行字，语言钉在中文上。
beforeEach(() => setLocale('zh-CN'))

/** pdf.js 是假的：这条测的是「谁作废了谁」，不是 pdf.js 会不会解析。
 *  `calls` 是自己数的，不用 vi.fn：组件是动态 import（`await import('pdfjs-dist/legacy/build/pdf.mjs')`），
 *  这个假模块要等 library() 真的跑起来才建起来——beforeEach 里够不着它。 */
const pdf = vi.hoisted(() => ({
  resolveDoc: null as null | ((doc: unknown) => void),
  calls: 0,
}))

vi.mock('pdfjs-dist/legacy/build/pdf.mjs', () => {
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

vi.mock('pdfjs-dist/legacy/build/pdf.worker.min.mjs?url', () => ({ default: '/pdf.worker.mjs' }))

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

const context = {
  topicId: 'room',
  path: 'deck.pdf',
  source: 'committed' as const,
  taskId: 'task',
  version: 'v7',
}

function mount(props: { data: ArrayBuffer | null; context?: typeof context } = { data: new ArrayBuffer(8) }) {
  return render(PreviewPages, {
    props,
    global: {
      // 按钮原样落成 <button>：这一条要读它的 disabled 和 aria-pressed。
      stubs: {
        VIcon: true,
        VProgressCircular: true,
        VBtn: { template: '<button v-bind="$attrs"><slot /></button>' },
      },
    },
  })
}

/** 打开一份能画出来的文档：pdf.js 是假的，解析得自己放行，然后等两页都画出来。 */
async function opened(props: { data: ArrayBuffer | null; context?: typeof context } = { data: new ArrayBuffer(8) }) {
  const ui = mount(props)
  await waitFor(() => expect(pdf.calls).toBeGreaterThan(0))
  pdf.resolveDoc?.(DOCUMENT)
  await waitFor(() => expect(ui.container.querySelectorAll('canvas')).toHaveLength(2))
  return ui
}

/** 每次 emit 的实参表。组件的 emits 类型让 testing-library 的返回类型收得太窄，
 *  读实参还得自己摊开。 */
function pins(ui: ReturnType<typeof mount>): unknown[] {
  return (ui.emitted('pin')?.[0] ?? []) as unknown[]
}

/** 组件交出去的那几样不挂在 props 上，只有实例上拿得到——测试里就这么拿。 */
function exposed(ui: ReturnType<typeof mount>) {
  const host = ui.container.firstElementChild as Element & {
    __vueParentComponent?: {
      exposed?: { snapshot?: (page: number) => Promise<unknown>; clearMark?: () => void }
    }
  }
  return host.__vueParentComponent?.exposed ?? {}
}

/** 页框的量测在 happy-dom 里全是零，而指位置要的正是比例：给那一页摆一个框。
 *
 *  量的是**画布**，所以框摆画布上；页框另给一个比画布大一圈的框（真实 CSS 里那
 *  一圈 1px 描边就是这样的），落点按页框算会偏——这条守的就是别量错那个框。 */
function pageBox(container: Element, number: number, rect = { left: 100, top: 200, width: 400, height: 800 }) {
  const page = container.querySelector(`[data-page="${number}"]`) as HTMLElement
  const canvas = page.querySelector('canvas') as HTMLCanvasElement
  page.getBoundingClientRect = () =>
    ({ left: rect.left - 1, top: rect.top - 1, width: rect.width + 2, height: rect.height + 2 }) as DOMRect
  canvas.getBoundingClientRect = () => rect as DOMRect
  return canvas
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
    // 好的那一页画出来时，坏的那一页的说明未必已经到了：两页各自异步渲染。
    await waitFor(() => expect(container.textContent).toContain('第 2 页无法显示'))
    expect(container.textContent).toContain('页流损坏')
  })

  it('文档画出来之后真正变了宽度，页还是重画', async () => {
    const { container } = mount()
    await waitFor(() => expect(pdf.calls).toBeGreaterThan(0))
    pdf.resolveDoc?.(DOCUMENT)
    await waitFor(() => expect(container.querySelectorAll('canvas')).toHaveLength(2))

    resize?.()
    await new Promise((r) => setTimeout(r, 260))
    await waitFor(() => expect(container.querySelectorAll('canvas')).toHaveLength(2))
  })

  it('选中一个字不发引用：门槛和幻灯片那条一样', async () => {
    const { container, emitted } = mount()
    await waitFor(() => expect(pdf.calls).toBeGreaterThan(0))
    pdf.resolveDoc?.(DOCUMENT)
    await waitFor(() => expect(container.querySelector('[data-page="1"]')).toBeTruthy())

    // 初次重排的防抖是 200 毫秒，重排会把页里画的东西整块换掉；等它落定再挂节点。
    await new Promise((r) => setTimeout(r, 300))
    // 真读的时候这一层是 pdf.js 的文字层。假的文字层不产文字，这里自己挂一个文本
    // 节点上去，好让选区落进 `[data-page]` 里。
    const host = container.querySelector('[data-page="1"]')!
    const node = document.createTextNode('第一页正文')
    host.appendChild(node)
    const selection = window.getSelection()!
    const range = document.createRange()
    const pick = async (end: number) => {
      range.setStart(node, 0)
      range.setEnd(node, end)
      selection.removeAllRanges()
      selection.addRange(range)
      // `@mouseup` 挂在组件的根 `.pv` 上，事件从页这一层冒上去。
      await fireEvent.mouseUp(host)
    }

    // 一个「第」字过不了「归一化之后至少两个字」，这条路到此为止；再把正例放出来
    // 证明同一套鼠标动作是通的，免得上面那条只是「什么都没发生」。
    await pick(1)
    expect(emitted().quote).toBeUndefined()
    await pick(3)
    await waitFor(() => expect(emitted().quote).toHaveLength(1))
    expect(emitted().quote![0]).toEqual([{ text: '第一页', page: 1 }])
    selection.removeAllRanges()
  })
})

describe('在页面上指一点', () => {
  it('这一页的版本没确认就不给指：按钮禁用，点了也不进指位置', async () => {
    const ui = await opened()

    const pin = ui.getByRole('button', { name: '指位置' })
    expect(pin).toHaveProperty('disabled', true)

    // 真浏览器里禁用的按钮根本收不到 click；这里直接点页面，守的是组件自己那道闸：
    // 没有已确认的版本，怎么点都不发。
    await fireEvent.click(pageBox(ui.container, 1), { clientX: 200, clientY: 400 })
    expect(ui.emitted('pin')).toBeUndefined()
    expect(ui.container.querySelector('.pv__pin')).toBeNull()
  })

  it('点一下报的是这一点在那一页里的比例，屏上同时留下标记', async () => {
    const ui = await opened({ data: new ArrayBuffer(8), context })

    await fireEvent.click(ui.getByRole('button', { name: '指位置' }))
    // 画布是 (100,200)-(500,1000)：横里走一半、竖里走十六分之一。两个数不一样，
    // 免得把 x 和 y 对调了还看不出来。
    await fireEvent.click(pageBox(ui.container, 1), { clientX: 300, clientY: 250 })

    expect(pins(ui)[0]).toEqual({ page: 1, x: 0.5, y: 0.0625, context })
    const mark = ui.container.querySelector('.pv__pin') as HTMLElement
    expect(mark.style.left).toBe('50%')
    expect(mark.style.top).toBe('6.25%')
  })

  it('点在画布外的坐标夹到边界', async () => {
    const ui = await opened({ data: new ArrayBuffer(8), context })

    await fireEvent.click(ui.getByRole('button', { name: '指位置' }))
    // 画布是 (100,200)-(500,1000)，点在这一框的右下外面和左上外面。
    await fireEvent.click(pageBox(ui.container, 1), { clientX: 1000, clientY: -100 })
    expect(pins(ui)[0]).toMatchObject({ page: 1, x: 1, y: 0 })
  })

  it('还没画出来的那一页不接受：点上去什么都不发', async () => {
    const ui = mount({ data: new ArrayBuffer(8), context })
    await waitFor(() => expect(pdf.calls).toBeGreaterThan(0))
    // 第 2 页取不出来：那块地方摆的是出错说明，没有画布。
    pdf.resolveDoc?.(ONE_BAD_PAGE)
    await waitFor(() => expect(ui.container.querySelectorAll('canvas')).toHaveLength(1))
    await waitFor(() => expect(ui.container.textContent).toContain('第 2 页无法显示'))

    await fireEvent.click(ui.getByRole('button', { name: '指位置' }))
    // 给这一页也摆一个量得到的框：不然「没发出指认」只是因为框量出来是零，
    // 闸门在不在都对——这条要守的正是那道闸。
    const broken = ui.container.querySelector('[data-page="2"]') as HTMLElement
    broken.getBoundingClientRect = () => ({ left: 100, top: 200, width: 400, height: 800 }) as DOMRect
    await fireEvent.click(broken, { clientX: 300, clientY: 250 })

    expect(ui.emitted('pin')).toBeUndefined()
    expect(ui.container.querySelector('.pv__pin')).toBeNull()
  })

  it('点在页与页之间的空白上什么都不发', async () => {
    const ui = await opened({ data: new ArrayBuffer(8), context })

    await fireEvent.click(ui.getByRole('button', { name: '指位置' }))
    // 落在容器本身，不在任何一页里。
    await fireEvent.click(ui.container.firstElementChild as HTMLElement, { clientX: 300, clientY: 250 })

    expect(ui.emitted('pin')).toBeUndefined()
    expect(ui.container.querySelector('.pv__pin')).toBeNull()
  })

  it('快照交的是这一页画出来的样子；还没画出来就交不出图', async () => {
    const ui = await opened({ data: new ArrayBuffer(8), context })

    const snapshot = exposed(ui).snapshot
    if (!snapshot) throw new Error('PreviewPages 没把 snapshot 交出来')

    const canvas = ui.container.querySelector('canvas') as HTMLCanvasElement
    canvas.toBlob = ((callback: (blob: Blob | null) => void) =>
      callback(new Blob(['png'], { type: 'image/png' }))) as HTMLCanvasElement['toBlob']
    canvas.width = 800
    canvas.height = 1000

    await expect(snapshot(1)).resolves.toMatchObject({ width: 800, height: 1000 })
    // 第 2 页还没画出来：没有画布就不硬凑一张图。
    await expect(snapshot(9)).resolves.toBeNull()
  })

  it('重排撤掉记号时告诉外面；没记号就不打扰', async () => {
    const ui = await opened({ data: new ArrayBuffer(8), context })

    // 还没指过的时候重排：不该顺手把外面正在编的那句话清掉。
    resize?.()
    await new Promise((r) => setTimeout(r, 260))
    expect(ui.emitted('dropped')).toBeUndefined()

    await fireEvent.click(ui.getByRole('button', { name: '指位置' }))
    await fireEvent.click(pageBox(ui.container, 1), { clientX: 300, clientY: 250 })
    expect(ui.container.querySelector('.pv__pin')).not.toBeNull()

    // 指过之后重排：记号按比例画，尺寸一变指的就不是同一处了，撤掉并告诉外面。
    resize?.()
    await waitFor(() => expect(ui.emitted('dropped')).toBeTruthy())
    expect(ui.container.querySelector('.pv__pin')).toBeNull()
  })

  it('记号撤掉时连着退出指位置，屏上不留一个已经交出去的记号', async () => {
    const ui = await opened({ data: new ArrayBuffer(8), context })
    await fireEvent.click(ui.getByRole('button', { name: '指位置' }))
    await fireEvent.click(pageBox(ui.container, 1), { clientX: 300, clientY: 250 })
    expect(ui.container.querySelector('.pv__pin')).not.toBeNull()

    exposed(ui).clearMark?.()

    await waitFor(() => expect(ui.container.querySelector('.pv__pin')).toBeNull())
    expect(ui.container.querySelector('.pv')?.className).not.toContain('pv--pointing')
  })
})

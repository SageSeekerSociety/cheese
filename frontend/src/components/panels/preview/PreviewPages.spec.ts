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
  /** 一页画了几回。重画不换页框尺寸，只有这个数看得出重画发生过。 */
  renders: 0,
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
  /** 假的文字层：这一页有没有字由 `streamTextContent` 说了算，有就往容器里写一点。
   *  真的 pdf.js 也是这样——扫描件的页照样建出一层，只是里面一个字都没有。
   *
   *  写的是「这一页的正文」这五个字，测试要另摆自己的字时把这一层清掉（组件不认得
   *  这些字，它只数有没有）。 */
  class TextLayer {
    private container: HTMLElement
    private source: { hasText?: boolean }
    constructor({
      container,
      textContentSource,
    }: {
      container: HTMLElement
      textContentSource: { hasText?: boolean }
    }) {
      this.container = container
      this.source = textContentSource
    }
    async render() {
      if (!this.source?.hasText) return
      this.container.textContent = '这一页的正文'
      // happy-dom 量不出布局：这一层的框由测试摆出来，键盘那条路才有「一行」可走。
      // 真 pdf.js 里是它按每一段算好位置写进样式，量到的是同一件事。
      this.container.getBoundingClientRect = () =>
        ({ left: 20, top: 40, width: 300, height: 12, right: 320, bottom: 52, x: 20, y: 40 }) as DOMRect
    }
  }
  return { GlobalWorkerOptions: {}, getDocument, TextLayer, version: 'test' }
})

vi.mock('pdfjs-dist/legacy/build/pdf.worker.min.mjs?url', () => ({ default: '/pdf.worker.mjs' }))
// 宽窄由 vueuse 的 useElementSize 量，它要真的 ResizeObserver 条目；这里的桩只是一只能手动
// 触发的回调（下面的 `resize`），两边混用会把那只回调换成 vueuse 的。宽窄在这一份里不是
// 被测的东西，钉成宽。
vi.mock('@/lib/previewNarrow', async () => {
  const { computed } = await import('vue')
  return { usePreviewNarrow: () => ({ narrow: computed(() => false), measured: computed(() => true) }) }
})

/** 一页假的：viewport 与 render 都够组件把画布造出来。
 *
 *  `hasText` 说这一页有没有可选的文字——假的那一层按它写不写。真 pdf.js 里扫描件的
 *  页照样有一层文字层，里面一个字也没有。
 *
 *  这些用例都当有文字：没写这一项时 `hasText` 是 `undefined`，假文字层就不写东西，
 *  「有没有字」的判据便落到「没有」。 */
function page(number: number, hasText = true) {
  return {
    number,
    getViewport: ({ scale }: { scale: number }) => ({ width: 600 * scale, height: 800 * scale }),
    render: () => {
      pdf.renders += 1
      return { promise: Promise.resolve() }
    },
    streamTextContent: () => ({ hasText }),
  }
}

const DOCUMENT = { numPages: 2, getPage: (n: number) => Promise.resolve(page(n)) }

/** 第二页取不出来——一页坏掉不该让整份文档一声不响地留白。 */
const ONE_BAD_PAGE = {
  numPages: 2,
  getPage: (n: number) => (n === 2 ? Promise.reject(new Error('页流损坏')) : Promise.resolve(page(n))),
}

/** 第一页是扫描件（整页一张图，没有文字层），第二页有文字。 */
const SCANNED_FIRST = {
  numPages: 2,
  getPage: (n: number) => Promise.resolve(page(n, n !== 1)),
}

/** 第二页是扫描件：一份文档里夹一两页扫描件是常事。 */
const SCANNED_MIDDLE = {
  numPages: 3,
  getPage: (n: number) => Promise.resolve(page(n, n !== 2)),
}

/** 整份都是扫描件。 */
const ALL_SCANNED = {
  numPages: 2,
  getPage: (n: number) => Promise.resolve(page(n, false)),
}

/** 浏览器的 ResizeObserver 在 observe() 时会立刻回调一次 —— 这一条就是被它绊倒的。 */
let resize: (() => void) | null = null

/** 浏览器里那个真实的「Ctrl 加号」：像素比变了，同时来一次窗口 resize。
 *  happy-dom 不会自己改 `devicePixelRatio`，这里替它改。 */
function zoomBrowserTo(value: number) {
  Object.defineProperty(window, 'devicePixelRatio', { value, configurable: true })
  window.dispatchEvent(new Event('resize'))
}

/** 在页里摆几行假字，好让键盘那条路有行可走。
 *
 *  pdf.js 的文字层是绝对定位摆上去的，happy-dom 量不出位置；这里给每一行一个自己摆的
 *  框，行与行错开，`linesFrom` 才分得出是几行。摆进 `.pv__canvas` 里，和真组件建文字层
 *  的位置一致。 */
function rows(container: Element, number: number, texts: string[], height = 12, gap = 6) {
  const page = container.querySelector(`[data-page="${number}"]`) as HTMLElement
  const host = (page.querySelector('.pv__canvas') as HTMLElement) ?? page
  // 假的文字层自己写过一句「这一页的正文」，这一条要的是这里摆的那几行：先清掉，
  // 不然两边的字会一起被选中。
  host.replaceChildren()
  const layer = document.createElement('div')
  layer.className = 'pv-text'
  for (const [i, text] of texts.entries()) {
    const span = document.createElement('span')
    span.textContent = text
    layer.append(span)
    const top = 40 + i * (height + gap)
    span.getBoundingClientRect = () =>
      ({ left: 20, top, width: 60, height, right: 80, bottom: top + height, x: 20, y: top }) as DOMRect
  }
  host.append(layer)
  page.getBoundingClientRect = () =>
    ({ left: 0, top: 0, width: 400, height: 800, right: 400, bottom: 800, x: 0, y: 0 }) as DOMRect
  page.scrollIntoView = () => {}
  return page
}

/** 按一次键，走的是组件挂在页框上的那个处理。 */
async function press(page: HTMLElement, key: string, shift = false) {
  await fireEvent.keyDown(page, { key, shiftKey: shift })
}

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
  pdf.renders = 0
  Object.defineProperty(window, 'devicePixelRatio', { value: 1, configurable: true })
})

afterEach(cleanup)

const context = {
  topicId: 'room',
  path: 'deck.pdf',
  source: 'committed' as const,
  taskId: 'task',
  version: 'v7',
}

/** 挂这一格。`marks` / `activeMark` 是这一份上已有的评论指着的几处（见 `PreviewPages` 的 props）。 */
type PagesProps = {
  data: ArrayBuffer | null
  context?: typeof context
  marks?: readonly { id: string; page: number; x: number; y: number }[]
  activeMark?: string | null
}

function mount(props: PagesProps = { data: new ArrayBuffer(8) }) {
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
async function opened(props: PagesProps = { data: new ArrayBuffer(8) }) {
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

/** 组件交出去的那几样不挂在 props 上，只有实例上拿得到——测试里就这么拿。
 *
 *  `container.firstElementChild` 的 `__vueParentComponent` 未必是宿主本身：stub 渲染
 *  根元素时它是那个替身，拿到的 `exposed` 就不是这里的组件。确认实例属于被测宿主。 */
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

  it('浏览器缩放改了屏幕像素比时，页照新精度重画一遍', async () => {
    // 缩放（Ctrl 加号）同时改像素比和视口宽度，而纸的 CSS 尺寸一个像素没变，
    // ResizeObserver 量到的宽度是同一个数、就此打住。没人管的话画布就停在旧精度的
    // 位图上被浏览器拉大，越放越糊。
    const ui = await opened()
    // 挂载时那次重排的防抖是 200 毫秒，等它落定，别把它的重画算进来。
    await new Promise((r) => setTimeout(r, 300))
    const drawn = pdf.renders
    expect(drawn).toBeGreaterThan(0)

    zoomBrowserTo(2)

    // 手停 150 毫秒后再画，和捏合放大走的是同一条路。
    await new Promise((r) => setTimeout(r, 200))
    await waitFor(() => expect(pdf.renders).toBeGreaterThan(drawn))
    // 重画之前页框尺寸不动：缩放不换版面，只换位图精度。
    expect(ui.container.querySelectorAll('[data-page]')).toHaveLength(2)
  })

  it('像素比没变的那次窗口 resize 不重画', async () => {
    // 拖窗口、转屏幕方向都会来 resize，像素比往往没变。不看像素比就重画一遍，
    // 每拖一次窗口整份文档白画一回。
    const ui = await opened()
    await new Promise((r) => setTimeout(r, 300))
    const drawn = pdf.renders

    window.dispatchEvent(new Event('resize'))
    await new Promise((r) => setTimeout(r, 250))
    expect(pdf.renders).toBe(drawn)
    expect(ui.container.querySelectorAll('[data-page]')).toHaveLength(2)
  })

  it('文字层带着 pdf.js 6 按它算字号和宽高的那几个变量', async () => {
    const ui = await opened()

    // 版面在 happy-dom 里量不出来，宽度自己给：给一个和页宽不一样的数，才看得出
    // 画布用的那个比例有没有传到文字层上。
    const pv = ui.container.querySelector('.pv') as HTMLElement
    Object.defineProperty(pv, 'clientWidth', { value: 600, configurable: true })
    resize?.()
    await new Promise((r) => setTimeout(r, 300))
    await waitFor(() => expect(ui.container.querySelectorAll('canvas')).toHaveLength(2))

    const canvas = ui.container.querySelector('canvas') as HTMLCanvasElement
    const layer = ui.container.querySelector('.pv-text') as HTMLElement
    expect(layer).toBeTruthy()
    // 这一条是文字层的字从哪来的：字号 = --total-scale-factor × 每个 span 自己的
    // --font-height，宽高也是 pdf.js 按这个变量写在层上的。它按自己所在的元素读这些
    // 变量，少了就整层按继承来的字号排版——画布照常是对的，只有选中落在别处。
    // 假的那一页 600 宽，页框就摆成 568（画布铺满页框）：两个数都得是同一个比例。
    const box = canvas.closest('[data-page]') as HTMLElement
    expect(Number(layer.style.getPropertyValue('--total-scale-factor'))).toBeCloseTo(
      Number.parseFloat(box.style.width) / 600,
      6
    )
    expect(layer.style.getPropertyValue('--scale-round-x')).not.toBe('')
    expect(layer.style.getPropertyValue('--scale-round-y')).not.toBe('')
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

  it('有已核过的身份时，选中一段发的是结构化引用，不是那句拼话', async () => {
    // 这一条原本是这句话的反面：分页文档选段只发一句不带版本的纯文本，而幻灯片同一
    // 件事发的是带身份、带两侧文字的 `slide-page`。两边本是一回事，受话人拿不到版本
    // 就回不到读者指的那一版。
    const ui = await opened({ data: new ArrayBuffer(8), context })
    await new Promise((r) => setTimeout(r, 300))

    const host = ui.container.querySelector('[data-page="2"]') as HTMLElement
    const layer = document.createElement('div')
    layer.className = 'pv-text'
    // 真的文字层由 pdf.js 立出来；假的这条自己摆一层，好让选区落进它里面，两侧文字
    // 也才量得到（`contextAround` 量的是它给的根）。
    const node = document.createTextNode('前文重试三次后文')
    layer.appendChild(node)
    host.appendChild(layer)
    const selection = window.getSelection()!
    const range = document.createRange()
    range.setStart(node, 2)
    range.setEnd(node, 6)
    selection.removeAllRanges()
    selection.addRange(range)
    await fireEvent.mouseUp(host)

    const emitted = (ui.emitted('page-context') ?? []) as unknown[][]
    expect(emitted).toHaveLength(1)
    const [payload] = emitted[0] as [
      { text: string; page: number; scope: string; context: typeof context; prefix: string; suffix: string },
    ]
    expect(payload.text).toBe('重试三次')
    expect(payload.page).toBe(2)
    expect(payload.scope).toBe('selection')
    // 带的是这一份已验证的身份：受话人据此回到读者当时看的那一版。
    expect(payload.context).toEqual(context)
    // 两侧文字：同一句话在这一页往往不止一处，前后文才是能分辨它说的是哪一处的东西。
    expect(payload.prefix).toBe('前文')
    expect(payload.suffix).toBe('后文')
    // 老那条出口没被用上——用了就会同时发两种引用。
    expect(ui.emitted('quote')).toBeUndefined()
    selection.removeAllRanges()
  })

  it('还没核过身份时，仍旧发那句不带版本的拼话', async () => {
    // 没有身份不是「不发」——读者那一下指的确实是一处；拼话至少把选中的原文交出去，
    // 只是指不回哪一版。这条守着别为了结构化把这条老路掐掉。
    const ui = await opened({ data: new ArrayBuffer(8) })
    await new Promise((r) => setTimeout(r, 300))

    const host = ui.container.querySelector('[data-page="1"]') as HTMLElement
    const node = document.createTextNode('第一页正文')
    host.appendChild(node)
    const selection = window.getSelection()!
    const range = document.createRange()
    range.setStart(node, 0)
    range.setEnd(node, 3)
    selection.removeAllRanges()
    selection.addRange(range)
    await fireEvent.mouseUp(host)

    await waitFor(() => expect(ui.emitted('quote')).toHaveLength(1))
    expect(ui.emitted('quote')![0]).toEqual([{ text: '第一页', page: 1 }])
    expect(ui.emitted('page-context')).toBeUndefined()
    selection.removeAllRanges()
  })

  it('选区跨了两页时，不拿起点那一页的后文当两侧文字', async () => {
    // 拖过页脚的选区起在这一页、结在下一页。只核起点的话 `contextAround` 会把结尾挪
    // 到起点那儿，`suffix` 量出来是空串——交出去的是「这是第 1 页的一段」，那段话
    // 其实跨到了第 2 页。量不出来就不带两侧文字，别给一段错的。
    const ui = await opened({ data: new ArrayBuffer(8), context })
    await new Promise((r) => setTimeout(r, 300))

    const first = ui.container.querySelector('[data-page="1"]') as HTMLElement
    const second = ui.container.querySelector('[data-page="2"]') as HTMLElement
    const makeLayer = (parent: HTMLElement, body: string) => {
      const layer = document.createElement('div')
      layer.className = 'pv-text'
      const node = document.createTextNode(body)
      layer.appendChild(node)
      parent.appendChild(layer)
      return node
    }
    const head = makeLayer(first, '跨页开头')
    const tail = makeLayer(second, '跨页结尾')
    const selection = window.getSelection()!
    const range = document.createRange()
    range.setStart(head, 0)
    range.setEnd(tail, tail.length)
    selection.removeAllRanges()
    selection.addRange(range)
    await fireEvent.mouseUp(first)

    const emitted = (ui.emitted('page-context') ?? []) as unknown[][]
    expect(emitted).toHaveLength(1)
    const [payload] = emitted[0] as [{ text: string; page: number; prefix?: string; suffix?: string }]
    expect(payload.page).toBe(1)
    // 两样都不出现：没量准的东西不许冒充「这一页的上下文」。
    expect(payload.prefix).toBeUndefined()
    expect(payload.suffix).toBeUndefined()
    selection.removeAllRanges()
  })

  it('选区落在这一页的错误提示上时，不把它当成正文量两侧', async () => {
    // 页框里除了文字层，还有画不出来的那一页的说明和评论标记。拿页框当量两侧的根，
    // 就会把那段说明当正文、把它的前后当上下文——交出去的引用说的是文件里的原文，
    // 而那段字是界面自己的话。
    const ui = await opened({ data: new ArrayBuffer(8), context })
    await new Promise((r) => setTimeout(r, 300))

    const pageBox = ui.container.querySelector('[data-page="1"]') as HTMLElement
    const notice = document.createElement('div')
    notice.className = 'pv-error'
    const node = document.createTextNode('这一页无法显示：页流损坏')
    notice.appendChild(node)
    pageBox.appendChild(notice)
    const selection = window.getSelection()!
    const range = document.createRange()
    range.setStart(node, 0)
    range.setEnd(node, 6)
    selection.removeAllRanges()
    selection.addRange(range)
    await fireEvent.mouseUp(pageBox)

    const emitted = (ui.emitted('page-context') ?? []) as unknown[][]
    expect(emitted).toHaveLength(1)
    const [payload] = emitted[0] as [{ text: string; prefix?: string; suffix?: string }]
    expect(payload.prefix).toBeUndefined()
    expect(payload.suffix).toBeUndefined()
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

describe('页上已有的评论标记', () => {
  const marks = [
    { id: 'c1', page: 1, x: 0.25, y: 0.5 },
    { id: 'c2', page: 2, x: 0.75, y: 0.1 },
  ]

  it('每一处画在它说的那一页、那个比例位置上', async () => {
    const ui = await opened({ data: new ArrayBuffer(8), context, marks })

    const onOne = ui.container.querySelector('[data-page="1"] [data-comment-mark="c1"]') as HTMLElement
    const onTwo = ui.container.querySelector('[data-page="2"] [data-comment-mark="c2"]') as HTMLElement
    expect(onOne).not.toBeNull()
    expect(onTwo).not.toBeNull()
    // 位置写的是比例，页按面板宽度重画、读者缩放都跟着走。
    expect(onOne.style.left).toBe('25%')
    expect(onOne.style.top).toBe('50%')
    expect(onTwo.style.left).toBe('75%')
    // 各在各的页上：左边那一页只有 c1，右边只有 c2。筛错了（比如每页都把前面的也画上）
    // 时，读者会在第 1 页上看到第 2 页的那一颗，指着完全不同的地方。
    expect(ui.container.querySelectorAll('[data-page="1"] [data-comment-mark]')).toHaveLength(1)
    expect(ui.container.querySelectorAll('[data-page="2"] [data-comment-mark]')).toHaveLength(1)
  })

  it('这一版里没有的那一页上不画：页数少了，那个页码落不到任何一页', async () => {
    // 评论记的是「哪一版 + 第几页 + 比例」。换成只有一页的那一版时，第 2 页上的那一处
    // 没有地方可落——这一档不去猜它去哪了，不画。
    const ui = await opened({
      data: new ArrayBuffer(8),
      context,
      marks: [{ id: 'c2', page: 9, x: 0.5, y: 0.5 }],
    })
    expect(ui.container.querySelector('[data-comment-mark="c2"]')).toBeNull()
  })

  it('点一处标记，把那一串评论开出来', async () => {
    const ui = await opened({ data: new ArrayBuffer(8), context, marks })
    await fireEvent.click(ui.container.querySelector('[data-comment-mark="c1"]')!)

    const emitted = (ui.emitted('mark') ?? []) as unknown[][]
    expect(emitted).toEqual([['c1']])
  })

  it('正在看的那一条画得显眼些', async () => {
    const ui = await opened({ data: new ArrayBuffer(8), context, marks, activeMark: 'c2' })
    expect(ui.container.querySelector('[data-comment-mark="c2"]')?.className).toContain('is-active')
    expect(ui.container.querySelector('[data-comment-mark="c1"]')?.className).not.toContain('is-active')
  })

  it('标记接得上指针：它自己就是一个按钮，不是一层盖住页面的透明块', async () => {
    // 页上原来只有「指位置」点的那个记号（`pointer-events: none`）。评论标记要能点，
    // 就必须真的收得到点击——顺手给错了类，读者点了没反应，这条守着它。
    const ui = await opened({ data: new ArrayBuffer(8), context, marks })
    const mark = ui.container.querySelector('[data-comment-mark="c1"]') as HTMLElement
    expect(mark.tagName).toBe('BUTTON')
    expect(mark.className).not.toContain('pv__pin')
    expect(mark.getAttribute('aria-label')).toBe('评论这一处')
  })
})

describe('扫描件：一页是一张图，没有文字可选', () => {
  // 选区是跨用例留着的：上一条留下的那一段会让「该是空的」那几条看不出真假。
  beforeEach(() => window.getSelection()?.removeAllRanges())

  /** 打开一份文档，等页都画出来。 */
  async function shown(doc = SCANNED_FIRST) {
    const ui = mount()
    await waitFor(() => expect(pdf.calls).toBeGreaterThan(0))
    pdf.resolveDoc?.(doc)
    await waitFor(() => expect(ui.container.querySelectorAll('canvas')).toHaveLength(doc.numPages))
    return ui
  }

  /** 键盘要走到这一页，得给它一个框和一句「滚到这儿」（happy-dom 两样都量不出来）。 */
  function reachable(container: Element, number: number) {
    const page = container.querySelector(`[data-page="${number}"]`) as HTMLElement
    page.getBoundingClientRect = () =>
      ({ left: 0, top: 0, width: 400, height: 800, right: 400, bottom: 800, x: 0, y: 0 }) as DOMRect
    page.scrollIntoView = () => {}
    return page
  }

  /** 用键盘把焦点给到某一页。 */
  async function tabTo(page: HTMLElement) {
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true }))
    await fireEvent.focus(page)
  }

  /** 选中的那一段落在第几页。
   *
   *  happy-dom 里 `activeElement` 不跟着 `focus()` 走，所以不用它判「走到哪一页了」——
   *  选区落在哪个页框里，是同一件事更靠得住的说法。 */
  function pageOfSelection(): string | null {
    const node = window.getSelection()?.anchorNode
    let el: Element | null = node?.nodeType === 1 ? (node as Element) : node?.parentElement ?? null
    while (el && !el.hasAttribute('data-page')) el = el.parentElement
    return el?.getAttribute('data-page') ?? null
  }

  it('没有文字的页说清为什么选不中，并指出还能指位置', async () => {
    const ui = await shown()
    const page = ui.container.querySelector('[data-page="1"]') as HTMLElement
    await waitFor(() => expect(page.querySelector('.pv__notext')).toBeTruthy())
    expect(page.querySelector('.pv__notext')?.textContent).toContain('选不中文字')
    expect(page.textContent).toContain('指位置可以')
  })

  it('读屏也听得到这一句：挂在页框的说明上，不只是画在屏幕上', async () => {
    const ui = await shown()
    const page = ui.container.querySelector('[data-page="1"]') as HTMLElement
    await waitFor(() => expect(page.getAttribute('aria-description')).toBeTruthy())
    expect(page.getAttribute('aria-description')).toContain('选不中文字')
  })

  it('有文字的页不挂这一句：这一句只对说得出「这里没字」的页成立', async () => {
    const ui = await shown()
    const page = ui.container.querySelector('[data-page="2"]') as HTMLElement
    await new Promise((r) => setTimeout(r, 300))
    expect(page.querySelector('.pv__notext')).toBeNull()
    expect(page.getAttribute('aria-description')).toBeNull()
  })

  it('还没画出来的页不下结论：画完之前不挂这一句', async () => {
    const ui = mount()
    await waitFor(() => expect(pdf.calls).toBeGreaterThan(0))
    pdf.resolveDoc?.(SCANNED_FIRST)
    // 页框先建出来（照第一页的大小占位），文字层还要等一会儿。
    await waitFor(() => expect(ui.container.querySelector('[data-page="1"]')).toBeTruthy())
    const page = ui.container.querySelector('[data-page="1"]') as HTMLElement
    expect(page.querySelector('.pv-text')).toBeNull()
    expect(page.querySelector('.pv__notext')).toBeNull()
    await waitFor(() => expect(page.querySelector('.pv__notext')).toBeTruthy())
  })

  it('重排之后这一句跟着撤掉，再按新的文字层重新判一次', async () => {
    // 宽度一变就整份重画，文字层全换掉。不把上一轮的结论作废，屏上会留着一句
    // 「这一页是图片」——而那时纸上的字已经能选了。
    const flip = { text: false }
    const ui = mount()
    await waitFor(() => expect(pdf.calls).toBeGreaterThan(0))
    pdf.resolveDoc?.({
      numPages: 1,
      getPage: (n: number) => Promise.resolve(page(n, flip.text)),
    })
    await waitFor(() => expect(ui.container.querySelector('.pv__notext')).toBeTruthy())

    // 这一页后来有了文字层（同一份文件重画、或者读者换了这一页的来源）。重排会把
    // 文字层整个换掉，这一句得跟着重新判一次。
    flip.text = true
    resize?.()
    await new Promise((r) => setTimeout(r, 320))

    await waitFor(() => expect(ui.container.querySelector('.pv__notext')).toBeNull())
  })

  it('键盘落到扫描件那一页时，走到后面第一页有文字的', async () => {
    const ui = await shown()
    await new Promise((r) => setTimeout(r, 300))
    const first = reachable(ui.container, 1)
    reachable(ui.container, 2)
    await tabTo(first)
    // 扫描件那一页没有行可选；焦点和光标应该落在第 2 页那一行上。
    await waitFor(() => expect(window.getSelection()?.toString()).toBe('这一页的正文'))
    expect(document.activeElement).toBe(ui.container.querySelector('[data-page="2"]'))
  })

  it('整份都是扫描件时停在原地，不来回跳', async () => {
    const ui = await shown(ALL_SCANNED)
    await new Promise((r) => setTimeout(r, 300))
    reachable(ui.container, 1)
    reachable(ui.container, 2)
    await tabTo(ui.container.querySelector('[data-page="1"]') as HTMLElement)
    // 前后都没有有文字的页：不选任何东西，也不把焦点甩到别处。
    expect(window.getSelection()?.toString()).toBe('')
    // 键盘那条路只认「Tab 出来之后焦点落在哪一页」——`keyboardNav` 已经记成键盘；
    // 这里确认第 2 页没有被当成本该去的地方（`onPageFocus` 只往有文字的页走）。
    expect(ui.container.querySelector('[data-page="2"] .pv-text')?.textContent ?? '').toBe('')
  })

  it('下一页是扫描件时，上下键跳过它走到再下一页', async () => {
    const ui = await shown(SCANNED_MIDDLE)
    await new Promise((r) => setTimeout(r, 300))
    for (const n of [1, 2, 3]) reachable(ui.container, n)

    const first = ui.container.querySelector('[data-page="1"]') as HTMLElement
    await tabTo(first)
    expect(window.getSelection()?.toString()).toBe('这一页的正文')

    // 下：第 2 页是扫描件，没有行可选，直接落到第 3 页——看选中的落点在哪一页
    // （`activeElement` 在 happy-dom 里不跟着 `focus()` 走，只看选区的锚点在哪一页）。
    await fireEvent.keyDown(first, { key: 'ArrowDown' })
    expect(pageOfSelection()).toBe('3')
  })

  it('往回走时上面没有有文字的页，就留在原地不动', async () => {
    const ui = await shown()
    await new Promise((r) => setTimeout(r, 300))
    reachable(ui.container, 1)
    const second = reachable(ui.container, 2)
    await tabTo(second)
    expect(pageOfSelection()).toBe('2')

    // 往上：第 1 页是扫描件，再上面没有了，选中的那一行留着不动。
    await fireEvent.keyDown(second, { key: 'ArrowUp' })
    expect(pageOfSelection()).toBe('2')
    expect(window.getSelection()?.toString()).toBe('这一页的正文')
  })
})

describe('键盘选字：一行一格', () => {
  // 浏览器的选区是跨用例留着的：上一条留下的那一段会让「该是空的」那几条看不出真假。
  beforeEach(() => window.getSelection()?.removeAllRanges())

  /** 把焦点用键盘的方式给到某一页：先来一下 keydown，再让焦点落上去。
   *  只 `focus()` 会被当成鼠标点进来的（按设计不对第一行动手），所以要这一步。 */
  async function tabTo(page: HTMLElement) {
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true }))
    await fireEvent.focus(page)
  }

  async function ready(texts = ['第一行的字', '第二行的字', '第三行的字', '第四行的字']) {
    const reached = await opened()
    // 挂载时那次重排的防抖是 200 毫秒，会把页里的东西整块换掉：等它落定再摆字。
    await new Promise((r) => setTimeout(r, 300))
    const page = rows(reached.container, 1, texts)
    return { ...reached, page }
  }

  it('Tab 进到页里时，第一行被选上——读者按上下键之前就知道自己在哪', async () => {
    const { page } = await ready()
    await tabTo(page)
    expect(window.getSelection()?.toString()).toBe('第一行的字')
  })

  it('按一下下是下一行，上就回上一行', async () => {
    const { page } = await ready()
    await tabTo(page)
    await press(page, 'ArrowDown')
    expect(window.getSelection()?.toString()).toBe('第二行的字')
    await press(page, 'ArrowDown')
    expect(window.getSelection()?.toString()).toBe('第三行的字')
    await press(page, 'ArrowUp')
    expect(window.getSelection()?.toString()).toBe('第二行的字')
  })

  it('Shift 加下键是从起点扩到这儿，不是一行一行地换', async () => {
    const { page } = await ready()
    await tabTo(page)
    await press(page, 'ArrowDown', true)
    expect(window.getSelection()?.toString()).toBe('第一行的字第二行的字')
    await press(page, 'ArrowDown', true)
    expect(window.getSelection()?.toString()).toBe('第一行的字第二行的字第三行的字')
    // 松开 Shift 再按：起点挪到当前这一行，选区收成一行。
    await press(page, 'ArrowDown')
    expect(window.getSelection()?.toString()).toBe('第四行的字')
  })

  it('Shift 往上缩也是同一段：方向只是从哪头量', async () => {
    const { page } = await ready()
    await tabTo(page)
    await press(page, 'ArrowDown')
    await press(page, 'ArrowDown')
    await press(page, 'ArrowUp', true)
    expect(window.getSelection()?.toString()).toBe('第二行的字第三行的字')
  })

  it('回车把选中的那几行交出去，和鼠标松手走同一条出口', async () => {
    const ui = await ready(['前文重试三次后文', '第二行的字'])
    await tabTo(ui.page)
    await press(ui.page, 'ArrowDown', true)
    await press(ui.page, 'Enter')
    const emitted = (ui.emitted('quote') ?? []) as unknown[][]
    expect(emitted).toHaveLength(1)
    expect(emitted[0]).toEqual([{ text: '前文重试三次后文第二行的字', page: 1 }])
  })

  it('鼠标点进来的焦点不选第一行：那一下读者正要拖选', async () => {
    const { page } = await ready()
    // 不先按键盘，直接把焦点给它——真实浏览器里这是鼠标点上去的样子。
    await fireEvent.focus(page)
    expect(window.getSelection()?.toString()).toBe('')
  })

  it('还没用键盘进过这一页时，方向键不管（鼠标选字后按方向键该滚页面）', async () => {
    const { page } = await ready()
    await press(page, 'ArrowDown')
    expect(window.getSelection()?.toString()).toBe('')
  })

  it('走到这一页的最后一行，再往下就翻到下一页的第一行', async () => {
    const ui = await ready()
    rows(ui.container, 2, ['第二页的头一行', '第二页的第二行'])
    await tabTo(ui.page)
    await press(ui.page, 'ArrowDown')
    await press(ui.page, 'ArrowDown')
    await press(ui.page, 'ArrowDown')
    expect(window.getSelection()?.toString()).toBe('第四行的字')
    await press(ui.page, 'ArrowDown')
    expect(window.getSelection()?.toString()).toBe('第二页的头一行')
  })

  it('Escape 放下选中的那一段', async () => {
    const { page } = await ready()
    await tabTo(page)
    await press(page, 'ArrowDown')
    await press(page, 'Escape')
    expect(window.getSelection()?.toString()).toBe('')
  })

  it('页框是可聚焦的、说得出这是第几页', async () => {
    const { container } = await opened()
    const page = container.querySelector('[data-page="1"]') as HTMLElement
    expect(page.getAttribute('tabindex')).toBe('0')
    expect(page.getAttribute('aria-label')).toBe('第 1 页')
  })

  it('整份文档在 Tab 顺序里只占一格：两百页不用按两百下 Tab', async () => {
    const { container } = await opened()
    const stops = [...container.querySelectorAll('[data-page]')].map((el) => el.getAttribute('tabindex'))
    expect(stops.filter((t) => t === '0')).toHaveLength(1)
    expect(stops.filter((t) => t === '-1')).toHaveLength(1)
  })

  it('键盘走到第二页后，Tab 的那一格跟着挪过去', async () => {
    const ui = await ready(['一', '二'])
    rows(ui.container, 2, ['三', '四'])
    await tabTo(ui.page)
    await press(ui.page, 'ArrowDown', true)
    await press(ui.page, 'ArrowDown')
    await press(ui.page, 'ArrowDown')
    const stops = [...ui.container.querySelectorAll('[data-page]')].map((el) => el.getAttribute('tabindex'))
    expect(stops).toEqual(['-1', '0'])
  })
})

describe('手指放大', () => {
  /** 点两下：两次抬起隔 150 毫秒、落在同一处。happy-dom 的触摸事件没有构造参数，自己挂。 */
  function doubleTap(el: HTMLElement) {
    for (const at of [1000, 1150]) {
      for (const type of ['touchstart', 'touchend']) {
        const event = new Event(type, { bubbles: true, cancelable: true })
        const point = [{ clientX: 50, clientY: 50, identifier: 0 }]
        Object.defineProperties(event, {
          touches: { value: type === 'touchstart' ? point : [] },
          changedTouches: { value: point },
          timeStamp: { value: at },
        })
        el.dispatchEvent(event)
      }
    }
  }
  const boxWidth = (ui: ReturnType<typeof mount>) =>
    Number.parseFloat((ui.container.querySelector('[data-page]') as HTMLElement).style.width)

  it('点两下页放大，再点两下回到整页；画布不清空，等手停了再重画', async () => {
    const ui = await opened({ data: new ArrayBuffer(8), context })
    const pv = ui.container.querySelector('.pv') as HTMLElement
    Object.defineProperty(pv, 'clientWidth', { value: 600, configurable: true })
    resize?.()
    await new Promise((r) => setTimeout(r, 300))
    await waitFor(() => expect(boxWidth(ui)).toBe(568))

    // happy-dom 量不出文字的位置，找不到「那一栏」，按两倍放大。
    doubleTap(pv)
    expect(boxWidth(ui)).toBe(1136)
    // 拉大的旧图留在页上，不是一块白。
    expect(ui.container.querySelectorAll('canvas')).toHaveLength(2)

    doubleTap(pv)
    expect(boxWidth(ui)).toBe(568)
  })

  it('指位置开着时点两下不放大：那是在指两处', async () => {
    const ui = await opened({ data: new ArrayBuffer(8), context })
    const pv = ui.container.querySelector('.pv') as HTMLElement
    Object.defineProperty(pv, 'clientWidth', { value: 600, configurable: true })
    resize?.()
    await new Promise((r) => setTimeout(r, 300))
    await waitFor(() => expect(boxWidth(ui)).toBe(568))
    await fireEvent.click(ui.getByRole('button', { name: '指位置' }))
    doubleTap(pv)
    expect(boxWidth(ui)).toBe(568)
  })
})

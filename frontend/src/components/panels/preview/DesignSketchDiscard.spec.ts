// 关掉/离开这块正在标注的图之前先问一句「放弃这些标注？」——画了、还没发出去的
// 笔画才拦。文案照参考物（Claude 桌面版那套标注器）：Discard your annotations?。
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { confirmAnnotationDiscard, hasUnsentAnnotations } from './annotationDiscard'
import DesignImage from './DesignImage.vue'

import { setLocale } from '@/i18n'
import { nextMillisecond } from '@/test/nextMillisecond'

// 合成标注图要真 canvas，测试环境里没有；只把这一步换成成功，别的照旧。
vi.mock('./designSketch', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./designSketch')>()
  return { ...actual, composeSketch: async () => new Blob(['annotated']) }
})

let observed: Map<Element, ResizeObserverCallback>
beforeEach(() => {
  setLocale('zh-CN')
  observed = new Map()
  vi.stubGlobal(
    'ResizeObserver',
    class {
      constructor(private callback: ResizeObserverCallback) {}
      observe(element: Element) {
        observed.set(element, this.callback)
      }
      disconnect() {}
    }
  )
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

/** 量测落在 rAF 里（happy-dom 用 setImmediate 实现）：手势之前先让排着的那一帧跑完。 */
async function settled() {
  await new Promise((resolve) => setTimeout(resolve, 0))
  await new Promise((resolve) => setTimeout(resolve, 0))
}

/**
 * 手势之前的体检。
 *
 * `down()` 拿不到完整的量测就静默什么都不画——按不出笔画，测试里只看到「等了半天什么都没有」。
 * 量测的三样（img 的 complete、它的 rect、pane 的宽度）全是测试按元素打上去的补丁，元素被
 * 换掉或那一帧没跑就一起失效，所以这里先摆出来：哪一样不对，失败信息里直接是那个对象。
 */
async function ready(ui: ReturnType<typeof render>) {
  await settled()
  const pane = ui.container.querySelector('.design-image__pane') as HTMLElement | null
  const live = pane?.querySelector('img') as HTMLImageElement | null
  expect({
    complete: live?.complete === true,
    rect: live?.getBoundingClientRect().width ?? 0,
    paneWidth: pane?.clientWidth ?? 0,
  }).toEqual({ complete: true, rect: 500, paneWidth: 532 })
}

async function painted(ui: ReturnType<typeof render>) {
  await waitFor(() => expect(ui.container.querySelector('.design-image__pane img')).toBeTruthy())
  const pane = ui.container.querySelector('.design-image__pane') as HTMLElement
  const image = pane.querySelector('img')!
  Object.defineProperty(pane, 'clientWidth', { configurable: true, value: 532 })
  Object.defineProperties(image, {
    complete: { configurable: true, value: true },
    naturalWidth: { configurable: true, value: 1000 },
    naturalHeight: { configurable: true, value: 500 },
  })
  image.getBoundingClientRect = () => ({ left: 10, top: 20, width: 500, height: 250 }) as DOMRect
  await waitFor(() => expect(observed.has(pane)).toBe(true))
  observed.get(pane)!([], {} as ResizeObserver)
  await fireEvent.load(image)
  await ready(ui)
  return image
}

function mount() {
  return render(DesignImage, { props: { src: 'blob:sketch', alt: 'design.png', identity: 'v1' } })
}

/** 挑一个工具，等它显示成选中、并跨过挂载那一毫秒再动手。见 DesignSketchObjectEdit.spec.ts 里的同名函数。 */
async function pickTool(ui: ReturnType<typeof render>, label: string) {
  await fireEvent.click(ui.getByRole('button', { name: label }))
  await waitFor(() =>
    expect(
      ui.container.querySelector(`.sketch-toolbar__tool[aria-label="${label}"]`)?.getAttribute('aria-pressed')
    ).toBe('true')
  )
  await nextMillisecond()
}

const rects = (ui: ReturnType<typeof render>) => ui.container.querySelectorAll('.sketch-overlay rect').length

/** 画一个矩形，并且等它真的落在屏上：后面每一条断言都从「这一笔在」出发。 */
async function drawRect(ui: ReturnType<typeof render>) {
  await ready(ui)
  const before = rects(ui)
  await pickTool(ui, '矩形')
  const layer = ui.getByRole('application', { name: '图片标注画布' })
  layer.setPointerCapture = vi.fn()
  await fireEvent.pointerDown(layer, { button: 0, pointerId: 11, clientX: 60, clientY: 70 })
  await fireEvent.pointerMove(layer, { pointerId: 11, clientX: 160, clientY: 120 })
  await fireEvent.pointerUp(layer, { pointerId: 11, clientX: 160, clientY: 120 })
  await waitFor(() => expect(rects(ui)).toBeGreaterThan(before), { timeout: 5000 })
}

describe('离开前问一句', () => {
  it('没画东西不拦：默认放行', async () => {
    const ui = mount()
    await painted(ui)
    expect(hasUnsentAnnotations()).toBe(false)
    await expect(confirmAnnotationDiscard()).resolves.toBe(true)
  })

  it('画了还没发就登记，确认框照参考物的文案', async () => {
    const ui = mount()
    await painted(ui)
    await drawRect(ui)
    await waitFor(() => expect(hasUnsentAnnotations()).toBe(true))

    const asked = confirmAnnotationDiscard()
    const dialog = await ui.findByRole('alertdialog', { name: '放弃这些标注？' })
    expect(dialog).toBeTruthy()
    // 保留：什么都不丢，返回 false。
    await fireEvent.click(ui.getByRole('button', { name: '保留' }))
    await expect(asked).resolves.toBe(false)
    expect(rects(ui)).toBe(1)
    expect(hasUnsentAnnotations()).toBe(true)
  })

  it('确认放弃：笔画清掉、登记撤下，紧接着再问一次不会再弹', async () => {
    const ui = mount()
    await painted(ui)
    await drawRect(ui)
    await waitFor(() => expect(hasUnsentAnnotations()).toBe(true))

    const asked = confirmAnnotationDiscard()
    await ui.findByRole('alertdialog', { name: '放弃这些标注？' })
    await fireEvent.click(ui.getByRole('button', { name: '放弃' }))
    await expect(asked).resolves.toBe(true)
    await waitFor(() => expect(rects(ui)).toBe(0))
    expect(hasUnsentAnnotations()).toBe(false)
    await expect(confirmAnnotationDiscard()).resolves.toBe(true)
  })

  it('发出去之后就不再拦：笔画还留在屏上，但已经不欠谁了', async () => {
    const ui = mount()
    await painted(ui)
    await drawRect(ui)
    await waitFor(() => expect(hasUnsentAnnotations()).toBe(true))
    await fireEvent.update(ui.getByPlaceholderText('说一句要改什么，回车发送'), '改成蓝色')
    await fireEvent.click(ui.getByRole('button', { name: '加入对话' }))
    await waitFor(() => expect(hasUnsentAnnotations()).toBe(false))
    // 笔画还在（发出去只是合成一张图，不改屏上的东西），拦不拦看的是「有没有没发出去的」。
    expect(rects(ui)).toBe(1)
    await expect(confirmAnnotationDiscard()).resolves.toBe(true)
  })

  it('发过之后撤销再重做：屏上又跟发出去的一样了，不该再拦', async () => {
    const ui = mount()
    await painted(ui)
    await drawRect(ui)
    await waitFor(() => expect(hasUnsentAnnotations()).toBe(true))
    await fireEvent.update(ui.getByPlaceholderText('说一句要改什么，回车发送'), '改成蓝色')
    await fireEvent.click(ui.getByRole('button', { name: '加入对话' }))
    await waitFor(() => expect(hasUnsentAnnotations()).toBe(false))

    // 撤销再重做，净变化为零：屏上那几笔又是发出去时的样子，没欠谁。
    await fireEvent.click(ui.getByRole('button', { name: '撤销' }))
    await fireEvent.click(ui.getByRole('button', { name: '重做' }))
    expect(rects(ui)).toBe(1)
    await waitFor(() => expect(hasUnsentAnnotations()).toBe(false))
    await expect(confirmAnnotationDiscard()).resolves.toBe(true)
  })

  it('发过之后只撤销（没重做）：屏上少了东西，得重新拦', async () => {
    const ui = mount()
    await painted(ui)
    await drawRect(ui)
    await drawRect(ui)
    await waitFor(() => expect(rects(ui)).toBe(2))
    await waitFor(() => expect(hasUnsentAnnotations()).toBe(true))
    await fireEvent.update(ui.getByPlaceholderText('说一句要改什么，回车发送'), '改成蓝色')
    await fireEvent.click(ui.getByRole('button', { name: '加入对话' }))
    await waitFor(() => expect(hasUnsentAnnotations()).toBe(false))

    // 只撤销一笔：屏上跟发出去的已经不一样了，该重新拦。
    await fireEvent.click(ui.getByRole('button', { name: '撤销' }))
    expect(rects(ui)).toBe(1)
    await waitFor(() => expect(hasUnsentAnnotations()).toBe(true))
  })

  it('换个工具不算离开：不弹框，没发出去的笔画照旧登记着', async () => {
    const ui = mount()
    await painted(ui)
    await drawRect(ui)
    await waitFor(() => expect(hasUnsentAnnotations()).toBe(true))
    await pickTool(ui, '自由画笔')
    expect(ui.queryByRole('alertdialog')).toBeNull()
    expect(hasUnsentAnnotations()).toBe(true)
    expect(rects(ui)).toBe(1)
  })

  // 对话框还开着的时候又来一次导航（双击页签、先点页签再点关闭、快捷键与点击几乎
  // 同时触发两次 setTab/closeFile）。第二次不该把第一次的 Promise 顶掉：那样第一次
  // 永远等不到答复，它该做的切换/关闭就静默丢了。一次回答要答得住两次。
  it('弹框还开着时又来一次导航：两次请求共用一个答复，没有悬着的 Promise', async () => {
    const ui = mount()
    await painted(ui)
    await drawRect(ui)
    // 分两步：先等框上屏，再断言登记。两步分开是为了区分「笔画根本没落下来」和
    // 「笔画落下来了但没登记」——它们坏的是两个地方，而上屏的那一步有 DOM 变化，
    // waitFor 的观察器一到就叫醒，不靠轮询撞。
    await waitFor(() => expect(rects(ui)).toBe(1))
    expect(hasUnsentAnnotations()).toBe(true)

    const first = confirmAnnotationDiscard()
    await ui.findByRole('alertdialog', { name: '放弃这些标注？' })
    const second = confirmAnnotationDiscard()
    // 合并进同一个弹框，不是再弹一个。
    expect(ui.getAllByRole('alertdialog')).toHaveLength(1)

    await fireEvent.click(ui.getByRole('button', { name: '保留' }))
    await expect(second).resolves.toBe(false)
    // 第一次也得跟着同一个答复落下来，不能悬着。
    const firstOutcome = await Promise.race([
      first.then((go) => `resolved:${go}`),
      new Promise<string>((resolve) => setTimeout(() => resolve('pending'), 100)),
    ])
    expect(firstOutcome).toBe('resolved:false')
    // 「保留」什么都不丢。
    expect(rects(ui)).toBe(1)
    expect(hasUnsentAnnotations()).toBe(true)
  })

  it('开着时第二次选了「放弃」：两次都说可以走，笔画只清一次', async () => {
    const ui = mount()
    await painted(ui)
    await drawRect(ui)
    await waitFor(() => expect(hasUnsentAnnotations()).toBe(true))

    const first = confirmAnnotationDiscard()
    await ui.findByRole('alertdialog', { name: '放弃这些标注？' })
    const second = confirmAnnotationDiscard()
    await fireEvent.click(ui.getByRole('button', { name: '放弃' }))
    await expect(first).resolves.toBe(true)
    await expect(second).resolves.toBe(true)
    await waitFor(() => expect(rects(ui)).toBe(0))
    expect(hasUnsentAnnotations()).toBe(false)
    // 答过之后不该再有第四个悬着的东西：再问直接放行。
    await expect(confirmAnnotationDiscard()).resolves.toBe(true)
  })

  it('焦点先落在「保留」上，关掉之后还给原来那个地方', async () => {
    const ui = mount()
    await painted(ui)
    await drawRect(ui)
    await waitFor(() => expect(hasUnsentAnnotations()).toBe(true))

    const origin = ui.getByRole('button', { name: '矩形' })
    origin.focus()
    expect(document.activeElement).toBe(origin)

    const asked = confirmAnnotationDiscard()
    const keep = await ui.findByRole('button', { name: '保留' })
    await waitFor(() => expect(document.activeElement).toBe(keep))
    await fireEvent.click(keep)
    await expect(asked).resolves.toBe(false)
    expect(document.activeElement).toBe(origin)
  })

  it('Tab 在弹框里绕圈，不漏到后面那份界面上', async () => {
    const ui = mount()
    await painted(ui)
    await drawRect(ui)
    await waitFor(() => expect(hasUnsentAnnotations()).toBe(true))

    confirmAnnotationDiscard()
    const keep = await ui.findByRole('button', { name: '保留' })
    const discard = ui.getByRole('button', { name: '放弃' })
    keep.focus()
    await fireEvent.keyDown(keep, { key: 'Tab' })
    expect(document.activeElement).toBe(discard)
    await fireEvent.keyDown(discard, { key: 'Tab' })
    expect(document.activeElement).toBe(keep)
    await fireEvent.keyDown(keep, { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(discard)
    // 收尾：答一句，免得弹框留在那儿。
    await fireEvent.click(keep)
  })

  it('这一页没在看了就不登记：画了东西、但收起来的那一页不拦别人', async () => {
    const ui = mount()
    await painted(ui)
    await drawRect(ui)
    await waitFor(() => expect(hasUnsentAnnotations()).toBe(true))
    await ui.rerender({ src: 'blob:sketch', alt: 'design.png', identity: 'v1', active: false })
    await waitFor(() => expect(hasUnsentAnnotations()).toBe(false))
    // 笔画还在，只是这一页没在看——登记的是「正在看的、且有未发笔画」的那一块。
    expect(rects(ui)).toBe(1)
    await expect(confirmAnnotationDiscard()).resolves.toBe(true)
  })
})

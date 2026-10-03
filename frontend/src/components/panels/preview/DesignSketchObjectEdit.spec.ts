// 对象级编辑，在真的 DesignImage 里跑一遍：点中一笔就选中并进入编辑，拖动整笔、拖把手
// 缩放，删除、改色、双击改文字；点在空白处拖仍然交给区域选择。撤销粒度、画完自动选中
// 也在这里钉住。
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import DesignImage from './DesignImage.vue'

import { setLocale } from '@/i18n'
import { nextMillisecond } from '@/test/nextMillisecond'

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
 * `down()` 拿不到完整的量测就静默什么都不画——按不出笔画、点不出输入框，测试里只看到
 * 「等了半天什么都没有」。量测的三样（img 的 complete、它的 rect、pane 的宽度）全都
 * 是测试按元素打上去的补丁，元素被换掉或那一帧没跑就一起失效，所以这里先摆出来：哪一样
 * 不对，失败信息里直接是那个对象。
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

/**
 * 挑一个工具，等它真的显示成选中、并且时钟走过这一毫秒，才让调用方动手。
 *
 * 两件事都要等，各治一种跑法：
 *
 * - `fireEvent.click` 只等到 Vue 那一次 flush。工具是 DesignImage 的 ref 再当 prop 递给
 *   画布，机器慢的时候这一下未必已经落到工具栏的 aria-pressed 上，手势就按下去了——
 *   `down()` 看见的还是上一个工具，要是 select 它直接什么都不做：一笔画不出来、输入框也
 *   不出现，测试里只看到「等了半天什么都没有」。
 * - 点工具才挂上画布，而 Vue 会把「挂上那一毫秒里到达的事件」当成早于监听器的事件丢掉
 *   （`event._vts <= invoker.attached`）；`waitFor` 第一次是同步判的，断言当场就成立时
 *   一个计时器都不会走，可能还停在同一毫秒。所以还要显式跨过这一毫秒，见 nextMillisecond。
 */
async function pickTool(ui: ReturnType<typeof render>, label: string) {
  await fireEvent.click(ui.getByRole('button', { name: label }))
  await waitFor(() =>
    expect(
      ui.container.querySelector(`.sketch-toolbar__tool[aria-label="${label}"]`)?.getAttribute('aria-pressed')
    ).toBe('true')
  )
  await nextMillisecond()
}

/**
 * 输入框没来时的现场。
 *
 * 手势到输入框之间有好几处会静默退出（量测拿不到、命中不到、工具已经切回 select），
 * 只报「找不到占位符」看不出是哪一处。这条一句话要排在报错信息最前面：CI 上的注解
 * 只保留四 KB，排在 Testing Library 那坨 DOM 后面就被截掉了。
 */
function probe(ui: ReturnType<typeof render>) {
  const pressed = (label: string) =>
    ui.container.querySelector(`.sketch-toolbar__tool[aria-label="${label}"]`)?.getAttribute('aria-pressed')
  const pane = ui.container.querySelector('.design-image__pane') as HTMLElement | null
  const image = pane?.querySelector('img') as HTMLImageElement | null
  return [
    `文字工具=${pressed('文字')} 选择工具=${pressed('选择图片区域')}`,
    `画布层=${ui.container.querySelectorAll('[role="application"]').length} 屏上文字=${ui.container.querySelectorAll('.sketch-overlay text').length}`,
    `img=${image?.isConnected ? `complete=${image.complete} rect=${image.getBoundingClientRect().width}` : '不在屏上'} paneWidth=${pane?.clientWidth}`,
  ].join(' | ')
}

/** 等文字输入框出现；没等到就把现场一起报出来。跨过挂载那一毫秒，输入框上的按键才不会被
 *  Vue 当成「挂上之前的事件」丢掉（见 nextMillisecond）。 */
async function textField(ui: ReturnType<typeof render>) {
  try {
    const field = (await waitFor(() => ui.getByPlaceholderText('输入文字，回车确认'))) as HTMLInputElement
    await nextMillisecond()
    return field
  } catch (error) {
    const why = error instanceof Error ? error.message : String(error)
    throw new Error(`${probe(ui)}\n${why}`)
  }
}

/** scale = (532-32)/1000 = 0.5；图片显示在 (10,20)-(510,270)。 */
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
}

const sheet = (ui: ReturnType<typeof render>) => {
  const element = ui.container.querySelector('.design-image__sheet') as HTMLElement
  element.setPointerCapture = vi.fn()
  return element
}
const strokeRects = (ui: ReturnType<typeof render>) =>
  ui.container.querySelectorAll('.sketch-overlay rect:not(.sketch-overlay__frame)')
const overlayRect = (ui: ReturnType<typeof render>) =>
  ui.container.querySelector('.sketch-overlay rect') as SVGRectElement
const handleCount = (ui: ReturnType<typeof render>) => ui.container.querySelectorAll('.sketch-overlay__handle').length

/** 画一个够大的矩形：client (60,70)→(310,220) 即原图 (100,100)→(600,400)。 */
async function drawRect(ui: ReturnType<typeof render>) {
  await ready(ui)
  await pickTool(ui, '矩形')
  const layer = ui.getByRole('application', { name: '图片标注画布' })
  layer.setPointerCapture = vi.fn()
  await fireEvent.pointerDown(layer, { button: 0, pointerId: 11, clientX: 60, clientY: 70 })
  await fireEvent.pointerMove(layer, { pointerId: 11, clientX: 310, clientY: 220 })
  await fireEvent.pointerUp(layer, { pointerId: 11, clientX: 310, clientY: 220 })
  try {
    await waitFor(() => expect(strokeRects(ui)).toHaveLength(1), { timeout: 5000 })
  } catch (error) {
    throw new Error(`${probe(ui)}\n${error}`)
  }
}

function mount() {
  return render(DesignImage, { props: { src: 'blob:sketch', alt: 'design.png', identity: 'v1' } })
}

it('画完一个非自由笔的图形就自动选中它、工具回到 select', async () => {
  const ui = mount()
  await painted(ui)
  await drawRect(ui)
  expect(ui.getByRole('button', { name: '选择图片区域' }).getAttribute('aria-pressed')).toBe('true')
  expect(handleCount(ui)).toBe(8)
})

it('点中已画好的矩形就选中它（出现把手）', async () => {
  const ui = mount()
  await painted(ui)
  await drawRect(ui)
  // 先切到别的工具放下选中，再回来点它。
  await pickTool(ui, '椭圆')
  expect(handleCount(ui)).toBe(0)
  await pickTool(ui, '选择图片区域')
  // 矩形屏上外框 (50,50)-(300,200)，点上边 (110,50)。
  await fireEvent.pointerDown(sheet(ui), { button: 0, pointerId: 21, clientX: 120, clientY: 70 })
  expect(handleCount(ui)).toBe(8)
})

it('拖动选中对象：松手才记一条历史，撤销一次就回到原位', async () => {
  const ui = mount()
  await painted(ui)
  await drawRect(ui)
  expect(overlayRect(ui).getAttribute('x')).toBe('50')

  const layer = sheet(ui)
  await fireEvent.pointerDown(layer, { button: 0, pointerId: 31, clientX: 120, clientY: 70 })
  // 移动 20px（> 3px 门槛）——显示 +20 即原图 +40。
  await fireEvent.pointerMove(layer, { pointerId: 31, clientX: 140, clientY: 70 })
  await fireEvent.pointerUp(layer, { pointerId: 31, clientX: 140, clientY: 70 })
  await waitFor(() => expect(overlayRect(ui).getAttribute('x')).toBe('70'))

  await fireEvent.keyDown(window, { key: 'z', code: 'KeyZ', metaKey: true })
  await waitFor(() => expect(overlayRect(ui).getAttribute('x')).toBe('50'))
})

it('不到门槛的那点位移不算拖动，不记历史', async () => {
  const ui = mount()
  await painted(ui)
  await drawRect(ui)
  const layer = sheet(ui)
  await fireEvent.pointerDown(layer, { button: 0, pointerId: 32, clientX: 120, clientY: 70 })
  await fireEvent.pointerMove(layer, { pointerId: 32, clientX: 121, clientY: 70 })
  await fireEvent.pointerUp(layer, { pointerId: 32, clientX: 121, clientY: 70 })
  // 撤销一次：画的那一笔没了（说明拖动没往历史里加东西）。
  await fireEvent.keyDown(window, { key: 'z', code: 'KeyZ', metaKey: true })
  await waitFor(() => expect(strokeRects(ui)).toHaveLength(0))
})

it('拖的时候撤销被拒绝', async () => {
  const ui = mount()
  await painted(ui)
  await drawRect(ui)
  const layer = sheet(ui)
  await fireEvent.pointerDown(layer, { button: 0, pointerId: 33, clientX: 120, clientY: 70 })
  await fireEvent.pointerMove(layer, { pointerId: 33, clientX: 140, clientY: 70 })
  // 还没松手：这一下撤销不该动它。
  await fireEvent.keyDown(window, { key: 'z', code: 'KeyZ', metaKey: true })
  expect(strokeRects(ui)).toHaveLength(1)
  expect(overlayRect(ui).getAttribute('x')).toBe('70')
  await fireEvent.pointerUp(layer, { pointerId: 33, clientX: 140, clientY: 70 })
})

it('Backspace / Delete 删掉选中的那一笔', async () => {
  const ui = mount()
  await painted(ui)
  await drawRect(ui)
  await fireEvent.keyDown(window, { key: 'Backspace' })
  await waitFor(() => expect(strokeRects(ui)).toHaveLength(0))
  // 撤销能把删掉的拿回来；撤销会放下选中，所以删除前要重新点中它。
  await fireEvent.keyDown(window, { key: 'z', code: 'KeyZ', metaKey: true })
  await waitFor(() => expect(strokeRects(ui)).toHaveLength(1))
  await fireEvent.pointerDown(sheet(ui), { button: 0, pointerId: 42, clientX: 120, clientY: 70 })
  expect(handleCount(ui)).toBe(8)
  await fireEvent.keyDown(window, { key: 'Delete' })
  await waitFor(() => expect(strokeRects(ui)).toHaveLength(0))
})

it('有选中对象时，色板改的是选中对象的颜色', async () => {
  const ui = mount()
  await painted(ui)
  await drawRect(ui)
  expect(overlayRect(ui).getAttribute('stroke')).toBe('#E03131')
  await fireEvent.click(ui.getByRole('button', { name: '标注颜色 #1971C2' }))
  await waitFor(() => expect(overlayRect(ui).getAttribute('stroke')).toBe('#1971C2'))
})

it('没有选中对象时，色板只改下一笔的颜色', async () => {
  const ui = mount()
  await painted(ui)
  await fireEvent.click(ui.getByRole('button', { name: '标注颜色 #2F9E44' }))
  await drawRect(ui)
  expect(overlayRect(ui).getAttribute('stroke')).toBe('#2F9E44')
})

/** 用涂黑工具画一块：client (60,70)→(310,220)。 */
async function drawRedact(ui: ReturnType<typeof render>) {
  await ready(ui)
  await pickTool(ui, '涂黑')
  const layer = ui.getByRole('application', { name: '图片标注画布' })
  layer.setPointerCapture = vi.fn()
  await fireEvent.pointerDown(layer, { button: 0, pointerId: 61, clientX: 60, clientY: 70 })
  await fireEvent.pointerMove(layer, { pointerId: 61, clientX: 310, clientY: 220 })
  await fireEvent.pointerUp(layer, { pointerId: 61, clientX: 310, clientY: 220 })
  try {
    await waitFor(() => expect(strokeRects(ui)).toHaveLength(1), { timeout: 5000 })
  } catch (error) {
    throw new Error(`${probe(ui)}\n${error}`)
  }
}

it('涂黑工具选中时：颜色轮换成样式行', async () => {
  const ui = mount()
  await painted(ui)
  await pickTool(ui, '涂黑')
  expect(ui.container.querySelectorAll('.sketch-toolbar__color')).toHaveLength(0)
  expect(ui.container.querySelectorAll('.sketch-toolbar__style')).toHaveLength(3)
})

it('涂黑不看颜色：画出来就是纯黑，不跟着默认红', async () => {
  const ui = mount()
  await painted(ui)
  await drawRedact(ui)
  expect(overlayRect(ui).getAttribute('fill')).toBe('#000000')
  expect(overlayRect(ui).getAttribute('stroke')).toBe('#000000')
})

it('改选中涂黑的样式：样式行的高亮跟着走', async () => {
  const ui = mount()
  await painted(ui)
  await drawRedact(ui)
  const active = () => ui.container.querySelector('.sketch-toolbar__style.is-active')?.getAttribute('aria-label')
  expect(active()).toBe('实心')
  await fireEvent.click(ui.getByRole('button', { name: '噪点' }))
  await waitFor(() => expect(active()).toBe('噪点'))
})

it('select 工具下双击文字进编辑，提交为空就删掉这条文字', async () => {
  const ui = mount()
  await painted(ui)
  // 文字工具点一下 -> 输入 '标签' -> 回车。
  await ready(ui)
  await pickTool(ui, '文字')
  const canvas = ui.getByRole('application', { name: '图片标注画布' })
  canvas.setPointerCapture = vi.fn()
  await fireEvent.pointerDown(canvas, { button: 0, pointerId: 41, clientX: 110, clientY: 120 })
  const field = await textField(ui)
  await fireEvent.update(field, '标签')
  await fireEvent.keyDown(field, { key: 'Enter' })
  await waitFor(() => expect(ui.container.querySelector('.sketch-overlay text')).toBeTruthy())

  // 双击它进编辑：原文字不画，输入框带着原文出现。
  await ready(ui)
  await fireEvent.dblClick(sheet(ui), { clientX: 110, clientY: 120 })
  const editor = await textField(ui)
  expect(editor.value).toBe('标签')
  expect(ui.container.querySelector('.sketch-overlay text')).toBeNull()

  // 提交为空 -> 这条文字被删掉。
  await fireEvent.update(editor, '  ')
  await fireEvent.blur(editor)
  await waitFor(() => expect(ui.container.querySelector('.sketch-overlay text')).toBeNull())
  // 撤销能把这条文字找回来。
  await fireEvent.keyDown(window, { key: 'z', code: 'KeyZ', metaKey: true })
  await waitFor(() => expect(ui.container.querySelector('.sketch-overlay text')).toBeTruthy())
})

it('text 工具下单击已有文字也进编辑', async () => {
  const ui = mount()
  await painted(ui)
  await ready(ui)
  await pickTool(ui, '文字')
  const canvas = ui.getByRole('application', { name: '图片标注画布' })
  canvas.setPointerCapture = vi.fn()
  await fireEvent.pointerDown(canvas, { button: 0, pointerId: 51, clientX: 110, clientY: 120 })
  const field = await textField(ui)
  await fireEvent.update(field, '标签')
  await fireEvent.keyDown(field, { key: 'Enter' })
  await waitFor(() => expect(ui.container.querySelector('.sketch-overlay text')).toBeTruthy())

  // 再点文字工具，点已有文字 -> 进编辑（带出原文）。
  await ready(ui)
  await pickTool(ui, '文字')
  const layer = ui.getByRole('application', { name: '图片标注画布' })
  await fireEvent.pointerDown(layer, { button: 0, pointerId: 52, clientX: 110, clientY: 120 })
  const editor = await textField(ui)
  expect(editor.value).toBe('标签')
})

it('点在空白处拖仍然是区域选择：交给芝士', async () => {
  const ui = mount()
  await painted(ui)
  const region = ui.container.querySelector('.raster-region') as HTMLElement
  region.setPointerCapture = vi.fn()
  await fireEvent.pointerDown(region, { button: 0, pointerId: 61, clientX: 350, clientY: 200 })
  await fireEvent.pointerMove(region, { pointerId: 61, clientX: 420, clientY: 240 })
  await fireEvent.pointerUp(region, { pointerId: 61, clientX: 420, clientY: 240 })
  await waitFor(() => expect(ui.emitted('region')).toHaveLength(1))
})

it('已有选中时点空白：放下选中，空白拖照旧给区域选择', async () => {
  const ui = mount()
  await painted(ui)
  await drawRect(ui)
  expect(handleCount(ui)).toBe(8)
  // 画完自动选中、工具切回 select，区域选择器这才挂上；同一毫秒里的按下会被 Vue 丢掉（见
  // nextMillisecond），真人的手没这么快。
  await nextMillisecond()
  const region = ui.container.querySelector('.raster-region') as HTMLElement
  region.setPointerCapture = vi.fn()
  // 离把手足够远（外框在显示 (50,50)-(300,200)）。
  await fireEvent.pointerDown(region, { button: 0, pointerId: 62, clientX: 450, clientY: 240 })
  expect(handleCount(ui)).toBe(0)
  await fireEvent.pointerMove(region, { pointerId: 62, clientX: 470, clientY: 250 })
  await fireEvent.pointerUp(region, { pointerId: 62, clientX: 470, clientY: 250 })
  await waitFor(() => expect(ui.emitted('region')).toHaveLength(1))
})

it('自由笔不可选：画完不自动选中，工具也不回 select', async () => {
  const ui = mount()
  await painted(ui)
  await pickTool(ui, '自由画笔')
  const layer = ui.getByRole('application', { name: '图片标注画布' })
  layer.setPointerCapture = vi.fn()
  await fireEvent.pointerDown(layer, { button: 0, pointerId: 71, clientX: 60, clientY: 70 })
  await fireEvent.pointerMove(layer, { pointerId: 71, clientX: 90, clientY: 90 })
  await fireEvent.pointerUp(layer, { pointerId: 71, clientX: 90, clientY: 90 })
  await waitFor(() => expect(ui.container.querySelector('.sketch-overlay polyline')).toBeTruthy())
  expect(handleCount(ui)).toBe(0)
  expect(ui.getByRole('button', { name: '自由画笔' }).getAttribute('aria-pressed')).toBe('true')
})

it('拖角把手缩放矩形：对边不动', async () => {
  const ui = mount()
  await painted(ui)
  await drawRect(ui)
  // 屏上外框显示 (50,50)-(300,200)，即 client (60,70)-(310,220)；右下角 client (310,220)。
  const layer = sheet(ui)
  await fireEvent.pointerDown(layer, { button: 0, pointerId: 81, clientX: 310, clientY: 220 })
  await fireEvent.pointerMove(layer, { pointerId: 81, clientX: 410, clientY: 270 })
  await fireEvent.pointerUp(layer, { pointerId: 81, clientX: 410, clientY: 270 })
  const box = overlayRect(ui)
  await waitFor(() => expect(Number(box.getAttribute('width'))).toBe(350))
  expect(Number(box.getAttribute('height'))).toBe(200)
  expect(Number(box.getAttribute('x'))).toBe(50)
})

it('拖角把手 + Shift 把矩形改成正方形', async () => {
  const ui = mount()
  await painted(ui)
  await drawRect(ui)
  const layer = sheet(ui)
  await fireEvent.pointerDown(layer, { button: 0, pointerId: 82, clientX: 310, clientY: 220 })
  // 拖到 client (410,240) 即显示 (400,220)：dx=100、dy=20 -> 正方形取 100。
  await fireEvent.pointerMove(layer, { pointerId: 82, clientX: 410, clientY: 240, shiftKey: true })
  await fireEvent.pointerUp(layer, { pointerId: 82, clientX: 410, clientY: 240, shiftKey: true })
  const box = overlayRect(ui)
  // 不按 Shift 时高会是 170；按了取更大的那个位移（横 100 > 纵 20），拉成 350×350 的正方形。
  await waitFor(() => expect(Number(box.getAttribute('width'))).toBe(350))
  expect(Number(box.getAttribute('height'))).toBe(350)
})

it('文字的角只挪不改大小：文字没有缩放这一档', async () => {
  const ui = mount()
  await painted(ui)
  await placeText(ui)
  // 放完文字，选中的是它，但工具还停在「文字」上（再点一下是接着放，不是拖动）。
  // 切回选择会放下选中，所以还要点它一下把选中找回来。
  await pickTool(ui, '选择图片区域')
  const layer = sheet(ui)
  await fireEvent.pointerDown(layer, { button: 0, pointerId: 80, clientX: 110, clientY: 120 })
  await fireEvent.pointerUp(layer, { button: 0, pointerId: 80, clientX: 110, clientY: 120 })
  await waitFor(() => expect(handleCount(ui)).toBe(4))
  const text = () => ui.container.querySelector('.sketch-overlay text')!
  const frame = () => ui.container.querySelector('.sketch-overlay__frame') as SVGRectElement
  const before = {
    x: Number(text().getAttribute('x')),
    y: Number(text().getAttribute('y')),
    width: Number(frame().getAttribute('width')),
    height: Number(frame().getAttribute('height')),
  }

  // 第一个把手是左上角，正好压在锚点上。图片显示在 (10,20)（见 `painted`），所以
  // client = 显示坐标 + (10,20)。拖它 40/30，文字整支平移，字号量出来的宽高不动
  // （`canResize` 对文字是假，这一下手势是 move 不是 resize）。
  const handle = ui.container.querySelector('.sketch-overlay__handle') as SVGCircleElement
  const grabX = Number(handle.getAttribute('cx')) + 10
  const grabY = Number(handle.getAttribute('cy')) + 20
  await fireEvent.pointerDown(layer, { button: 0, pointerId: 83, clientX: grabX, clientY: grabY })
  await fireEvent.pointerMove(layer, { pointerId: 83, clientX: grabX + 40, clientY: grabY + 30 })
  await fireEvent.pointerUp(layer, { pointerId: 83, clientX: grabX + 40, clientY: grabY + 30 })

  await waitFor(() => expect(Number(text().getAttribute('x'))).toBe(before.x + 40))
  expect(Number(text().getAttribute('y'))).toBe(before.y + 30)
  expect(Number(frame().getAttribute('width'))).toBe(before.width)
  expect(Number(frame().getAttribute('height'))).toBe(before.height)
})

/** 文字工具下点一下、输入、回车：屏上多一条文字。 */
async function placeText(ui: ReturnType<typeof render>, clientX = 110, clientY = 120, text = '标签') {
  await ready(ui)
  await pickTool(ui, '文字')
  const layer = ui.getByRole('application', { name: '图片标注画布' })
  layer.setPointerCapture = vi.fn()
  await fireEvent.pointerDown(layer, { button: 0, pointerId: 12, clientX, clientY })
  const field = await waitFor(() => ui.getByPlaceholderText('输入文字，回车确认'))
  await fireEvent.update(field, text)
  await fireEvent.keyDown(field, { key: 'Enter' })
  await waitFor(() => expect(ui.container.querySelector('.sketch-overlay text')).toBeTruthy())
}

it('屏上文字锚在左上角，和命中框、选中框、导出用同一个基准', async () => {
  const ui = mount()
  await painted(ui)
  await placeText(ui)
  // `at` 处处当左上角用（命中框、选中框、导出时的 textBaseline='top'）；SVG 的默认
  // 基线是中下，不显式改掉就差半个字高，框和字对不上。
  expect(ui.container.querySelector('.sketch-overlay text')!.getAttribute('dominant-baseline')).toBe('text-before-edge')
})

it('编辑文字时按下编辑框不抢：光标放得下，文字不会被顺手拖走', async () => {
  const ui = mount()
  await painted(ui)
  await placeText(ui)
  const text = () => ui.container.querySelector('.sketch-overlay text')!
  const x = text().getAttribute('x')
  const y = text().getAttribute('y')

  await fireEvent.dblClick(sheet(ui), { clientX: 110, clientY: 120 })
  const editor = (await waitFor(() => ui.getByPlaceholderText('输入文字，回车确认'))) as HTMLInputElement
  // 在编辑框里按下并拖动。输入框是 sheet 的子元素，sheet 的 capture 监听在祖先上
  // 先跑，输入框自己的 @pointerdown.stop 拦不住它 —— 这里必须让开。
  await fireEvent.pointerDown(editor, { button: 0, pointerId: 91, clientX: 110, clientY: 120 })
  await fireEvent.pointerMove(editor, { pointerId: 91, clientX: 190, clientY: 190 })
  await fireEvent.pointerUp(editor, { pointerId: 91, clientX: 190, clientY: 190 })
  // 编辑没被打断，文字也没被挪走。
  expect(await waitFor(() => ui.getByPlaceholderText('输入文字，回车确认'))).toBe(editor)
  await fireEvent.keyDown(editor, { key: 'Enter' })
  await waitFor(() => expect(text()).toBeTruthy())
  expect(text().getAttribute('x')).toBe(x)
  expect(text().getAttribute('y')).toBe(y)
})

it('编辑文字时点别处：先把编辑落下来，这一下不再顺手开始框区域', async () => {
  const ui = mount()
  await painted(ui)
  await placeText(ui)
  await fireEvent.dblClick(sheet(ui), { clientX: 110, clientY: 120 })
  const editor = (await waitFor(() => ui.getByPlaceholderText('输入文字，回车确认'))) as HTMLInputElement
  await fireEvent.update(editor, '标签')

  // 编辑期间区域选择器整个不挂出来：这一下只该把编辑落下来，不该开始框区域。
  expect(ui.container.querySelector('.raster-region')).toBeNull()

  const layer = sheet(ui)
  await fireEvent.pointerDown(layer, { button: 0, pointerId: 92, clientX: 400, clientY: 200 })
  await fireEvent.pointerMove(layer, { pointerId: 92, clientX: 460, clientY: 240 })
  await fireEvent.pointerUp(layer, { pointerId: 92, clientX: 460, clientY: 240 })
  await waitFor(() => expect(ui.container.querySelector('.sketch-overlay text')).toBeTruthy())
  expect(ui.emitted('region')).toBeUndefined()
})

it('把矩形拖到退化（宽高全零）等于删掉它，撤销能把这一笔找回来', async () => {
  const ui = mount()
  await painted(ui)
  await drawRect(ui)
  const layer = sheet(ui)
  // 抓右下角 client (310,220)，一路拖到左上角 client (60,70)：框退化成一点。
  await fireEvent.pointerDown(layer, { button: 0, pointerId: 93, clientX: 310, clientY: 220 })
  await fireEvent.pointerMove(layer, { pointerId: 93, clientX: 60, clientY: 70 })
  await fireEvent.pointerUp(layer, { pointerId: 93, clientX: 60, clientY: 70 })
  await waitFor(() => expect(strokeRects(ui)).toHaveLength(0))
  await fireEvent.keyDown(window, { key: 'z', code: 'KeyZ', metaKey: true })
  await waitFor(() => expect(strokeRects(ui)).toHaveLength(1))
})

it('拖到一半按删除：删除照做，松手也不会再多记一条历史', async () => {
  const ui = mount()
  await painted(ui)
  await drawRect(ui)
  const layer = sheet(ui)
  await fireEvent.pointerDown(layer, { button: 0, pointerId: 94, clientX: 120, clientY: 70 })
  await fireEvent.pointerMove(layer, { pointerId: 94, clientX: 140, clientY: 70 })
  await fireEvent.keyDown(window, { key: 'Delete' })
  await waitFor(() => expect(strokeRects(ui)).toHaveLength(0))
  // 手势已经被收掉，松手不该再往历史里塞东西。
  await fireEvent.pointerUp(layer, { pointerId: 94, clientX: 140, clientY: 70 })
  await fireEvent.keyDown(window, { key: 'z', code: 'KeyZ', metaKey: true })
  await waitFor(() => expect(strokeRects(ui)).toHaveLength(1))
  // 再撤一次：画的那一笔也没了。若松手多记了一条，这一下会还原成拖动后的样子。
  await fireEvent.keyDown(window, { key: 'z', code: 'KeyZ', metaKey: true })
  await waitFor(() => expect(strokeRects(ui)).toHaveLength(0))
})

it('指针捕获无故丢失：退回按下前的样子，不记历史', async () => {
  const ui = mount()
  await painted(ui)
  await drawRect(ui)
  const layer = sheet(ui)
  await fireEvent.pointerDown(layer, { button: 0, pointerId: 95, clientX: 120, clientY: 70 })
  await fireEvent.pointerMove(layer, { pointerId: 95, clientX: 140, clientY: 70 })
  await waitFor(() => expect(overlayRect(ui).getAttribute('x')).toBe('70'))
  await fireEvent.lostPointerCapture(layer, { pointerId: 95 })
  await waitFor(() => expect(overlayRect(ui).getAttribute('x')).toBe('50'))
  // 只撤销一次：画的那一笔没了，说明断开这一下没往历史里加东西。
  await fireEvent.keyDown(window, { key: 'z', code: 'KeyZ', metaKey: true })
  await waitFor(() => expect(strokeRects(ui)).toHaveLength(0))
})

it('小对象能整支拖走：抓取半径跟着对象收，不整支陷进把手的圈里', async () => {
  const ui = mount()
  await painted(ui)
  // 画一个小矩形：client (60,70)→(110,95) 即原图 (100,100)-(200,150)，屏上 (50,50)-(100,75)。
  await pickTool(ui, '矩形')
  const layer = ui.getByRole('application', { name: '图片标注画布' })
  layer.setPointerCapture = vi.fn()
  await fireEvent.pointerDown(layer, { button: 0, pointerId: 96, clientX: 60, clientY: 70 })
  await fireEvent.pointerMove(layer, { pointerId: 96, clientX: 110, clientY: 95 })
  await fireEvent.pointerUp(layer, { pointerId: 96, clientX: 110, clientY: 95 })
  await waitFor(() => expect(strokeRects(ui)).toHaveLength(1))

  const canvas = sheet(ui)
  // 上边中点 client (85,70) 即显示 (75,50)：离两个上角各 25。抓取半径收到 12.5，
  // 这一下该判成「整支拖动」；半径固定 48 时会误判成缩放。
  await fireEvent.pointerDown(canvas, { button: 0, pointerId: 97, clientX: 85, clientY: 70 })
  await fireEvent.pointerMove(canvas, { pointerId: 97, clientX: 105, clientY: 70 })
  await fireEvent.pointerUp(canvas, { pointerId: 97, clientX: 105, clientY: 70 })
  await waitFor(() => expect(overlayRect(ui).getAttribute('x')).toBe('70'))
  expect(overlayRect(ui).getAttribute('width')).toBe('50')
  expect(overlayRect(ui).getAttribute('height')).toBe('25')
})

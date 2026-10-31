/// <reference types="node" />
import { createHash, webcrypto } from 'node:crypto'

import type { FileContent } from '../../cx_types'

import { createVuetify } from 'vuetify'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import * as api from '../../api'
import { setLocale } from '../../i18n'
import { nextMillisecond } from '../../test/nextMillisecond'

import DesignImage from './preview/DesignImage.vue'

import PanelPreviewHost from '@/components/work/PanelPreviewHost.vue'

vi.mock('../../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api')>()
  return {
    ...actual,
    readPreviewFile: vi.fn(),
    previewFileBytes: vi.fn(),
    attachmentImageUrl: vi.fn(),
    uploadAttachment: vi.fn(),
  }
})
vi.mock('./preview/RevisionList.vue', () => ({ default: { template: '<div />' } }))
// 图片这一档也要画评论标记（`drawsMarks`），所以线程列表在栏关着时就会被读——这一条
// 是那一档的性质，不是这几条用例要看的事，桩掉即可（真走一遍在 `PanelPreview.fileMarks.spec.ts`）。
const threadsApi = vi.hoisted(() => ({ listFileThreads: vi.fn(), postFileThread: vi.fn() }))
vi.mock('../../api/fileThreads', () => threadsApi)
threadsApi.listFileThreads.mockResolvedValue({ data: [], total: 0 })

const bytes = new Uint8Array([1, 2, 3, 4]).buffer
const version = createHash('sha256').update(new Uint8Array(bytes)).digest('hex').slice(0, 16)
const panelProps = { topicId: 'room', projectId: 'project', path: 'design.png', active: true, refreshTick: 0 }
let observers: Map<Element, ResizeObserverCallback>
let urls = 0
beforeEach(() => {
  vi.resetAllMocks()
  observers = new Map()
  urls = 0
  setLocale('zh-CN')
  vi.stubGlobal('crypto', webcrypto)
  vi.stubGlobal(
    'ResizeObserver',
    class {
      constructor(private callback: ResizeObserverCallback) {}
      observe(element: Element) {
        observers.set(element, this.callback)
      }
      disconnect() {}
    }
  )
  vi.spyOn(URL, 'createObjectURL').mockImplementation(() => `blob:design-${++urls}`)
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
  vi.mocked(api.readPreviewFile).mockResolvedValue(file(version))
  vi.mocked(api.previewFileBytes).mockResolvedValue(bytes)
  vi.mocked(api.attachmentImageUrl).mockResolvedValue('blob:attachment')
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})
function file(fingerprint: string | null, path = 'design.png'): FileContent {
  return { path, content: null, version: fingerprint, bytes: 4, binary: true, too_large: false, source: 'committed' }
}
function mount() {
  return render(PanelPreviewHost, {
    props: panelProps,
    global: {
      plugins: [createVuetify()],
      stubs: {
        VBtn: { template: '<button><slot /></button>' },
        VIcon: true,
        VSpacer: true,
        VAlert: { template: '<div><slot /></div>' },
        VDialog: true,
      },
    },
  })
}
async function paint(ui: ReturnType<typeof mount>) {
  await waitFor(() => expect(ui.container.querySelector('.design-image img')).toBeTruthy())
  // 图上框一块、画标注都在「批注」那一段里：先切过去（默认是「看」）。
  const note = ui.queryByTestId('preview-mode-note')
  if (note) await fireEvent.click(note)
  const image = ui.container.querySelector('.design-image img') as HTMLImageElement
  const pane = ui.container.querySelector('.design-image__pane') as HTMLElement
  Object.defineProperty(pane, 'clientWidth', { value: 532, configurable: true })
  Object.defineProperties(image, {
    complete: { value: true, configurable: true },
    naturalWidth: { value: 1000, configurable: true },
    naturalHeight: { value: 500, configurable: true },
  })
  image.getBoundingClientRect = () => ({ left: 10, top: 20, width: 500, height: 250 }) as DOMRect
  await waitFor(() => expect(observers.has(pane)).toBe(true))
  observers.get(pane)!([], {} as ResizeObserver)
  await fireEvent.load(image)
  return image
}
async function select(ui: ReturnType<typeof mount>) {
  await fireEvent.click(ui.getByRole('button', { name: '标一处' }))
  // The layer has only just mounted; a pointer in that same millisecond is dropped by Vue as an
  // event older than its listener (see nextMillisecond). No real hand is that fast.
  await nextMillisecond()
  const overlay = ui.getByRole('group', { name: '图上的一块' })
  overlay.setPointerCapture = vi.fn()
  await fireEvent.pointerDown(overlay, { button: 0, pointerId: 7, clientX: 60, clientY: 70 })
  await fireEvent.pointerUp(overlay, { pointerId: 7, clientX: 160, clientY: 120 })
  // Letting go mounts the region note, and its card listens for Esc while the input inside it
  // listens for Enter: a key pressed in that same millisecond would reach only the input.
  await nextMillisecond()
}
it('routes a confirmed natural-pixel region from the actual named image and verified byte source', async () => {
  const ui = mount()
  await paint(ui)
  await select(ui)
  expect(api.previewFileBytes).toHaveBeenCalledWith('room', 'design.png', null, 'committed')
  expect(api.attachmentImageUrl).not.toHaveBeenCalled()
  expect(ui.emitted().locate).toBeUndefined()
  const input = ui.getByPlaceholderText('输入修改要求或批注内容…')
  expect(ui.getByRole('button', { name: '发送' }).hasAttribute('disabled')).toBe(true)
  await fireEvent.keyDown(input, { key: 'Enter', keyCode: 13, isComposing: false })
  expect(ui.emitted().locate).toBeUndefined()
  await fireEvent.update(input, '让这块留白更紧凑')
  await fireEvent.click(ui.getByRole('button', { name: '发送' }))
  const message = (ui.emitted().locate as [{ message: string }][] | undefined)?.[0]?.[0]?.message ?? ''
  expect(message).toContain('design.png')
  expect(message).toContain(`topic=room source=committed task= version=${version}`)
  expect(message).toContain('1000 × 500')
  expect(message).toContain('x=100 y=100 width=200 height=100')
  expect(message).toContain('让这块留白更紧凑')
  expect(ui.emitted().locate).toHaveLength(1)
  expect(ui.queryByPlaceholderText('输入修改要求或批注内容…')).toBeNull()
})
it.each([null, 'bbbbbbbbbbbbbbbb'])(
  'keeps the image readable but cannot send regions for metadata version %s',
  async (fingerprint) => {
    vi.mocked(api.readPreviewFile).mockResolvedValue(file(fingerprint))
    const ui = mount()
    await paint(ui)
    expect(ui.getByRole('img')).toBeTruthy()
    expect(ui.getByRole('button', { name: '标一处' }).hasAttribute('disabled')).toBe(true)
    expect(ui.emitted().locate).toBeUndefined()
  }
)
it('does not promote unverified bytes when source fingerprinting is unavailable', async () => {
  vi.stubGlobal('crypto', {})
  const ui = mount()
  await paint(ui)
  expect(ui.getByRole('button', { name: '标一处' }).hasAttribute('disabled')).toBe(true)
  expect(ui.emitted().locate).toBeUndefined()
})
it.each([
  { nextVersion: 'bbbbbbbbbbbbbbbb', source: 'committed' as const },
  { nextVersion: version, source: 'live' as const },
])(
  'retires a selected region when metadata changes to %s without replacement bytes',
  async ({ nextVersion, source }) => {
    const ui = mount()
    await paint(ui)
    await select(ui)
    await fireEvent.update(ui.getByPlaceholderText('输入修改要求或批注内容…'), '旧图的说明')
    vi.mocked(api.readPreviewFile).mockResolvedValue({ ...file(nextVersion), source })
    vi.mocked(api.previewFileBytes).mockReturnValue(new Promise(() => {}))
    await ui.rerender({ ...panelProps, refreshTick: 1 })
    await waitFor(() => expect(api.previewFileBytes).toHaveBeenCalledTimes(2))
    expect(ui.queryByPlaceholderText('输入修改要求或批注内容…')).toBeNull()
    expect(ui.emitted().locate).toBeUndefined()
  }
)
// ---- 框选之后弹的是哪张卡 ----
// 图片这一档曾经自造一张 `DesignRegionNote`，和别的文件类型共用的 `PreviewLocator`
// 是两套。现在统一到共用那张：框选之后弹的一定是 `.locator`，图自己的卡不再存在。
it('图片框选之后弹的是共用的那张卡，不是图自己那张', async () => {
  const ui = mount()
  await paint(ui)
  await select(ui)
  const card = ui.container.querySelector('.locator')
  expect(card).toBeTruthy()
  expect(ui.container.querySelector('.design-region-note')).toBeNull()
  // 卡上写着指的是哪儿（标签 = 「图上的一块」，原文 = 这块区域的坐标）。
  expect(card!.querySelector('.locator__label')?.textContent).toContain('图上的一块')
  expect(card!.querySelector('.locator__quote')?.textContent).toContain('x=100')
})

// 位置：卡片按那块选区的实际矩形弹在旁边。happy-dom 没有布局，所以把选区元素的矩形和
// 面板的宽高按真实浏览器里会给的值补上，再让面板重算一次位置（等同滚动/改尺寸那一帧）。
it('卡片按选区的实际矩形弹在它旁边', async () => {
  const ui = mount()
  await paint(ui)
  await select(ui)
  const selection = ui.container.querySelector('.design-image__selection') as HTMLElement
  expect(selection).toBeTruthy()
  selection.getBoundingClientRect = () =>
    ({ top: 150, left: 100, bottom: 250, right: 300, width: 200, height: 100, x: 100, y: 150, toJSON() {} }) as DOMRect
  const width = vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(800)
  const height = vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(600)
  window.dispatchEvent(new Event('resize'))
  const card = await waitFor(() => {
    const el = ui.container.querySelector('.locator') as HTMLElement
    expect(el.classList.contains('locator--floating')).toBe(true)
    return el
  })
  // 选区在面板内容坐标里是 (left 100, bottom 250)：卡片左沿对齐 100，落在它下面 8px 处。
  expect(card.style.left).toBe('100px')
  expect(card.style.top).toBe('258px')
  width.mockRestore()
  height.mockRestore()
})

// 框出来的那一块要走结构化引用那条路（连同坐标发出去），发不出才退回拼一句话——
// 两条都只发当前这一处，凑不进待发篮那批「合并发送」。所以这一档不给「加入待发」，
// 主按钮也不被篮里那几条改成「全部发送（N+1）」（那是它做不到的事）。
it('图片框选那一处也能加入待发：进篮只带位置，不当场发出去', async () => {
  const ui = mount()
  await paint(ui)
  await select(ui)
  const labels = () => [...ui.container.querySelectorAll('button')].map((b) => b.textContent?.trim() ?? '')
  expect(labels()).toContain('加入待发')
  expect(labels()).toContain('发送')
  await fireEvent.update(ui.getByPlaceholderText('输入修改要求或批注内容…'), '这块留白收一下')
  await fireEvent.click(ui.getByRole('button', { name: '加入待发' }))
  expect(ui.queryByPlaceholderText('输入修改要求或批注内容…')).toBeNull()
  expect(ui.emitted().locate).toBeUndefined()
})

it('cancels the region note without posting a message', async () => {
  const ui = mount()
  await paint(ui)
  await select(ui)
  const input = ui.getByPlaceholderText('输入修改要求或批注内容…')
  await fireEvent.update(input, '未发送的说明')
  await fireEvent.keyDown(input, { key: 'Escape' })
  expect(ui.queryByPlaceholderText('输入修改要求或批注内容…')).toBeNull()
  expect(ui.container.querySelector('.design-image__selection')).toBeNull()
  expect(ui.emitted().locate).toBeUndefined()
})
it('does not send on composition Enter and sends once on an explicit committed Enter', async () => {
  const ui = mount()
  await paint(ui)
  await select(ui)
  const input = ui.getByPlaceholderText('输入修改要求或批注内容…')
  await fireEvent.update(input, '调整文字')
  await fireEvent.keyDown(input, { key: 'Enter', keyCode: 229, isComposing: true })
  expect(ui.emitted().locate).toBeUndefined()
  await fireEvent.keyDown(input, { key: 'Enter', keyCode: 13, isComposing: false })
  expect(ui.emitted().locate).toHaveLength(1)
})

// ---- 画完的那张图进对话 ----
// 图和那句话必须一起走：只发一句「这块留白收一下」，芝士手里没有图就不知道「这块」
// 是哪一块。图走房间附件那条路（origin=clipboard），落到对话里是一条真图片输入。
//
// happy-dom 没有真的画布，合成那一步靠一个只会说「成」的画布顶过去：这里要验的是
// 「画完之后那张图有没有跟着那句话走」，不是像素。
function stubCanvas() {
  const noop = vi.fn()
  const context = {
    // 读像素直接抛：这台宿主本来就没有像素，内容分界线不存在，框选退化成自由拖。
    getImageData: () => {
      throw new Error('this host has no pixels')
    },
    save: noop,
    restore: noop,
    beginPath: noop,
    closePath: noop,
    moveTo: noop,
    lineTo: noop,
    stroke: noop,
    fill: noop,
    fillRect: noop,
    strokeRect: noop,
    ellipse: noop,
    arc: noop,
    drawImage: noop,
    fillText: noop,
  }
  // 这两样 happy-dom 根本没有（`getContext` 连属性都不在），所以不是 spyOn 而是补上，
  // 用 defineProperty 是因为 configurable 才允许下一个用例再盖一次。
  Object.defineProperty(HTMLCanvasElement.prototype, 'getContext', {
    configurable: true,
    writable: true,
    value: () => context,
  })
  Object.defineProperty(HTMLCanvasElement.prototype, 'toBlob', {
    configurable: true,
    writable: true,
    value: (callback: BlobCallback) => {
      callback(new Blob([new Uint8Array([137, 80, 78, 71])], { type: 'image/png' }))
    },
  })
}
async function draw(ui: ReturnType<typeof mount>) {
  await fireEvent.click(ui.getByRole('button', { name: '矩形' }))
  // The layer has only just mounted; a pointer in that same millisecond is dropped by Vue as an
  // event older than its listener (see nextMillisecond). No real hand is that fast.
  await nextMillisecond()
  const layer = ui.getByRole('application', { name: '图片标注画布' })
  layer.setPointerCapture = vi.fn()
  await fireEvent.pointerDown(layer, { button: 0, pointerId: 11, clientX: 60, clientY: 70 })
  await fireEvent.pointerMove(layer, { pointerId: 11, clientX: 160, clientY: 120 })
  await fireEvent.pointerUp(layer, { pointerId: 11, clientX: 160, clientY: 120 })
}
it('sends the marked-up image together with the note as one message', async () => {
  stubCanvas()
  const attachment = { id: 7, path: 'uploads/ab/design-annotated.png' }
  vi.mocked(api.uploadAttachment).mockResolvedValue(attachment as never)
  const ui = mount()
  await paint(ui)
  await draw(ui)
  await fireEvent.update(ui.getByPlaceholderText('说一句要改什么，回车发送'), '这块留白收紧一点')
  await fireEvent.click(ui.getByRole('button', { name: '加入对话' }))
  await waitFor(() => expect(ui.emitted().locate).toBeTruthy())
  expect(api.uploadAttachment).toHaveBeenCalledTimes(1)
  const [topic, file] = vi.mocked(api.uploadAttachment).mock.calls[0]
  expect(topic).toBe('room')
  expect((file as File).name).toBe('design-annotated.png')
  const payload = (ui.emitted().locate as [{ message: string; attachments?: unknown[] }][])[0][0]
  expect(payload.attachments).toEqual([attachment])
  expect(payload.message).toContain('标注 1 处')
  expect(payload.message).toContain('这块留白收紧一点')
})
it('says why and sends nothing at all when the marked-up image cannot be uploaded', async () => {
  stubCanvas()
  vi.mocked(api.uploadAttachment).mockRejectedValue(new Error('超过 10 MiB'))
  const ui = mount()
  await paint(ui)
  await draw(ui)
  await fireEvent.update(ui.getByPlaceholderText('说一句要改什么，回车发送'), '这块留白收紧一点')
  await fireEvent.click(ui.getByRole('button', { name: '加入对话' }))
  await waitFor(() => expect(ui.getByText('超过 10 MiB')).toBeTruthy())
  // 图没上去就什么都不发：发出那句没有图的说明，芝士只能猜。
  expect(ui.emitted().locate).toBeUndefined()
})
it('will not send a marked-up image until the note says what to change', async () => {
  const ui = mount()
  await paint(ui)
  await draw(ui)
  const send = await ui.findByRole('button', { name: '加入对话' })
  expect(send.hasAttribute('disabled')).toBe(true)
  await fireEvent.update(ui.getByPlaceholderText('说一句要改什么，回车发送'), '这块留白收紧一点')
  expect(send.hasAttribute('disabled')).toBe(false)
})
// ---- 滚轮 ----
// 面板里图片嵌在一段要滚的正文中间，滚轮得先把那段滚下去；只有图铺满整个屏幕时
// 才把滚轮让给缩放，否则图片一占满面板就再也滚不动了。
it('leaves the wheel to the page in the panel', async () => {
  const ui = mount()
  await paint(ui)
  const pane = ui.container.querySelector('.design-image__pane') as HTMLElement
  const event = new WheelEvent('wheel', { deltaY: -100, cancelable: true })
  pane.dispatchEvent(event)
  expect(event.defaultPrevented).toBe(false)
  expect(ui.getByLabelText('显示比例').textContent).toContain('50%')
})
it('gives the wheel to zoom when the image is the whole screen', async () => {
  const ui = render(DesignImage, {
    props: { src: 'blob:design-1', alt: 'design.png', identity: 'room:v', zoomOnWheel: true },
    global: { plugins: [createVuetify()] },
  })
  await paint(ui)
  const pane = ui.container.querySelector('.design-image__pane') as HTMLElement
  const event = new WheelEvent('wheel', { deltaY: -100, cancelable: true })
  pane.dispatchEvent(event)
  expect(event.defaultPrevented).toBe(true)
})

it('draws a mark on the image for a region comment, and opening the panel follows a click', async () => {
  // 评论栏关着也画得出来：线程列表是 `drawsMarks` 让读的。点一下就把那一串打开。
  threadsApi.listFileThreads.mockResolvedValue({
    data: [
      {
        comment: {
          id: 'c1',
          author: 'li',
          content: '这块不对',
          anchor_quote: null,
          anchor: {
            kind: 'image-region',
            path: 'design.png',
            source: 'committed',
            version,
            task_id: null,
            x: 200,
            y: 200,
            width: 100,
            height: 100,
            natural_width: 1000,
            natural_height: 500,
          },
          created_at: '',
        },
        revision: 1,
        state: 'open',
        replies: [],
      },
    ],
    total: 1,
  })
  const ui = mount()
  await paint(ui)
  const dot = await waitFor(() => {
    const found = ui.container.querySelector<HTMLElement>('.design-image__mark')
    expect(found).toBeTruthy()
    return found!
  })
  expect(dot.dataset.commentMark).toBe('c1')
  // 原图 (200,200) × 0.5 = 屏上 (100,100)。
  expect(dot.style.left).toBe('100px')
  expect(dot.style.top).toBe('100px')
})

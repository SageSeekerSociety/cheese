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
import PanelPreview from './PanelPreview.vue'

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
vi.mock('./preview/RoomOutputs.vue', () => ({ default: { template: '<div />' } }))

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
  return render(PanelPreview, {
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
  await fireEvent.click(ui.getByRole('button', { name: '选择图片区域' }))
  // The layer has only just mounted; a pointer in that same millisecond is dropped by Vue as an
  // event older than its listener (see nextMillisecond). No real hand is that fast.
  await nextMillisecond()
  const overlay = ui.getByRole('group', { name: '选择图片区域' })
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
  const input = ui.getByPlaceholderText('说明要改什么')
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
  expect(ui.queryByPlaceholderText('说明要改什么')).toBeNull()
})
it.each([null, 'bbbbbbbbbbbbbbbb'])(
  'keeps the image readable but cannot send regions for metadata version %s',
  async (fingerprint) => {
    vi.mocked(api.readPreviewFile).mockResolvedValue(file(fingerprint))
    const ui = mount()
    await paint(ui)
    expect(ui.getByRole('img')).toBeTruthy()
    expect(ui.getByRole('button', { name: '选择图片区域' }).hasAttribute('disabled')).toBe(true)
    expect(ui.emitted().locate).toBeUndefined()
  }
)
it('does not promote unverified bytes when source fingerprinting is unavailable', async () => {
  vi.stubGlobal('crypto', {})
  const ui = mount()
  await paint(ui)
  expect(ui.getByRole('button', { name: '选择图片区域' }).hasAttribute('disabled')).toBe(true)
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
    await fireEvent.update(ui.getByPlaceholderText('说明要改什么'), '旧图的说明')
    vi.mocked(api.readPreviewFile).mockResolvedValue({ ...file(nextVersion), source })
    vi.mocked(api.previewFileBytes).mockReturnValue(new Promise(() => {}))
    await ui.rerender({ ...panelProps, refreshTick: 1 })
    await waitFor(() => expect(api.previewFileBytes).toHaveBeenCalledTimes(2))
    expect(ui.queryByPlaceholderText('说明要改什么')).toBeNull()
    expect(ui.emitted().locate).toBeUndefined()
  }
)
it('cancels the region note without posting a message', async () => {
  const ui = mount()
  await paint(ui)
  await select(ui)
  const input = ui.getByPlaceholderText('说明要改什么')
  await fireEvent.update(input, '未发送的说明')
  await fireEvent.keyDown(input, { key: 'Escape' })
  expect(ui.queryByPlaceholderText('说明要改什么')).toBeNull()
  expect(ui.container.querySelector('.design-image__selection')).toBeNull()
  expect(ui.emitted().locate).toBeUndefined()
})
it('does not send on composition Enter and sends once on an explicit committed Enter', async () => {
  const ui = mount()
  await paint(ui)
  await select(ui)
  const input = ui.getByPlaceholderText('说明要改什么')
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
  const [topic, file, origin] = vi.mocked(api.uploadAttachment).mock.calls[0]
  expect(topic).toBe('room')
  expect((file as File).name).toBe('design-annotated.png')
  // 房间附件而不是资料库：这张合成图是一次性的话，不是这个项目要留的文件。
  expect(origin).toBe('clipboard')
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

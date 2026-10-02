import { createHash, webcrypto } from 'node:crypto'

import type { FileContent } from '../../cx_types'

import { createVuetify } from 'vuetify'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import * as api from '../../api'
import { setLocale } from '../../i18n'

import PanelPreview from './PanelPreview.vue'

vi.mock('../../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api')>()
  return { ...actual, readPreviewFile: vi.fn(), previewFileBytes: vi.fn(), attachmentImageUrl: vi.fn() }
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
  const overlay = ui.getByRole('group', { name: '选择图片区域' })
  overlay.setPointerCapture = vi.fn()
  await fireEvent.pointerDown(overlay, { button: 0, pointerId: 7, clientX: 60, clientY: 70 })
  await fireEvent.pointerUp(overlay, { pointerId: 7, clientX: 160, clientY: 120 })
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
  const message = (ui.emitted().locate as [string][] | undefined)?.[0]?.[0] ?? ''
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

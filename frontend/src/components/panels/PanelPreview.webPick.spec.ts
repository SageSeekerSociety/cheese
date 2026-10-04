/**
 * 网页预览上的「圈选」：房间 HTML 走注入的桥，应用走宿主自己盖的一层。
 *
 * 这一条守的是「指着网页上一处说一句话」这条路：开关只给网页预览，点中的那一处要
 * 连同选择器、文字和位置一起变成结构化引用，没有桥的应用上框出来的那一块要带上
 * 页面地址和几何。
 */
import type { ComponentPublicInstance } from 'vue'
import type { PreviewFrame } from '../../composables/usePreviewFrames'
import type { DocumentIdentity } from '../../lib/documentBytes'
import type { SubmitPreviewQuestion } from '../../lib/previewQuestion'

import { defineComponent, h, nextTick } from 'vue'
import { createVuetify } from 'vuetify'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import PanelPreviewView from './PanelPreviewView.vue'

import { setLocale } from '@/i18n'

vi.mock('./preview/RevisionList.vue', () => ({ default: { template: '<div />' } }))
vi.mock('./preview/RoomOutputs.vue', () => ({ default: { template: '<div />' } }))

type View = ComponentPublicInstance & { handlePick: (pick: unknown) => void }

const identity: DocumentIdentity = {
  topicId: 'room',
  path: 'report.html',
  taskId: null,
  source: 'committed',
  version: 'v7',
}

function frame(overrides: Partial<PreviewFrame> = {}): PreviewFrame {
  return {
    id: 1,
    name: 'frame',
    url: 'https://content.example/report.html',
    label: 'report.html',
    mime: 'text/html',
    version: 'v7',
    live: false,
    posted: true,
    runtime: 'ready',
    ...overrides,
  }
}

function baseProps(submitQuestion: SubmitPreviewQuestion | undefined, overrides: Record<string, unknown>) {
  return {
    topicId: 'room',
    projectId: 'project',
    path: null,
    frameName: 'frame',
    displayedFrame: frame(),
    loading: false,
    refreshing: false,
    previewFile: {
      path: 'report.html',
      content: null,
      version: 'v7',
      bytes: 8,
      binary: false,
      too_large: false,
      source: 'committed' as const,
    },
    previewMime: 'text/html',
    previewNamed: true,
    previewUrl: 'https://content.example/report.html',
    previewAppNote: '',
    previewTunnelUp: false,
    previewNamedPath: 'report.html',
    previewError: null,
    previewReadError: null,
    documentSuffix: 'html',
    documentType: null,
    documentName: 'report.html',
    isImageArtifact: false,
    downloadError: '',
    docBytes: null,
    docIdentity: identity,
    docSnapshot: null,
    docLoading: false,
    docError: '',
    docRendererMissing: false,
    submitQuestion,
    ...overrides,
  }
}

/** 包一层，好拿到 `PanelPreview` 平时用的那个 `ref`（`defineExpose` 出来的 handlePick）。 */
function mount(submitQuestion?: SubmitPreviewQuestion, overrides: Record<string, unknown> = {}) {
  let view: View | null = null
  const modes: boolean[] = []
  const locates: unknown[] = []
  const props = baseProps(submitQuestion, overrides)
  const Host = defineComponent({
    setup() {
      return () =>
        h(PanelPreviewView, {
          ...props,
          'onPick-mode': (on: boolean) => modes.push(on),
          onLocate: (payload: unknown) => locates.push(payload),
          ref(v: unknown) {
            view = v as View | null
          },
        })
    },
  })
  const ui = render(Host, {
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
  return Object.assign(ui, { view: () => view, modes, locates })
}

beforeEach(() => setLocale('zh-CN'))
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

it('the toggle only appears for web previews and hands the switch to the frame', async () => {
  const ui = mount()
  const toggle = ui.getByTestId('preview-pick')
  expect(toggle.getAttribute('aria-pressed')).toBe('false')
  await fireEvent.click(toggle)
  expect(ui.modes).toEqual([true])
  expect(toggle.getAttribute('aria-pressed')).toBe('true')
  await fireEvent.click(toggle)
  expect(ui.modes).toEqual([true, false])
})

it('an element pick from the frame becomes a structured web-element quote', async () => {
  const submit = vi.fn().mockReturnValue(true)
  const ui = mount(submit)
  const pick = {
    selection: false,
    selector: 'body > main > p:nth-of-type(2)',
    tag: 'p',
    text: '这一句说错了',
    prefix: '',
    suffix: '',
    rect: { x: 12, y: 40, w: 300, h: 24 },
    viewport: { w: 1024, h: 768 },
  }
  expect(ui.view()).toBeTruthy()
  expect(typeof ui.view()!.handlePick).toBe('function')
  ui.view()!.handlePick(pick)
  await nextTick()
  await fireEvent.update(ui.getByPlaceholderText('说明要改什么'), '这里改一下')
  await fireEvent.click(ui.getByText('发送'))
  expect(submit).toHaveBeenCalledTimes(1)
  expect(submit.mock.calls[0]![0].quotedContext).toEqual({
    kind: 'web-element',
    selector: 'body > main > p:nth-of-type(2)',
    tag: 'p',
    text: '这一句说错了',
    rect: { x: 12, y: 40, w: 300, h: 24 },
    viewport: { w: 1024, h: 768 },
    path: 'report.html',
    source: 'committed',
    version: 'v7',
    task_id: null,
  })
})

it('a selection from the frame stays a web-text quote with the words on either side', async () => {
  const submit = vi.fn().mockReturnValue(true)
  const ui = mount(submit)
  ui.view()!.handlePick({
    selection: true,
    selector: 'body > main',
    tag: 'main',
    text: '只选中这一句',
    prefix: '退避',
    suffix: '，超过就报错',
    rect: { x: 1, y: 2, w: 30, h: 10 },
    viewport: { w: 800, h: 600 },
  })
  await nextTick()
  await fireEvent.update(ui.getByPlaceholderText('说明要改什么'), '这句不对')
  await fireEvent.click(ui.getByText('发送'))
  expect(submit.mock.calls[0]![0].quotedContext).toMatchObject({
    kind: 'web-text',
    prefix: '退避',
    suffix: '，超过就报错',
  })
})

it('an app without the bridge gets an overlay and quotes the boxed region by url', async () => {
  const submit = vi.fn().mockReturnValue(true)
  const app = frame({ live: true, label: 'dashboard', url: 'https://app.tunnel.example/dashboard' })
  const ui = mount(submit, {
    displayedFrame: app,
    frames: [app],
    previewUrl: 'https://app.tunnel.example/dashboard',
    previewMime: '',
    documentSuffix: '',
  })
  await fireEvent.click(ui.getByTestId('preview-pick'))
  const overlay = ui.getByTestId('preview-region-pick')
  const frames = ui.container.querySelector('.preview-frames') as HTMLElement
  frames.getBoundingClientRect = () =>
    ({ left: 10, top: 20, width: 300, height: 200, right: 310, bottom: 220 }) as DOMRect
  overlay.setPointerCapture = vi.fn()
  // 拖出一个 100×100 的框，落在 iframe 里的 (40, 30) 处。
  await fireEvent.pointerDown(overlay, { button: 0, pointerId: 7, clientX: 50, clientY: 50 })
  await fireEvent.pointerMove(overlay, { pointerId: 7, clientX: 150, clientY: 150 })
  await fireEvent.pointerUp(overlay, { pointerId: 7, clientX: 150, clientY: 150 })
  await waitFor(() => expect(ui.queryByPlaceholderText('说明要改什么')).toBeTruthy())
  await fireEvent.update(ui.getByPlaceholderText('说明要改什么'), '这块空白太大')
  await fireEvent.click(ui.getByText('发送'))
  expect(submit.mock.calls[0]![0].quotedContext).toEqual({
    kind: 'web-region',
    url: 'https://app.tunnel.example/dashboard',
    rect: { x: 40, y: 30, w: 100, h: 100 },
    viewport: { w: 300, h: 200 },
  })
})

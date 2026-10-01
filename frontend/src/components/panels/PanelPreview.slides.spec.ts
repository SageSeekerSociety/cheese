import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import PanelPreviewView from './PanelPreviewView.vue'

import { setLocale } from '@/i18n'

const text = 'Whole page original PDF text '.repeat(30)
const context = { topicId: 'room', path: 'deck.pptx', source: 'committed' as const, taskId: 'task', version: 'v7' }
vi.mock('./preview/PreviewSlides.vue', () => ({
  default: {
    props: ['data', 'context'],
    emits: ['quote', 'pageContext'],
    template: `<div data-testid="slides"><button @click="$emit('quote', {text: '${'q'.repeat(250)}', page: 2})">quote</button><button @click="$emit('pageContext', {text: '${'Whole page original PDF text '.repeat(30)}', page: 2, scope: 'page', context})">page</button></div>`,
  },
}))
vi.mock('./preview/PreviewPages.vue', () => ({ default: { template: '<div data-testid="pages" />' } }))
vi.mock('./preview/RevisionList.vue', () => ({ default: { template: '<div />' } }))
vi.mock('./preview/RoomOutputs.vue', () => ({ default: { template: '<div />' } }))
const docBytes = new ArrayBuffer(8)
const props = {
  topicId: 'room',
  projectId: 'project',
  path: 'deck.pptx',
  frameName: 'frame',
  loading: false,
  refreshing: false,
  previewFile: {
    path: 'deck.pptx',
    content: null,
    version: 'v7',
    bytes: 8,
    binary: true,
    too_large: false,
    source: context.source,
  },
  previewMime: '',
  previewNamed: true,
  previewUrl: null,
  previewAppNote: '',
  previewTunnelUp: false,
  previewNamedPath: 'deck.pptx',
  previewError: null,
  previewReadError: null,
  documentSuffix: 'pptx',
  documentType: { view: 'pages' as const, label: 'slides', icon: 'mdi-file-powerpoint-outline' },
  documentName: 'deck.pptx',
  isImageArtifact: false,
  downloadError: '',
  docBytes,
  docIdentity: context,
  docSnapshot: { bytes: docBytes, identity: context, sourceVersion: context.version },
  docLoading: false,
  docError: '',
  docRendererMissing: false,
  slideContext: context,
}
beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)
function mount() {
  return render(PanelPreviewView, {
    props,
    global: {
      stubs: {
        VBtn: { template: '<button><slot /></button>' },
        VIcon: true,
        VSpacer: true,
        VAlert: true,
        VDialog: true,
      },
    },
  })
}
it('mounts the slides branch and keeps quote normalization capped at 200 characters', async () => {
  const ui = mount()
  expect(ui.queryByTestId('pages')).toBeNull()
  await fireEvent.click(ui.getByText('quote'))
  await fireEvent.update(ui.getByPlaceholderText('说明要改什么'), 'fix it')
  await fireEvent.click(ui.getByText('发送'))
  const payload: unknown = ui.emitted().locate?.[0]
  if (!Array.isArray(payload) || typeof payload[0] !== 'string')
    throw new Error('Expected locate to emit a string message')
  const message = payload[0]
  expect(message).toContain('q'.repeat(200))
  expect(message).not.toContain('q'.repeat(201))
  await fireEvent.click(ui.getByRole('button', { name: /^下载$/ }))
  expect(ui.emitted().download).toHaveLength(1)
})
it('sends whole-page PDF context with complete text and the verified file identity', async () => {
  const ui = mount()
  await fireEvent.click(ui.getByText('page'))
  await fireEvent.update(ui.getByPlaceholderText('说明要改什么'), 'explain this page')
  await fireEvent.click(ui.getByText('发送'))
  const payload: unknown = ui.emitted().locate?.[0]
  if (!Array.isArray(payload) || typeof payload[0] !== 'string')
    throw new Error('Expected locate to emit a string message')
  const message = payload[0]
  expect(message).toContain(text)
  expect(message).toContain('整页 PDF 文字上下文')
  expect(message).toContain('topic=room source=committed task=task version=v7')
})
it('retires the locator when bytes or source identity change and routes PDF to the existing reader', async () => {
  const ui = mount()
  await fireEvent.click(ui.getByText('page'))
  const nextProps = {
    ...props,
    slideContext: { ...context, version: 'v8' },
    docBytes: new ArrayBuffer(16),
  }
  await ui.rerender(nextProps)
  expect(ui.queryByPlaceholderText('说明要改什么')).toBeNull()
  expect(ui.emitted().locate).toBeUndefined()
  await ui.rerender({ ...nextProps, documentSuffix: 'pdf' })
  expect(ui.getByTestId('pages')).toBeTruthy()
  expect(ui.queryByTestId('slides')).toBeNull()
})

it('does not open whole-page context when displayed source bytes disagree with metadata', async () => {
  const ui = mount()
  await ui.rerender({ ...props, docSnapshot: { ...props.docSnapshot, sourceVersion: 'v8' } })
  await fireEvent.click(ui.getByText('page'))
  expect(ui.queryByPlaceholderText('说明要改什么')).toBeNull()
  expect(ui.emitted().locate).toBeUndefined()
  await fireEvent.click(ui.getByText('quote'))
  expect(ui.getByPlaceholderText('说明要改什么')).toBeTruthy()
})

it('rechecks current metadata at send time without relying on locator retirement', async () => {
  const ui = mount()
  await fireEvent.click(ui.getByText('page'))
  await fireEvent.update(ui.getByPlaceholderText('说明要改什么'), 'explain this page')
  await ui.rerender({ ...props, previewFile: { ...props.previewFile, version: 'v8' } })
  await fireEvent.click(ui.getByText('发送'))
  expect(ui.emitted().locate).toBeUndefined()
})

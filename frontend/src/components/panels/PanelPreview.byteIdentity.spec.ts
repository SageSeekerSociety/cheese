import type { FileContent } from '../../cx_types'
import type { PdfPreview } from '../../lib/previewPdf'

import { createVuetify } from 'vuetify'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import * as api from '../../api'

import PanelPreviewHost from '@/components/work/PanelPreviewHost.vue'
import { setLocale } from '@/i18n'

const text = 'Whole page text from displayed PDF '.repeat(30)
vi.mock('../../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api')>()
  return { ...actual, readPreviewFile: vi.fn(), previewDocumentPdfSnapshot: vi.fn() }
})
vi.mock('./preview/PreviewSlides.vue', () => ({
  default: {
    props: ['data', 'context'],
    emits: ['pageContext'],
    template: `<button :disabled="!context" @click="$emit('pageContext', { text: '${'Whole page text from displayed PDF '.repeat(30)}', page: 2, scope: 'page', context })">page</button>`,
  },
}))
vi.mock('./preview/RevisionList.vue', () => ({ default: { template: '<div />' } }))
beforeEach(() => {
  vi.resetAllMocks()
  setLocale('zh-CN')
})
afterEach(cleanup)

const panelProps = { topicId: 'room', projectId: 'project', path: 'deck.pptx', active: true, refreshTick: 0 }
const global = {
  plugins: [createVuetify()],
  stubs: {
    VBtn: { template: '<button><slot /></button>' },
    VIcon: true,
    VSpacer: true,
    VAlert: true,
    VDialog: true,
  },
}
function file(version: string): FileContent {
  return { path: 'deck.pptx', content: null, version, bytes: 8, binary: true, too_large: false, source: 'committed' }
}
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => {
    resolve = done
  })
  return { promise, resolve }
}

it('wires the real PanelPreview reader to whole-page context only after matching source bytes', async () => {
  const version = 'aaaaaaaaaaaaaaaa'
  vi.mocked(api.readPreviewFile).mockResolvedValue(file(version))
  vi.mocked(api.previewDocumentPdfSnapshot).mockResolvedValue({ bytes: new ArrayBuffer(8), sourceVersion: version })
  const submitQuestion = vi.fn().mockReturnValue(true)
  const ui = render(PanelPreviewHost, { props: { ...panelProps, submitQuestion }, global })
  await waitFor(() => expect(ui.getByText('page').hasAttribute('disabled')).toBe(false))
  expect(api.previewDocumentPdfSnapshot).toHaveBeenCalledWith('room', 'deck.pptx', null, 'committed')
  await fireEvent.click(ui.getByText('page'))
  await fireEvent.update(ui.getByPlaceholderText('说明要改什么'), 'explain')
  await fireEvent.click(ui.getByText('发送'))
  const request = submitQuestion.mock.calls[0]?.[0]
  expect(request).toMatchObject({ topicId: 'room', intent: 'ask-agent' })
  expect(request.content).toBe('explain')
  expect(request.quotedContext).toMatchObject({
    text,
    source: 'committed',
    task_id: null,
    version,
  })
})

it.each([null, 'bbbbbbbbbbbbbbbb'])(
  'does not enable whole-page context for a missing or mismatched source fingerprint: %s',
  async (sourceVersion) => {
    vi.mocked(api.readPreviewFile).mockResolvedValue(file('aaaaaaaaaaaaaaaa'))
    vi.mocked(api.previewDocumentPdfSnapshot).mockResolvedValue({ bytes: new ArrayBuffer(8), sourceVersion })
    const submitQuestion = vi.fn().mockReturnValue(true)
    const ui = render(PanelPreviewHost, { props: { ...panelProps, submitQuestion }, global })
    await waitFor(() => expect(api.previewDocumentPdfSnapshot).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(ui.getByText('page').hasAttribute('disabled')).toBe(true))
    await fireEvent.click(ui.getByText('page'))
    expect(ui.queryByPlaceholderText('说明要改什么')).toBeNull()
    expect(submitQuestion).not.toHaveBeenCalled()
  }
)

it('does not bind a late A conversion to B metadata in the real owning container', async () => {
  const old = deferred<PdfPreview>()
  const fresh = deferred<PdfPreview>()
  vi.mocked(api.readPreviewFile)
    .mockResolvedValueOnce(file('aaaaaaaaaaaaaaaa'))
    .mockResolvedValueOnce(file('bbbbbbbbbbbbbbbb'))
  vi.mocked(api.previewDocumentPdfSnapshot).mockReturnValueOnce(old.promise).mockReturnValueOnce(fresh.promise)
  const submitQuestion = vi.fn().mockReturnValue(true)
  const ui = render(PanelPreviewHost, { props: { ...panelProps, submitQuestion }, global })
  await waitFor(() => expect(api.previewDocumentPdfSnapshot).toHaveBeenCalledTimes(1))
  await ui.rerender({ ...panelProps, refreshTick: 1, submitQuestion })
  await waitFor(() => expect(api.previewDocumentPdfSnapshot).toHaveBeenCalledTimes(2))
  fresh.resolve({ bytes: new ArrayBuffer(16), sourceVersion: 'bbbbbbbbbbbbbbbb' })
  await waitFor(() => expect(ui.getByText('page').hasAttribute('disabled')).toBe(false))
  old.resolve({ bytes: new ArrayBuffer(8), sourceVersion: 'aaaaaaaaaaaaaaaa' })
  await fireEvent.click(ui.getByText('page'))
  await fireEvent.update(ui.getByPlaceholderText('说明要改什么'), 'explain')
  await fireEvent.click(ui.getByText('发送'))
  const request = submitQuestion.mock.calls[0]?.[0]
  expect(request).toMatchObject({ topicId: 'room', intent: 'ask-agent' })
  expect(request.quotedContext.version).toBe('bbbbbbbbbbbbbbbb')
  expect(request.quotedContext.version).not.toBe('aaaaaaaaaaaaaaaa')
})
